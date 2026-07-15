#!/usr/bin/env python3
"""Apply a supplement constraints patch (add/replace) into constraints.json.

把 constraint-supplementer 产出的 constraints_patch.json(op=add/replace +
proposed + match_expr + basis)确定性合并进 constraints.json:剥离 patch 层
字段,只保留 InterParamConstraint 五字段(expr_type/expr/relation_params/
src_text/origin),标 origin="supplement"。合并后重跑 normalize_constraints.py
+ validate_artifacts.py constraints,任一失败退出非 0,主协调器据此阻断、
不进 GENERATE。

本脚本只做确定性合并,不做任何业务推理(解读自然语言补充文件是
constraint-supplementer 的职责)。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
VALID_OPS = {"add_constraint", "replace_constraint"}
# InterParamConstraint 必填三字段;src_text/origin 由本脚本填,不取自 patch
INTER_REQUIRED_FIELDS = ("expr_type", "expr", "relation_params")
# patch 层"展开到所有平台"的哨兵值:合并器据此把条目写入 cip 中每个平台桶,
# 不产生 common 桶(跨平台约束直接落各平台,不依赖生成器侧 common 合并)。
ALL_PLATFORMS_SENTINEL = "all"
# 已废弃:旧 patch用 "common" 表示跨平台,会建出 common 桶;现改用 ALL_PLATFORMS_SENTINEL。
DEPRECATED_COMMON = "common"


def _normalize_cip(value: Any) -> dict[str, list[dict]]:
    """constraints_in_parameters 规范化为 dict[平台 -> list]。

    - dict 形态(按平台分组):原样保留键值,值非 list 者置空。
    - list 形态:不允许(无法确定平台以展开 'all');抛错,应由 EXTRACT 产出按
      平台分组的 dict。
    - 缺失/非法:初始化为空 dict。
    """
    if isinstance(value, list):
        raise ValueError(
            "constraints_in_parameters 为 list 形态,无法确定平台以展开 'all';"
            "应由 EXTRACT 产出按平台分组的 dict"
        )
    if isinstance(value, dict):
        result: dict[str, list[dict]] = {}
        for platform, items in value.items():
            result[platform] = list(items) if isinstance(items, list) else []
        return result
    return {}


def _resolve_platforms(cip: dict[str, list[dict]], platform: str) -> list[str]:
    """解析 patch.target_platform 到具体平台 key 列表。

    - ALL_PLATFORMS_SENTINEL("all"):展开到 cip 中所有平台桶(排除 "common"
      生成器桶);无平台桶则报错。
    - 具体平台名:返回单元素列表;桶存在性由 add(setdefault)/replace(搜索)
      各自处理,与原行为一致。
    - DEPRECATED_COMMON("common"):已废弃,报错引导改用 "all"(避免建出 common 桶)。
    """
    if platform == DEPRECATED_COMMON:
        raise ValueError(
            "target_platform='common' 已废弃:跨平台关系请用 'all',"
            "合并器展开到各平台桶,不产生 common 桶"
        )
    if platform == ALL_PLATFORMS_SENTINEL:
        targets = [k for k in cip.keys() if k != DEPRECATED_COMMON]
        if not targets:
            raise ValueError(
                "target_platform='all' 但 constraints_in_parameters 无平台桶可展开"
            )
        return targets
    return [platform]


def _build_entry(patch: dict, origin: str) -> dict:
    """从 patch.proposed 构造 InterParamConstraint 条目,填 src_text/origin。

    只取三必填字段,src_text 取自 patch.basis,origin 由本脚本指定;
    patch 的 op/match_expr/proposed/basis 等套壳字段一律不进 constraints.json
    (InterParamConstraint 为 extra:forbid)。
    """
    proposed = patch.get("proposed")
    if not isinstance(proposed, dict):
        raise ValueError(f"patch 项缺少 proposed 对象: {patch!r}")
    entry: dict[str, Any] = {}
    for field in INTER_REQUIRED_FIELDS:
        if field not in proposed:
            raise ValueError(
                f"patch 项 proposed 缺少必填字段 {field!r}: {patch!r}"
            )
        entry[field] = proposed[field]
    entry["src_text"] = patch.get("basis", "") or ""
    entry["origin"] = origin
    return entry


def apply_patch(
    constraints: dict, patch_items: list[dict], origin: str = "supplement"
) -> tuple[dict, list[str]]:
    """原地合并 patch 到 constraints['constraints_in_parameters']。返回 (constraints, 日志)。

    target_platform="all" 时把条目展开写入 cip 中每个平台桶(不产生 common 桶);
    具体平台名时只写该平台。每平台独立构造条目,避免跨平台共享引用。
    """
    cip = _normalize_cip(constraints.get("constraints_in_parameters"))
    log: list[str] = []
    for idx, patch in enumerate(patch_items):
        if not isinstance(patch, dict):
            raise ValueError(f"patch[{idx}] 不是对象: {patch!r}")
        op = patch.get("op")
        if op not in VALID_OPS:
            raise ValueError(f"patch[{idx}].op 非法 {op!r},应为 {sorted(VALID_OPS)}")
        platform = patch.get("target_platform")
        if not platform:
            raise ValueError(f"patch[{idx}].target_platform 缺失")
        targets = _resolve_platforms(cip, platform)
        scope = platform if len(targets) == 1 else f"{platform}->{len(targets)}平台"

        if op == "add_constraint":
            new_expr = ""
            for tgt in targets:
                entry = _build_entry(patch, origin)
                cip.setdefault(tgt, []).append(entry)
                new_expr = entry["expr"]
            log.append(f"add@{scope}: {new_expr}")
        else:  # replace_constraint
            match_expr = patch.get("match_expr")
            if not match_expr:
                raise ValueError(
                    f"patch[{idx}] op=replace_constraint 缺少 match_expr"
                )
            new_expr = ""
            for tgt in targets:
                bucket = cip.setdefault(tgt, [])
                target_idx = None
                for i, item in enumerate(bucket):
                    if item.get("expr") == match_expr:
                        target_idx = i
                        break
                if target_idx is None:
                    raise ValueError(
                        f"patch[{idx}] op=replace_constraint 在平台 {tgt!r} "
                        f"未找到 expr==match_expr 的条目: {match_expr!r}"
                    )
                entry = _build_entry(patch, origin)
                bucket[target_idx] = entry
                new_expr = entry["expr"]
            log.append(f"replace@{scope}: {match_expr} -> {new_expr}")

    constraints["constraints_in_parameters"] = cip
    return constraints, log


def _run(cmd: list[str]) -> tuple[int, str]:
    """同步子进程,捕获输出。失败时由调用方把输出打到 stderr(让 normalize/validate
    的错误可见),成功时不打印,保证本脚本 stdout 始终只有单一 JSON 摘要。"""
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "把 constraints_patch.json(op=add/replace)确定性合并进 "
            "constraints.json,标 origin=supplement,剥离 patch 层字段,"
            "随后重跑 normalize_constraints + validate_artifacts constraints。"
        )
    )
    parser.add_argument("constraints", help="constraints.json 路径(原地合并改写)")
    parser.add_argument("patch", help="constraints_patch.json 路径")
    parser.add_argument(
        "--origin", default="supplement",
        help="合并条目的 origin 标记(默认 supplement)",
    )
    parser.add_argument(
        "--skip-revalidate", action="store_true",
        help="仅合并,不重跑 normalize+validate(调试用)",
    )
    args = parser.parse_args()

    constraints_path = Path(args.constraints).resolve()
    patch_path = Path(args.patch).resolve()
    if not constraints_path.is_file():
        print(json.dumps({"ok": False, "code": "CONSTRAINTS_NOT_FOUND",
                          "constraints": str(constraints_path)}, ensure_ascii=False))
        return 2
    if not patch_path.is_file():
        print(json.dumps({"ok": False, "code": "PATCH_NOT_FOUND",
                          "patch": str(patch_path)}, ensure_ascii=False))
        return 2

    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    if not isinstance(constraints, dict):
        print(json.dumps({"ok": False, "code": "CONSTRAINTS_NOT_OBJECT",
                          "constraints": str(constraints_path)}, ensure_ascii=False))
        return 2
    patch_items = json.loads(patch_path.read_text(encoding="utf-8"))
    if not isinstance(patch_items, list):
        print(json.dumps({"ok": False, "code": "PATCH_NOT_LIST",
                          "patch": str(patch_path)}, ensure_ascii=False))
        return 2
    if not patch_items:
        print(json.dumps({"ok": True, "applied": 0, "skipped": "empty patch",
                          "constraints": str(constraints_path)}, ensure_ascii=False))
        return 0

    # 备份留痕:保留 EXTRACT 原始产物。每轮覆盖(第 2 轮 re-EXTRACT 后干净底,
    # 备份本轮 EXTRACT 产物供回溯;跨轮不叠加,保证 re-supplement 幂等)。
    backup = constraints_path.with_name(constraints_path.stem + ".json.pre_supplement")
    shutil.copy2(constraints_path, backup)

    try:
        constraints, log = apply_patch(constraints, patch_items, origin=args.origin)
    except ValueError as exc:
        print(json.dumps({"ok": False, "code": "PATCH_APPLY_FAILED",
                          "error": str(exc)}, ensure_ascii=False))
        return 2

    constraints_path.write_text(
        json.dumps(constraints, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if not args.skip_revalidate:
        norm_rc, norm_out = _run([sys.executable, str(SCRIPTS / "normalize_constraints.py"),
                                  str(constraints_path)])
        if norm_rc != 0:
            sys.stderr.write(norm_out)
            print(json.dumps({"ok": False, "code": "NORMALIZE_FAILED",
                              "constraints": str(constraints_path)}, ensure_ascii=False))
            return 2
        val_rc, val_out = _run([sys.executable, str(SCRIPTS / "validate_artifacts.py"),
                                "constraints", str(constraints_path)])
        if val_rc != 0:
            sys.stderr.write(val_out)
            print(json.dumps({"ok": False, "code": "VALIDATE_FAILED",
                              "constraints": str(constraints_path)}, ensure_ascii=False))
            return 2

    print(json.dumps({"ok": True, "applied": len(log), "ops": log,
                      "constraints": str(constraints_path),
                      "patch": str(patch_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
