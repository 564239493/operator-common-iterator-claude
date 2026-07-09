#!/usr/bin/env python3
"""机械应用 constraints_patch.json 到 constraints.json。

source-analyst 产 constraints_patch.json（带 doc_quote / source_authoritative 依据的
增删改建议）；本脚本确定性机械应用，设 `origin`，重跑 `OperatorRule` 校验，写回。
通过确定性脚本应用而非让 source-analyst 直接写 constraints，保持"constraint-extractor
是唯一 LLM 写手"的边界（source-analyst 只产证据 + patch 建议）。

支持三类 op：
  - add_constraint：尽量用 enum expr_type（生成器 enforce 的 10 种之一）；非 enum 为
    声明式约束，生成器不 enforce，脚本 warn 不阻断。
  - narrow_param_range：修改 inputs/outputs[param] 的 allowed_range_value，使生成器实际收紧。
    basis_type=source_authoritative 时把 allowed_range_value 规整为 ValueWithSrcText
    形式并写入 src_text=basis，让"按源码改"在 constraints.json 留痕。
  - replace_constraint：按 target_platform + match_expr(旧 expr 精确匹配) 替换已有约束的
    expr/expr_type/relation_params，设 src_text=basis、origin=source_analysis。用于
    "已有表达式错误，按源码修正"——add_constraint 只会追加旧错 expr 仍在，故需 replace。

校验失败（OperatorRule 不通过）→ 不写输出、返回结构化错误，由主协调器回滚到旧路径。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.generators.common_model_definition import OperatorRule, InterConstraintsRuleType

ENUM_EXPR_TYPES = {t.value for t in InterConstraintsRuleType}


def _origin_for(item: dict) -> str:
    if item.get("origin"):
        return item["origin"]
    return "source_analysis" if item.get("basis_type") == "source_authoritative" else "doc"


def _with_src_text(new_range, src_text: str):
    """确保 allowed_range_value 以 ValueWithSrcText 形式携带 src_text。

    - 已是 dict（ValueWithSrcText 形式）：src_text 为空则补 basis，否则保留；
    - 非 dict（裸 list/str）：包成 {"value": new_range, "src_text": src_text}。
    """
    if isinstance(new_range, dict):
        if not new_range.get("src_text"):
            return {**new_range, "src_text": src_text}
        return new_range
    return {"value": new_range, "src_text": src_text}


def _apply_add_constraint(constraints: dict, item: dict, warnings: list) -> None:
    cip = constraints.setdefault("constraints_in_parameters", {})
    if isinstance(cip, list):
        # 罕见：约束为无平台 list。转为 dict 以按平台追加。
        cip = {"common": cip}
        constraints["constraints_in_parameters"] = cip
    platform = item.get("target_platform", "common")
    bucket = cip.setdefault(platform, [])
    proposed = item.get("proposed", {})
    constraint = {
        "expr_type": proposed.get("expr_type", ""),
        "expr": proposed.get("expr", ""),
        "relation_params": proposed.get("relation_params", []),
        "src_text": proposed.get("src_text") or item.get("basis", ""),
        "origin": _origin_for(item),
    }
    bucket.append(constraint)
    if constraint["expr_type"] not in ENUM_EXPR_TYPES:
        warnings.append(
            f"add_constraint expr_type '{constraint['expr_type']}' 非 enum 型，生成器不 "
            f"enforce（声明式约束，仅作记录）：{constraint['expr']}"
        )


def _apply_narrow_param_range(constraints: dict, item: dict, errors: list) -> None:
    platform = item.get("target_platform")
    param = item.get("target_param")
    if not param:
        errors.append("narrow_param_range 缺 target_param")
        return
    new_range = item.get("proposed", {}).get("allowed_range_value")
    if new_range is None:
        errors.append(f"narrow_param_range({param}) 缺 proposed.allowed_range_value")
        return
    # 源码驱动的范围修改必须在 constraints.json 留 src_text 痕迹（ValueWithSrcText 形式），
    # 否则改了范围却看不出"按源码改"，违背 source-analyst 的审计要求。
    basis = item.get("basis", "")
    if item.get("basis_type") == "source_authoritative" and basis:
        new_range = _with_src_text(new_range, basis)
    modified = False
    for section in ("inputs", "outputs"):
        sec = constraints.get(section)
        if not isinstance(sec, dict) or param not in sec:
            continue
        spec = sec[param]
        # 平台嵌套 {platform: ParamAttributes}：值为 dict 且全部值为 dict
        if isinstance(spec, dict) and spec and all(isinstance(v, dict) for v in spec.values()):
            for plat, attr in spec.items():
                if platform is None or plat == platform:
                    attr["allowed_range_value"] = new_range
                    modified = True
        elif isinstance(spec, dict):
            # 扁平 ParamAttributes
            spec["allowed_range_value"] = new_range
            modified = True
    if not modified:
        errors.append(f"narrow_param_range({param}) 未匹配到参数（platform={platform}）")


def _apply_replace_constraint(constraints: dict, item: dict, errors: list) -> None:
    """按 target_platform + match_expr 精确匹配旧约束，替换 expr/expr_type/relation_params。

    InterParamConstraint 无 id 字段，故按旧 expr 字符串精确匹配。命中后设
    src_text=basis、origin=source_analysis（source_authoritative），使"按源码修正错误
    表达式"在 constraints.json 可审计。未命中则报错不写（避免静默失败）。
    """
    platform = item.get("target_platform", "common")
    match_expr = item.get("match_expr")
    proposed = item.get("proposed", {})
    new_expr = proposed.get("expr")
    if not match_expr:
        errors.append("replace_constraint 缺 match_expr（要替换的旧 expr）")
        return
    if not new_expr:
        errors.append("replace_constraint 缺 proposed.expr（新 expr）")
        return
    cip = constraints.get("constraints_in_parameters")
    bucket = None
    if isinstance(cip, dict):
        bucket = cip.get(platform)
    elif isinstance(cip, list) and platform == "common":
        bucket = cip
    if not isinstance(bucket, list):
        errors.append(f"replace_constraint 未找到平台 {platform} 的约束列表")
        return
    replaced = False
    for c in bucket:
        if isinstance(c, dict) and c.get("expr") == match_expr:
            c["expr_type"] = proposed.get("expr_type", c.get("expr_type", ""))
            c["expr"] = new_expr
            c["relation_params"] = proposed.get(
                "relation_params", c.get("relation_params", [])
            )
            c["src_text"] = proposed.get("src_text") or item.get("basis", "")
            c["origin"] = _origin_for(item)
            replaced = True
            break
    if not replaced:
        errors.append(
            f"replace_constraint 未匹配到 expr={match_expr!r}（platform={platform}）"
        )


def apply_patch(constraints: dict, patch: list) -> tuple[dict, list, list]:
    """返回 (patched, warnings, errors)。errors 非空表示应用期问题（不应写回）。"""
    patched = copy.deepcopy(constraints)
    warnings: list = []
    errors: list = []
    for item in patch:
        if not isinstance(item, dict):
            errors.append(f"patch 项非对象：{item!r}")
            continue
        op = item.get("op")
        if op == "add_constraint":
            _apply_add_constraint(patched, item, warnings)
        elif op == "narrow_param_range":
            _apply_narrow_param_range(patched, item, errors)
        elif op == "replace_constraint":
            _apply_replace_constraint(patched, item, errors)
        else:
            errors.append(f"未知 op：{op}")
    return patched, warnings, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="机械应用 constraints_patch.json")
    parser.add_argument("--constraints", required=True, help="输入 constraints.json")
    parser.add_argument("--patch", required=True, help="constraints_patch.json")
    parser.add_argument("--output", required=True, help="输出 patched constraints.json")
    args = parser.parse_args()

    constraints = json.loads(Path(args.constraints).read_text(encoding="utf-8"))
    patch = json.loads(Path(args.patch).read_text(encoding="utf-8"))
    if not isinstance(patch, list):
        print(json.dumps({"ok": False, "error": "patch 必须是数组"}, ensure_ascii=False))
        return 1

    patched, warnings, errors = apply_patch(constraints, patch)
    if errors:
        print(json.dumps({"ok": False, "applied": False, "errors": errors,
                          "warnings": warnings}, ensure_ascii=False))
        return 1

    # 校验：OperatorRule 必须通过；不通过则回滚（不写输出）
    try:
        OperatorRule(**patched)
    except Exception as exc:
        print(json.dumps({"ok": False, "applied": False, "rolled_back": True,
                          "error": f"OperatorRule validation failed: {exc}",
                          "warnings": warnings}, ensure_ascii=False))
        return 1

    Path(args.output).write_text(json.dumps(patched, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "applied": True, "output": args.output,
                      "ops": len(patch), "warnings": warnings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
