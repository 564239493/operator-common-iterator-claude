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
            "mode": args.mode,
            "server_config": str(server_config) if server_config else "",
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
