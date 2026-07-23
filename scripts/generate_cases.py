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


def _apply_runtime_ranksize_pin(constraints: dict, server_config_path: Path) -> int | None:
    """Pin ``rankSize`` to ``len(fusion_devices)`` in-memory for fusion ops.

    Fusion distributed operators require ``rankSize == world_size`` (the
    actual communication-domain card count): the operator's alltoAll exchanges
    over ``rankSize`` cards and the CPU golden builds
    ``A=[BS/world_size, H*world_size]`` to matmul with ``x2=[H*rankSize, N]``.
    The doc-derived constraints list the operator's *supported* rankSize values
    per platform (e.g. A2: {2,4,8}), but the harness only provisions
    ``len(fusion_devices)`` cards — so unpinned cases with rankSize != world_size
    are unrunnable (CPU golden matmul shape mismatch; NPU alltoAll over a
    too-large group).

    This narrows the rankSize candidate source (``allowed_range_value.value``)
    and the mirrored ``self_value_enum`` constraint to ``[world_size]``, in
    memory only — the on-disk ``constraints.json`` stays doc-faithful. Returns
    the pinned ``world_size``, or ``None`` when no pinning applies (no
    ``rankSize`` param, or no fusion_devices in the config).
    """
    from scripts.runtime_config import resolve_fusion_world_size

    world_size = resolve_fusion_world_size(server_config_path)
    if world_size is None:
        return None
    # Parameter definitions live under the top-level "inputs" key (keyed by
    # param name -> platform -> spec); fall back to "parameters" in case a
    # future schema renames it.
    parameters = constraints.get("inputs")
    if not isinstance(parameters, dict):
        parameters = constraints.get("parameters")
    if not isinstance(parameters, dict) or not isinstance(parameters.get("rankSize"), dict):
        return None
    rank_param = parameters["rankSize"]
    # Narrow the per-platform candidate enum (the source the generator draws
    # rankSize from) so it has no choice but world_size.
    for spec in rank_param.values():
        if isinstance(spec, dict) and isinstance(spec.get("allowed_range_value"), dict):
            spec["allowed_range_value"]["value"] = [world_size]
    # Keep the mirrored self_value_enum constraint consistent, in case the
    # solver also consults it as a candidate source.
    cip = constraints.get("constraints_in_parameters")
    if isinstance(cip, dict):
        for bucket in cip.values():
            if not isinstance(bucket, list):
                continue
            for entry in bucket:
                if not isinstance(entry, dict):
                    continue
                if (
                    entry.get("expr_type") == "self_value_enum"
                    and entry.get("relation_params") == ["rankSize"]
                    and "rankSize.range_value" in entry.get("expr", "")
                ):
                    entry["expr"] = f"rankSize.range_value in [{world_size}]"
    return world_size


def _log_ranksize_distribution(
    per_platform_paths: dict[str, Path], pinned_ws: int | None
) -> None:
    """Best-effort: log the rankSize value distribution per generated file."""
    if pinned_ws is None:
        return
    from collections import Counter

    for platform, path in per_platform_paths.items():
        try:
            cases = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(cases, list):
            continue
        counts: Counter = Counter()
        for case in cases:
            if not isinstance(case, dict):
                continue
            flat: list = []
            for it in case.get("inputs", []):
                flat.extend(it if isinstance(it, list) else [it])
            for it in flat:
                if isinstance(it, dict) and it.get("name") == "rankSize":
                    counts[it.get("range_values")] += 1
        logger.info(
            "rankSize distribution [%s]: %s (expected 100%% = %d)",
            platform,
            dict(counts),
            pinned_ws,
        )


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
    parser.add_argument(
        "--server-config",
        default=str(ROOT / "servers.json"),
        help="servers.json 路径, 用于读 fusion_devices 把 rankSize 钉为 "
        "len(fusion_devices) (fusion 分布式算子 rankSize 必须等于实际卡数)。"
        "默认项目根 servers.json; 文件不存在或无 fusion server 时不钉值, "
        "保持文档 enum。仅读非秘密字段 fusion_devices。",
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

    # Pin rankSize to len(fusion_devices) for fusion distributed operators so
    # generated cases are runnable on the provisioned hardware (rankSize must
    # equal the actual card count). In-memory only; the on-disk
    # constraints.json stays doc-faithful. No-op when the operator has no
    # rankSize param or servers.json has no fusion_devices. See plan
    # golden-crafting-chipmunk.md.
    server_config_path = Path(args.server_config)
    pinned_ws = _apply_runtime_ranksize_pin(constraints, server_config_path)
    if pinned_ws is not None:
        logger.info(
            "rankSize pinned to %d (= len(fusion_devices) from %s) for fusion run",
            pinned_ws,
            server_config_path,
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

    _log_ranksize_distribution(per_platform_paths, pinned_ws)

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
