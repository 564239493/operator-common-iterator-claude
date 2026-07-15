#!/usr/bin/env python3
"""Create a run directory and initial workflow state."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from runtime_config import (
    ROOT,
    config_error_payload,
    find_latest_operator_prompt,
    resolve_input_path,
    validate_server_config,
)
from select_prompt import assemble


# L1 算子名 stem 全树闭包：路径含以下任一段的命中视为噪声跳过。
CLOSURE_NOISE_PARTS = frozenset({
    "tests", "ut", "examples", "binary_config", "tbe",
    "cann-ops-competitions", "cann-outreach", "data_preprocess",
    "dvpp", "dump_statistics", ".git",
})
# 闭包匹配的源码后缀（与 source_exts 一致）。
CLOSURE_EXTS = ("cc", "cpp", "h", "hpp", "c")


def _closure_by_stem(
    src_root: Path, dest: Path, stems: list[str], seen: set[Path]
) -> int:
    """L1 算子名 stem 全树闭包：在 src_root 下按算子名找同名实现文件。

    SEED 的固定位置 glob 只抓算子目录的 op_host/op_api，漏掉 canndev 主力
    实现（如 canndev/ops/built-in/op_tiling/runtime/trans_data.cc，与算子
    目录同 stem）。此处对每个 stem（算子目录名）× 后缀 rglob 全树，命中且
    不在噪声路径、未已复制的，按 rel-to-src_root 路径复制进快照。

    严格 stem 匹配（不做前缀），避免 trans_data* 噪声；带后缀变体
    (_def/_tiling_arch35) 是已知盲区。闭包范围 = src_root：传树根跨子树含
    canndev，传算子目录仅该目录。基础算子（canndev 无同名文件）→ 空集跳过。
    """
    if not stems:
        return 0
    added = 0
    for stem in stems:
        if not stem:
            continue
        for ext in CLOSURE_EXTS:
            for hit in src_root.rglob(f"{stem}.{ext}"):
                if not hit.is_file():
                    continue
                if any(part in CLOSURE_NOISE_PARTS for part in hit.parts):
                    continue
                if hit in seen:
                    continue
                seen.add(hit)
                rel = hit.relative_to(src_root)
                dst = dest / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(hit, dst)
                added += 1
    return added


def _snapshot_operator_source(
    src_root: Path, dest: Path, aclnn_name: str = ""
) -> dict:
    """只读复制算子源码关键文件到快照目录，保持相对 src_root 的路径结构。

    **树根 + 算子名**（常见场景）走升级版 :func:`collect_operator_source.collect`：
    SEED + stem/后缀变体闭包 + canndev 多层 + **include 不动点闭包 + L0 反查**，
    覆盖跨目录 L0 实现（如 ``npu_format_cast`` → ``transdata``/``contiguous``/
    ``reshape``/``transpose``/``view_copy``），并落 ``manifest.json`` +
    ``closure_report.md``。L0 反查按声明头 stem 反查实现 ``.cpp``；当某 ``l0op``
    函数与声明头不同 stem（如 ``ViewCopy`` 声明在 ``contiguous.h``、实现在
    ``view_copy.cpp``）时会漏，需 ``collect-operator-source`` skill 手动补
    ``--extra-stem``（默认流程不传，接受该已知盲区）。

    **单算子目录**（src_root 自身即算子目录，SEED 直命中源码）或 **collect
    失败/异常**时，回退旧路径：SEED 固定位置 glob + L1 算子名 stem 全树闭包
    （``_closure_by_stem``，仅按算子目录名 rglob 同名 ``.cc/.cpp/.h``，不解析
    include）。带后缀变体 ``_def/_tiling_arch35`` 与跨目录依赖在此路径仍漏，
    由 source-analyst 标 ``missing_evidence`` 进 ``uncertain-doc.md``。

    --src 既可指单个算子目录（根下直接有 op_host/op_api/docs/config），也可指
    整棵源码树根（如 operators-src）。对后者，非递归 SEED globs 只会命中顶层
    docs/，漏掉埋在 <repo>/<class>/<op>/op_host/op_api 下的真实实现；故当直接
    扫描命中 0 个源码文件时，用 aclnn_name 调 locate_operator_source 在 src_root
    下定位算子目录，对定位结果套同样 SEED（仅扫算子目录，不带顶层 docs 噪声）。
    定位仍失败则回退扫 src_root（保持旧行为，返回 source_files=0 由 caller 提示）。

    返回 {"files": <总文件数>, "source_files": <源码文件数>,
           "closure_files": <L1 stem 闭包新增数>}。
    """
    # 源码目录 × 源码后缀，单一定义供 globs 与 source_files 判定共用。
    # CANN 真实算子主用 .cpp/.h；asc-devkit 自定义算子与 canndev 模板用 .cc；
    # .hpp/.c 兜底。docs/*.md 与 config/**/*.json 单列（config 含 tiling 配置等）。
    source_dirs = ("op_host", "op_host/op_api", "op_api")
    source_exts = ("cpp", "h", "cc", "hpp", "c")
    source_ext_dots = {f".{e}" for e in source_exts}
    seed_globs = [f"{d}/*.{e}" for d in source_dirs for e in source_exts]
    seed_globs += ["docs/*.md", "docs/**/*.md", "config/**/*.json"]

    def _collect(root: Path) -> list[Path]:
        hits: list[Path] = []
        for pattern in seed_globs:
            for src in root.glob(pattern):
                if src.is_file():
                    hits.append(src)
        return hits

    # 先探测 src_root 是否本身就是算子目录（直接套 globs 能命中源码文件）。
    direct = _collect(src_root)
    direct_has_source = any(p.suffix.lower() in source_ext_dots for p in direct)

    # 树根 + 有算子名：走升级版 collect_operator_source（include 不动点闭包 +
    # L0 反查），出全量快照 + manifest + closure_report，覆盖跨目录 L0 实现。
    # collect 内部做 locate+SEED+stem闭包+include闭包+L0反查；locate 失败返回
    # ok=False（未写任何文件），异常也被兜住——两种情况都回退下方旧 SEED 路径。
    if not direct_has_source and aclnn_name:
        try:
            import collect_operator_source as _col

            result = _col.collect(
                aclnn_name, src_root, dest,
                with_tbe_py=False, with_tests=False,
                with_external_stub=False,
            )
            if result.get("ok"):
                cstats = result.get("stats", {})
                closure = (
                    cstats.get("stem_closure", 0)
                    + cstats.get("include_closure", 0)
                    + cstats.get("l0_backref", 0)
                    + cstats.get("external_stub", 0)
                )
                source_files = sum(
                    1 for p in dest.rglob("*")
                    if p.is_file() and p.suffix.lower() in source_ext_dots
                )
                return {
                    "files": cstats.get("total_copied", source_files),
                    "source_files": source_files,
                    "closure_files": closure,
                    "collector": "collect_operator_source",
                    "layer_counts": result.get("layer_counts", {}),
                }
        except Exception:
            # collect 异常（import 失败/解析异常等）：dest 可能已被部分写入，
            # 回退旧 SEED 会 copy2 覆盖同名文件，剩余旧文件保留——保持鲁棒不中断。
            pass

    targets: list[Path]
    if direct_has_source:
        targets = [src_root]
    else:
        # src_root 是树根或无效目录：用算子名定位算子目录；定位失败回退 src_root。
        targets = []
        if aclnn_name:
            import locate_operator_source as _loc

            for d in _loc.locate(aclnn_name, src_root).get("operator_dirs", []):
                p = Path(d)
                if p.is_dir():
                    targets.append(p)
        if not targets:
            targets = [src_root]

    seen: set[Path] = set()
    files = 0
    source_files = 0
    for target in targets:
        for pattern in seed_globs:
            for src in target.glob(pattern):
                if not src.is_file() or src in seen:
                    continue
                seen.add(src)
                rel = src.relative_to(src_root)
                dst = dest / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                files += 1
                if src.suffix.lower() in source_ext_dots:
                    source_files += 1
    # L1 算子名 stem 全树闭包：补 SEED 抓不到的 canndev 主力实现
    #（如 trans_data.cc 的 OP_TILING_CHECK）。
    stems = [t.name for t in targets]
    closure_files = _closure_by_stem(src_root, dest, stems, seen)
    files += closure_files
    source_files += closure_files  # 闭包命中的全是源码后缀
    return {"files": files, "source_files": source_files,
            "closure_files": closure_files}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "创建 run 目录并初始化 workflow 状态。算子文档路径既可作为"
            "位置参数传入，也可通过 --doc 指定。"
        )
    )
    # 算子文档路径两种写法都接受:
    #   1) 位置参数:        init_run.py <doc>
    #   2) 显式 --doc flag:  init_run.py --doc <doc>
    # 显式 --doc 优先, 位置参数作为回退; 都不给则报错。
    parser.add_argument(
        "doc_pos",
        nargs="?",
        default=None,
        help="算子文档路径 (与 --doc 等价, 留空则必须用 --doc)",
    )
    parser.add_argument(
        "--doc",
        dest="doc",
        default=None,
        help="算子文档路径 (项目内或外部绝对路径)",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help=(
            "约束提取提示词路径；省略时自动选择 "
            "prompts/operator_constraints_extract_vN.md 中数值版本最大的文件"
        ),
    )
    parser.add_argument(
        "--supplement-constraints",
        dest="supplement_constraints",
        default=None,
        help=(
            "补充约束 Markdown 路径（项目内或外部）；省略则跳过约束补充阶段。"
            "EXTRACT 产出 constraints.json 后据此做关系补充（add/replace）。"
        ),
    )
    parser.add_argument(
        "--src",
        dest="src",
        default=None,
        help=(
            "算子源码目录绝对路径（可选，项目内或外部）。提供时把关键源码只读"
            "复制到 run/inputs/src_snapshot/，供 source-analyst 做交叉校验与"
            "失败反向推导。省略则跳过源码分析，退回纯文档驱动。算子源码典型"
            "结构: op_host/<op>_def.cpp|_tiling*.cpp|op_api/aclnn_<op>.cpp|docs/*.md。"
        ),
    )
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--case-count", type=int, default=10)
    parser.add_argument("--mode", choices=("mock", "real"), default="real")
    parser.add_argument("--server-config", default="servers.json")
    args = parser.parse_args()

    if args.doc is None:
        args.doc = args.doc_pos
    if not args.doc:
        parser.error(
            "必须提供算子文档路径: 位置参数 doc_pos 或 --doc 二选一。"
        )

    doc = resolve_input_path(args.doc)
    if args.prompt:
        prompt = resolve_input_path(args.prompt)
        explicit_prompt = True
    else:
        prompt = find_latest_operator_prompt()
        explicit_prompt = False
    if not doc.is_file():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "OPERATOR_DOC_NOT_FOUND",
                "message": "算子文档不存在，请提供绝对路径、项目相对路径或包含 .. 的相对路径。",
                "operator_doc": str(doc),
            },
            ensure_ascii=False,
        ))
        return 2
    if prompt is None or not prompt.is_file():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "PROMPT_NOT_FOUND",
                "message": (
                    "约束提取提示词不存在。请通过 --prompt 指定文件，或在 prompts "
                    "目录提供 operator_constraints_extract_vN.md。"
                ),
                "prompt": str(prompt) if prompt else "",
            },
            ensure_ascii=False,
        ))
        return 2
    supplement_path = (
        resolve_input_path(args.supplement_constraints)
        if args.supplement_constraints
        else None
    )
    if supplement_path is not None and not supplement_path.is_file():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "SUPPLEMENT_NOT_FOUND",
                "message": (
                    "补充约束文件不存在。请提供绝对路径、项目相对路径或包含 .. 的"
                    "相对路径，或省略 --supplement-constraints 以跳过约束补充阶段。"
                ),
                "supplement_constraints": str(supplement_path),
            },
            ensure_ascii=False,
        ))
        return 2
    src_path = resolve_input_path(args.src) if args.src else None
    if src_path is not None and not src_path.is_dir():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "OPERATOR_SRC_NOT_FOUND",
                "message": (
                    "算子源码目录不存在。请提供有效的 --src 绝对路径，"
                    "或省略以跳过源码分析退回纯文档驱动。"
                ),
                "operator_src_source": str(args.src),
            },
            ensure_ascii=False,
        ))
        return 2
    if args.max_iterations < 1 or args.case_count < 1:
        raise SystemExit("max-iterations and case-count must be positive")

    server_config: Path | None = None
    if args.mode == "real":
        server_config, config_errors = validate_server_config(args.server_config)
        if config_errors:
            print(json.dumps(
                config_error_payload(server_config, config_errors),
                ensure_ascii=False,
            ))
            return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_id = f"{doc.stem}-{stamp}"
    run_dir = ROOT / "runs" / run_id
    input_dir = run_dir / "inputs"
    (run_dir / "iter_001").mkdir(parents=True, exist_ok=False)
    input_dir.mkdir(parents=True, exist_ok=False)

    # External inputs are read-only. All Agents consume immutable snapshots
    # inside this project so they never edit the user's original document.
    doc_snapshot = input_dir / doc.name
    prompt_snapshot = input_dir / "prompt_v1.md"
    supplement_snapshot = input_dir / "supplement_constraints.md"
    shutil.copy2(doc, doc_snapshot)
    if supplement_path is not None:
        # --supplement-constraints：外部补充约束文件只读复制到 inputs/，
        # EXTRACT 后据此对 constraints.json 做关系补充（add/replace）。
        shutil.copy2(supplement_path, supplement_snapshot)
    # --src：外部算子源码目录只读快照到 inputs/src_snapshot/，供
    # source-analyst 做交叉校验与失败反向推导。空则跳过源码分析。
    # 传树根时 _snapshot_operator_source 会用 doc.stem 自动定位算子目录。
    operator_src_source = ""
    operator_src_snapshot = ""
    operator_src_stats = {"files": 0, "source_files": 0, "closure_files": 0}
    if src_path is not None:
        src_snapshot = input_dir / "src_snapshot"
        src_snapshot.mkdir(parents=True, exist_ok=False)
        operator_src_stats = _snapshot_operator_source(
            src_path, src_snapshot, aclnn_name=doc.stem
        )
        operator_src_source = str(src_path)
        operator_src_snapshot = str(src_snapshot)
    if explicit_prompt:
        # --prompt 逃生口：原样复制指定文件，不装配模块（用于固定版本/外部提示词）
        shutil.copy2(prompt, prompt_snapshot)
        loaded_modules = []
    else:
        # 默认：按算子特征装配 base + 命中模块 -> prompt_snapshot
        loaded_modules = assemble(prompt, doc_snapshot, prompt_snapshot)

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "run_id": run_id,
        "operator_doc_source": str(doc),
        "operator_doc": str(doc_snapshot),
        "current_prompt_source": str(prompt),
        "current_prompt": str(prompt_snapshot),
        "current_prompt_modules": loaded_modules,
        "supplement_constraints_source": str(supplement_path) if supplement_path else "",
        "supplement_constraints": str(supplement_snapshot) if supplement_path else "",
        "operator_src_source": operator_src_source,
        "operator_src_snapshot": operator_src_snapshot,
        "mode": args.mode,
        "server_config": str(server_config) if server_config else "",
        "max_iterations": args.max_iterations,
        "case_count": args.case_count,
        "current_iteration": 1,
        "state": "PLAN",
        "history": [{"state": "PLAN", "at": now}],
        "created_at": now,
        "updated_at": now,
    }
    (run_dir / "run_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(
        {
            "ok": True,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "operator_doc_source": str(doc),
            "operator_doc_snapshot": str(doc_snapshot),
            "prompt_snapshot": str(prompt_snapshot),
            "prompt_modules": loaded_modules,
            "supplement_constraints_source": str(supplement_path) if supplement_path else "",
            "supplement_constraints_snapshot": str(supplement_snapshot) if supplement_path else "",
            "operator_src_source": operator_src_source,
            "operator_src_snapshot": operator_src_snapshot,
            "operator_src_stats": operator_src_stats,
            "operator_src_warning": (
                "快照命中 0 个源码文件（--src 可能是树根且未定位到算子目录）；"
                "source-analyst 将无源码可析，建议用 locate_operator_source.py "
                "确认算子目录后重传 --src。"
                if src_path is not None and operator_src_stats["source_files"] == 0
                else ""
            ),
            "mode": args.mode,
            "server_config": str(server_config) if server_config else "",
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
