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
    resolve_input_path,
    validate_server_config,
)


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
    parser.add_argument("--prompt", default="prompts/operator_constraints_extract_v1.md")
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
    prompt = resolve_input_path(args.prompt)
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
    if not prompt.is_file():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "PROMPT_NOT_FOUND",
                "message": "约束提取提示词不存在。",
                "prompt": str(prompt),
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
    shutil.copy2(prompt, prompt_snapshot)

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "run_id": run_id,
        "operator_doc_source": str(doc),
        "operator_doc": str(doc_snapshot),
        "current_prompt_source": str(prompt),
        "current_prompt": str(prompt_snapshot),
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
            "mode": args.mode,
            "server_config": str(server_config) if server_config else "",
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
