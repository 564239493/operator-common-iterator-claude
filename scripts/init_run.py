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


def _snapshot_operator_source(src_root: Path, dest: Path) -> int:
    """只读复制算子源码关键文件到快照目录,保持相对路径结构。

    覆盖: op_host/ 下的 .cpp/.h/.hpp/.json(含 _def/_infershape/_tiling*、
    op_api/aclnn_*、config/<platform>/binary.json 及 arch32/arch35 子目录),
    以及 docs/aclnn*.md。source-analyst 只读此快照,不触外部源码树,
    与"项目内快照为唯一真相源"原则一致。
    """
    patterns = (
        "op_host/**/*.cpp",
        "op_host/**/*.h",
        "op_host/**/*.hpp",
        "op_host/**/*.json",
        "docs/aclnn*.md",
    )
    count = 0
    for pat in patterns:
        for src in src_root.glob(pat):
            if not src.is_file():
                continue
            rel = src.relative_to(src_root)
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            count += 1
    return count


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
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--case-count", type=int, default=10)
    parser.add_argument("--mode", choices=("mock", "real"), default="real")
    parser.add_argument("--server-config", default="servers.json")
    parser.add_argument(
        "--source-root",
        dest="source_root",
        default=None,
        help=(
            "算子源码目录绝对路径(可选)。提供时把关键源码只读复制到 run/inputs/src_snapshot/,"
            "供 source-analyst 在每轮 EXTRACT 后校验约束类型/范围/表达式。不提供或为空则跳过"
            "源码分析,退回纯文档驱动。算子源码典型结构: op_host/<op>_def.cpp|_tiling*.cpp|"
            "op_api/aclnn_<op>.cpp|config/<platform>/<op>_binary.json 与 docs/aclnn<OpName>.md。"
        ),
    )
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
    shutil.copy2(doc, doc_snapshot)
    if explicit_prompt:
        # --prompt 逃生口：原样复制指定文件，不装配模块（用于固定版本/外部提示词）
        shutil.copy2(prompt, prompt_snapshot)
        loaded_modules = []
    else:
        # 默认：按算子特征装配 base + 命中模块 -> prompt_snapshot
        loaded_modules = assemble(prompt, doc_snapshot, prompt_snapshot)

    # Optional read-only snapshot of operator source code. Empty when
    # --source-root is not provided or empty; in that case downstream
    # source-analyst is skipped and the flow falls back to pure document-driven
    # mode (EXTRACT->GENERATE->EXECUTE->GATE, no source artifacts).
    operator_src_source = ""
    operator_src_snapshot = ""
    if args.source_root:
        src_root = resolve_input_path(args.source_root)
        if not src_root.is_dir():
            print(json.dumps(
                {
                    "ok": False,
                    "requires_user_action": True,
                    "code": "OPERATOR_SRC_NOT_FOUND",
                    "message": (
                        "算子源码目录不存在。请提供有效的 --source-root 绝对路径,"
                        "或省略以跳过源码分析退回纯文档驱动。"
                    ),
                    "operator_src_source": str(args.source_root),
                },
                ensure_ascii=False,
            ))
            return 2
        src_snapshot = input_dir / "src_snapshot"
        src_snapshot.mkdir(parents=True, exist_ok=False)
        _snapshot_operator_source(src_root, src_snapshot)
        operator_src_source = str(src_root)
        operator_src_snapshot = str(src_snapshot)

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "run_id": run_id,
        "operator_doc_source": str(doc),
        "operator_doc": str(doc_snapshot),
        "operator_src_source": operator_src_source,
        "operator_src_snapshot": operator_src_snapshot,
        "current_prompt_source": str(prompt),
        "current_prompt": str(prompt_snapshot),
        "current_prompt_modules": loaded_modules,
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
            "operator_src_source": operator_src_source,
            "operator_src_snapshot": operator_src_snapshot,
            "prompt_snapshot": str(prompt_snapshot),
            "prompt_modules": loaded_modules,
            "mode": args.mode,
            "server_config": str(server_config) if server_config else "",
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
