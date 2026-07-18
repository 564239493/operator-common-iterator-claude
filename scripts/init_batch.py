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
    find_latest_hs_prompt,
    find_latest_operator_prompt,
    resolve_input_path,
    validate_server_config,
)


def _document_family(path: Path) -> str:
    head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    is_torch_npu = (
        "torch_npu" in head
        or "torch\\_npu" in head
        or "torch.npu." in head
        or "torch_npu" in path.name
    )
    return "hs" if is_torch_npu else "aclnn"


def _is_catalog_document(path: Path) -> bool:
    """Known torch_npu navigation files are not callable API documents."""
    return path.name in {"torch_npu.md", "torch_npu_list.md"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="扫描算子文档目录并初始化可恢复的串行执行批次。"
    )
    parser.add_argument("directory", help="算子文档目录（项目内或外部路径）")
    parser.add_argument("--glob", default="*.md", help="文档匹配模式，默认 *.md")
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    parser.add_argument(
        "--prompt",
        default=None,
        help=(
            "显式原样约束提取提示词路径；省略时每个文档按 family 选择并装配 "
            "operator_constraints_extract_vN.md 或 torch_npu_constraints_extract_vN.md"
        ),
    )
    parser.add_argument(
        "--supplement-constraints",
        dest="supplement_constraints",
        default=None,
        help=(
            "整批共享的补充约束 Markdown 路径（可选）；省略则各算子跳过补充阶段。"
            "内层 /iterate-operator 会将其透传给 init_run.py 复制为各 run 快照。"
        ),
    )
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--case-count", type=int, default=10)
    parser.add_argument(
        "--operator-family",
        choices=("auto", "aclnn", "hs", "torch_npu"),
        default="auto",
        help="整批 family；auto 允许目录内逐文档选择，torch_npu 是 hs 的别名。",
    )
    parser.add_argument(
        "--test-framework",
        choices=("auto", "atk", "ttk", "constraints"),
        default="auto",
        help="整批测试框架；auto 由每个文档选择 atk/ttk/constraints-only。",
    )
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
    if not directory.is_dir():
        return print_error(
            "OPERATOR_DIRECTORY_NOT_FOUND",
            "算子文档目录不存在。",
            directory=str(directory),
        )
    if not args.glob.strip():
        return print_error("INVALID_GLOB", "glob 匹配模式不能为空。")
    supplement_path = (
        resolve_input_path(args.supplement_constraints)
        if args.supplement_constraints
        else None
    )
    if supplement_path is not None and not supplement_path.is_file():
        return print_error(
            "SUPPLEMENT_NOT_FOUND",
            (
                "补充约束文件不存在。请提供绝对路径、项目相对路径或包含 .. 的"
                "相对路径，或省略 --supplement-constraints 以跳过约束补充阶段。"
            ),
            supplement_constraints=str(supplement_path),
        )
    if args.max_iterations < 1 or args.case_count < 1:
        return print_error(
            "INVALID_BATCH_ARGUMENT",
            "max-iterations 和 case-count 必须为正整数。",
        )

    server_config: Path | None = None
    if args.mode == "real" and args.test_framework != "constraints":
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
        matched_files = [path.resolve() for path in iterator if path.is_file()]
        skipped_catalogs = sorted(
            (path for path in matched_files if _is_catalog_document(path)),
            key=lambda path: str(path.relative_to(directory)).casefold(),
        )
        documents = sorted(
            (path for path in matched_files if not _is_catalog_document(path)),
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

    normalized_family = "hs" if args.operator_family == "torch_npu" else args.operator_family
    explicit_prompt = bool(args.prompt)
    prompt = resolve_input_path(args.prompt) if explicit_prompt else None
    prompt_sources: dict[str, str] = {}
    if explicit_prompt:
        if prompt is None or not prompt.is_file():
            return print_error(
                "PROMPT_NOT_FOUND",
                "显式指定的约束提取提示词不存在。",
                prompt=str(prompt) if prompt else "",
            )
    else:
        required_families = (
            {_document_family(path) for path in documents}
            if normalized_family == "auto"
            else {normalized_family}
        )
        selected = {
            "aclnn": find_latest_operator_prompt() if "aclnn" in required_families else None,
            "hs": find_latest_hs_prompt() if "hs" in required_families else None,
        }
        missing = sorted(name for name in required_families if selected.get(name) is None)
        if missing:
            return print_error(
                "PROMPT_NOT_FOUND",
                "缺少批次所需 family 的版本化约束提取提示词。",
                missing_families=missing,
            )
        prompt_sources = {
            name: str(path)
            for name, path in selected.items()
            if path is not None
        }

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
        # prompt 非空仅表示用户显式固定原样 prompt；自动模式由每个 init_run
        # 按 family 重新选择并装配，避免把 ACLNN prompt 透传给 torch_npu。
        "prompt": str(prompt) if prompt is not None else "",
        "prompt_explicit": explicit_prompt,
        "prompt_sources": prompt_sources,
        "operator_family": normalized_family,
        "test_framework": args.test_framework,
        "max_iterations": args.max_iterations,
        "case_count": args.case_count,
        "mode": args.mode,
        "server_config": str(server_config) if server_config else "",
        "supplement_constraints": str(supplement_path) if supplement_path else "",
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
            "prompt_mode": "explicit_raw" if explicit_prompt else "per_document_family_auto",
            "prompt_sources": prompt_sources,
            "skipped_catalog_documents": [str(path) for path in skipped_catalogs],
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
