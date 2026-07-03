#!/usr/bin/env python3
"""Create a resumable directory-level operator batch."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from batch_state import save_batch, utc_now
from runtime_config import (
    ROOT,
    config_error_payload,
    resolve_input_path,
    validate_server_config,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="扫描算子文档目录并初始化可恢复的串行执行批次。"
    )
    parser.add_argument("directory", help="算子文档目录（项目内或外部路径）")
    parser.add_argument("--glob", default="*.md", help="文档匹配模式，默认 *.md")
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    parser.add_argument("--prompt", default="prompts/operator_constraints_extract_v1.md")
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--case-count", type=int, default=10)
    parser.add_argument("--mode", choices=("mock", "real"), default="real")
    parser.add_argument("--server-config", default="servers.json")
    policy = parser.add_mutually_exclusive_group()
    policy.add_argument(
        "--continue-on-error",
        dest="continue_on_error",
        action="store_true",
        default=True,
        help="单个算子失败后继续，默认启用",
    )
    policy.add_argument(
        "--fail-fast",
        dest="continue_on_error",
        action="store_false",
        help="首个非 SUCCESS 终态后停止批次",
    )
    args = parser.parse_args()

    directory = resolve_input_path(args.directory)
    prompt = resolve_input_path(args.prompt)
    if not directory.is_dir():
        return print_error(
            "OPERATOR_DIRECTORY_NOT_FOUND",
            "算子文档目录不存在。",
            directory=str(directory),
        )
    if not args.glob.strip():
        return print_error("INVALID_GLOB", "glob 匹配模式不能为空。")
    if not prompt.is_file():
        return print_error(
            "PROMPT_NOT_FOUND",
            "约束提取提示词不存在。",
            prompt=str(prompt),
        )
    if args.max_iterations < 1 or args.case_count < 1:
        return print_error(
            "INVALID_BATCH_ARGUMENT",
            "max-iterations 和 case-count 必须为正整数。",
        )

    server_config: Path | None = None
    if args.mode == "real":
        server_config, config_errors = validate_server_config(args.server_config)
        if config_errors:
            print(json.dumps(
                config_error_payload(server_config, config_errors),
                ensure_ascii=False,
                indent=2,
            ))
            return 2

    try:
        iterator = (
            directory.rglob(args.glob)
            if args.recursive
            else directory.glob(args.glob)
        )
        documents = sorted(
            (path.resolve() for path in iterator if path.is_file()),
            key=lambda path: str(path.relative_to(directory)).casefold(),
        )
    except (OSError, ValueError) as exc:
        return print_error("INVALID_GLOB", f"无法使用该 glob 扫描目录: {exc}")
    if not documents:
        return print_error(
            "NO_OPERATOR_DOCUMENTS",
            "目录中没有匹配的算子文档。",
            directory=str(directory),
            glob=args.glob,
            recursive=args.recursive,
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    batch_id = f"{directory.name or 'operators'}-{stamp}"
    batch_dir = ROOT / "runs" / "batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    now = utc_now()
    batch = {
        "batch_id": batch_id,
        "source_directory": str(directory),
        "glob": args.glob,
        "recursive": args.recursive,
        "prompt": str(prompt),
        "max_iterations": args.max_iterations,
        "case_count": args.case_count,
        "mode": args.mode,
        "server_config": str(server_config) if server_config else "",
        "continue_on_error": args.continue_on_error,
        "state": "RUNNING",
        "current_index": None,
        "counts": {},
        "operators": [
            {
                "index": index,
                "relative_path": str(path.relative_to(directory)),
                "operator_doc_source": str(path),
                "status": "PENDING",
                "terminal_state": None,
                "run_id": None,
                "run_dir": None,
                "run_state": None,
                "message": "",
                "started_at": None,
                "completed_at": None,
            }
            for index, path in enumerate(documents, start=1)
        ],
        "history": [{"event": "BATCH_CREATED", "at": now}],
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    save_batch(batch_dir, batch)
    print(json.dumps(
        {
            "ok": True,
            "batch_id": batch_id,
            "batch_dir": str(batch_dir),
            "batch_state": str(batch_dir / "batch_state.json"),
            "batch_summary": str(batch_dir / "batch_summary.json"),
            "total": len(documents),
            "continue_on_error": args.continue_on_error,
            "documents": [str(path) for path in documents],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def print_error(code: str, message: str, **details: object) -> int:
    print(json.dumps(
        {
            "ok": False,
            "requires_user_action": True,
            "code": code,
            "message": message,
            **details,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
