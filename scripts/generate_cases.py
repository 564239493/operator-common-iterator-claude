#!/usr/bin/env python3
"""Deterministically call the retained business case generator.

CLI-only adaptation of the retained generation pipeline in
``agent/generators`` and the reference ``executer/generate_atk.py``.
No NZ or shape post-processing is
applied here — those constraints live in the upstream operator doc
extraction step (constraints.json + agent.generators.facade outputs match
operator_case_generator's ``single_operator_handle`` semantics).

Outputs:
- ``<output>``                       — overall cases path; its parent dir
  receives per-platform files (kept for backward CLI compatibility)
- ``<output_dir>/cases_<platform>.json`` — one JSON array per product_support
  entry, ``id`` is the integer the facade emits (per-platform 0, 1, 2, …)
- ``<output_dir>/generation_summary.json`` — per-platform counts + paths
- ``<iter_dir>/generation.log``       — when ``--iter-dir`` is passed, a
  timestamped log mirror of the run for diagnostics
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("generate_cases")


def _setup_iter_log(iter_dir: Path) -> Path | None:
    """Mirror the script's run into ``<iter_dir>/generation.log``."""
    try:
        iter_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    log_path = iter_dir / "generation.log"
    try:
        handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        return log_path
    except OSError as exc:
        print(
            f"[generate_cases] warning: cannot open {log_path}: {exc}",
            file=sys.stderr,
        )
        return None


def generate_platform_outputs(
    generator: Any,
    count: int,
    jsonl_save_path: Path,
    output_dir: Path,
) -> tuple[dict[str, Path], dict[str, int], dict[str, Path]]:
    """逐平台生成 JSONL，并在该平台结束时立即转换为正式 JSON 产物。"""
    from agent.generators.data_definition.param_models_def import RunPlatform

    platforms = generator.supported_platforms or [RunPlatform.DEFAULT_PLATFORM.value]
    per_platform_paths: dict[str, Path] = {}
    per_platform_counts: dict[str, int] = {}
    checkpoint_paths: dict[str, Path] = {}

    output_dir.mkdir(parents=True, exist_ok=True)
    for platform in platforms:
        sanitized = platform.replace("/", "_")
        checkpoint_dir = jsonl_save_path / sanitized
        checkpoint_file = checkpoint_dir / f"{generator.operator_name}.jsonl"
        converted_source = output_dir / f"{generator.operator_name}.json"
        target = output_dir / f"cases_{sanitized}.json"
        checkpoint_paths[platform] = checkpoint_file
        target.unlink(missing_ok=True)
        converted_source.unlink(missing_ok=True)

        try:
            generator.generate_for_platform(
                platform,
                count,
                jsonl_save_path=str(checkpoint_dir),
                json_save_path=str(output_dir),
            )
        finally:
            # facade 按 DataHandleUtil 的既有约定生成 <operator>.json；
            # 这里立即重命名为平台正式产物，且中断时也保留已转换结果。
            if converted_source.exists():
                converted_source.replace(target)
        if not target.exists():
            raise RuntimeError(
                f"Final case JSON was not produced for platform={platform}, "
                f"operator={generator.operator_name}"
            )
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"Converted case payload is not a list: {target}")
        per_platform_paths[platform] = target
        per_platform_counts[platform] = len(payload)

    return per_platform_paths, per_platform_counts, checkpoint_paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--constraints", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--jsonl-save-path",
        default=None,
        help="JSONL checkpoint 根目录；默认写入 <output-dir>/jsonl_checkpoints",
    )
    parser.add_argument(
        "--iter-dir",
        default=None,
        help="可选: 迭代目录 (如 runs/<run>/iter_001)。传入后, "
        "生成过程日志会写到 <iter-dir>/generation.log。",
    )
    args = parser.parse_args()
    if args.count < 1:
        raise SystemExit("count must be positive")

    iter_dir = Path(args.iter_dir) if args.iter_dir else None
    iter_log_path = _setup_iter_log(iter_dir) if iter_dir else None

    started = time.monotonic()
    logger.info(
        "start: constraints=%s output=%s count=%d seed=%d iter_dir=%s",
        args.constraints,
        args.output,
        args.count,
        args.seed,
        iter_dir or "(none)",
    )

    constraints_path = Path(args.constraints)
    output_path = Path(args.output)
    jsonl_save_path = (
        Path(args.jsonl_save_path)
        if args.jsonl_save_path
        else output_path.parent / "jsonl_checkpoints"
    )
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    from scripts.normalize_constraints import normalize_constraints

    normalized_count = normalize_constraints(constraints)
    if normalized_count:
        logger.info(
            "normalized %d type-dependent constraint attribute values",
            normalized_count,
        )
    logger.info(
        "constraints loaded: operator=%s, product_support=%d 项",
        constraints.get("operator_name", "<unknown>"),
        len(constraints.get("product_support", [])),
    )

    # Reference entry point — facade.TestCaseGenerator delegates to
    # ``single_operator_handle`` for each platform.
    from agent.generators.facade import TestCaseGenerator

    generator = TestCaseGenerator(constraints, seed=args.seed)
    output_dir = output_path.parent
    per_platform_paths, per_platform_counts, checkpoint_paths = generate_platform_outputs(
        generator,
        args.count,
        jsonl_save_path,
        output_dir,
    )

    summary = {
        "operator_name": generator.operator_name,
        "requested_per_platform": args.count,
        "platforms": per_platform_counts,
        "per_platform_files": {
            k: str(v) for k, v in per_platform_paths.items()
        },
        "jsonl_checkpoint_files": {
            platform: str(path) for platform, path in checkpoint_paths.items()
        },
        "jsonl_checkpoint_status": "converted_and_removed",
        "total": sum(per_platform_counts.values()),
        "seed": args.seed,
        "generator_version": (
            "agent.generators.facade.TestCaseGenerator -> "
            "agent.generators.operator_handle_main.single_operator_handle"
        ),
        "id_format": "platform 内 0 基整数 (per-platform 0,1,2,...)",
    }
    (output_dir / "generation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    elapsed = time.monotonic() - started
    logger.info(
        "done: %d cases across %d platforms in %.2fs -> %s",
        summary["total"],
        len(per_platform_paths),
        elapsed,
        output_dir,
    )
    for platform, path in per_platform_paths.items():
        logger.info(
            "  %s -> %s (%d cases)",
            platform,
            path,
            per_platform_counts[platform],
        )
    if iter_log_path is not None:
        logger.info("generation log: %s", iter_log_path)

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
