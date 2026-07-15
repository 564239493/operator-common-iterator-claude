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
        # The retained generator logs through module/root loggers. Mirror them
        # into the iteration artifact as well; the former logs/generate_case_*
        # file is no longer the authoritative log for this CLI pipeline.
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
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
        if not payload:
            raise RuntimeError(
                f"ZERO_CASES_GENERATED: platform={platform}, "
                f"operator={generator.operator_name}; inspect generation.log"
            )

    return per_platform_paths, per_platform_counts, checkpoint_paths


def generate_hs_scenario_outputs(
    constraints: dict[str, Any],
    count: int,
    seed: int,
    jsonl_save_path: Path,
    output_dir: Path,
) -> tuple[dict[str, Path], dict[str, int], dict[str, Path], dict[str, Any]]:
    """Generate HS cases scenario-by-scenario through the retained generator."""
    from agent.generators.data_definition.param_models_def import RunPlatform
    from agent.generators.facade import TestCaseGenerator
    from agent.hs.scenario_planner import plan_hs_scenarios, pin_scenario_constraints

    probe = TestCaseGenerator(constraints, seed=seed)
    platforms = probe.supported_platforms or [RunPlatform.DEFAULT_PLATFORM.value]
    operator_name = probe.operator_name
    per_platform_paths: dict[str, Path] = {}
    per_platform_counts: dict[str, int] = {}
    checkpoint_paths: dict[str, Path] = {}
    scenario_stats: dict[str, Any] = {}
    output_dir.mkdir(parents=True, exist_ok=True)

    for platform in platforms:
        sanitized = platform.replace("/", "_")
        target = output_dir / f"cases_{sanitized}.json"
        plans = plan_hs_scenarios(constraints, count, platform)
        combined: list[dict[str, Any]] = []
        platform_stats: list[dict[str, Any]] = []
        for scenario_index, scenario in enumerate(plans):
            if scenario.count < 1:
                continue
            scenario_constraints = pin_scenario_constraints(constraints, scenario)
            scenario_generator = TestCaseGenerator(
                scenario_constraints, seed=seed + scenario_index
            )
            scenario_root = jsonl_save_path / sanitized / scenario.name
            generated = scenario_generator.generate_for_platform(
                platform,
                scenario.count,
                jsonl_save_path=str(scenario_root),
                json_save_path=str(scenario_root),
            )
            payload = [case.model_dump() for case in generated]
            if len(payload) != scenario.count:
                raise RuntimeError(
                    "HS_SCENARIO_GENERATION_INCOMPLETE: "
                    f"platform={platform}, scenario={scenario.name}, "
                    f"requested={scenario.count}, generated={len(payload)}"
                )
            combined.extend(payload)
            platform_stats.append({
                "name": scenario.name,
                "requested": scenario.count,
                "generated": len(payload),
                "fixed_attrs": scenario.fixed_attrs,
            })
        if not combined:
            raise RuntimeError(
                f"ZERO_CASES_GENERATED: platform={platform}, operator={operator_name}"
            )
        for case_id, case in enumerate(combined):
            case["id"] = case_id
        target.write_text(
            json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        per_platform_paths[platform] = target
        per_platform_counts[platform] = len(combined)
        checkpoint_paths[platform] = jsonl_save_path / sanitized
        scenario_stats[platform] = platform_stats

    return per_platform_paths, per_platform_counts, checkpoint_paths, scenario_stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--constraints", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--test-framework", choices=("atk", "ttk"), default="atk",
        help="测试框架；默认 atk 保持 ACLNN 现有行为，海思 torch_npu 算子可选 ttk。",
    )
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
    if str(constraints.get("operator_name", "")).startswith("torch_npu."):
        from agent.hs.constraint_validation import validate_hs_constraints

        hs_constraint_errors = validate_hs_constraints(constraints)
        if hs_constraint_errors:
            raise SystemExit(
                "HS_CONSTRAINT_VALIDATION_FAILED: "
                + json.dumps(hs_constraint_errors, ensure_ascii=False)
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
    scenario_generation: dict[str, Any] = {}
    if args.test_framework == "ttk":
        from agent.hs import is_hs_operator

        if is_hs_operator(generator.operator_name):
            (
                per_platform_paths,
                per_platform_counts,
                checkpoint_paths,
                scenario_generation,
            ) = generate_hs_scenario_outputs(
                constraints, args.count, args.seed, jsonl_save_path, output_dir
            )
        else:
            per_platform_paths, per_platform_counts, checkpoint_paths = generate_platform_outputs(
                generator, args.count, jsonl_save_path, output_dir
            )
    else:
        per_platform_paths, per_platform_counts, checkpoint_paths = generate_platform_outputs(
            generator, args.count, jsonl_save_path, output_dir
        )

    if args.test_framework == "ttk":
        from agent.hs import (
            audit_golden_coverage, install_ttk_plugin, is_hs_operator,
            load_golden_manifest, validate_hs_cases,
        )
        from scripts.atc_to_ttk import convert_file, _ordered_input_tensor_names

        operator_name = generator.operator_name
        if not is_hs_operator(operator_name):
            raise SystemExit(
                f"--test-framework ttk currently supports documented HiSilicon operators only: {operator_name}"
            )
        # cases.json is the canonical, framework-neutral concrete-case model.
        # Keep one selected platform canonical for execution while preserving
        # every per-platform JSON generated above for audit/replay.
        selected_platform = next(iter(per_platform_paths))
        selected_source = per_platform_paths[selected_platform]
        canonical_cases = output_dir / "cases.json"
        canonical_cases.write_text(selected_source.read_text(encoding="utf-8"), encoding="utf-8")
        concrete_cases = json.loads(canonical_cases.read_text(encoding="utf-8"))
        hs_case_audit = validate_hs_cases(concrete_cases, constraints)
        (output_dir / "hs_case_audit.json").write_text(
            json.dumps(hs_case_audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if hs_case_audit["semantically_clean_count"] != hs_case_audit["case_count"]:
            failing = [
                entry for entry in hs_case_audit["audit"] if entry["issues"]
            ]
            raise RuntimeError(
                "HS_CASE_SEMANTIC_VALIDATION_FAILED: "
                + json.dumps(failing[:5], ensure_ascii=False)
            )
        if hs_case_audit["missing_scenarios"]:
            raise RuntimeError(
                "HS_SCENARIO_COVERAGE_FAILED: "
                + ", ".join(hs_case_audit["missing_scenarios"])
            )
        ttk_output = output_path if output_path.suffix.lower() == ".csv" else output_path.with_suffix(".csv")
        tensor_order = _ordered_input_tensor_names(constraints)
        conversion = convert_file(canonical_cases, ttk_output, selected_platform, tensor_order)
        plugin = install_ttk_plugin(operator_name, ttk_output.parent)
        manifest = load_golden_manifest(operator_name)
        coverage = audit_golden_coverage(concrete_cases, manifest)
        manifest = {**manifest, "coverage": coverage}
        if coverage["status"] != "verified":
            manifest["status"] = "partial"
        (ttk_output.parent / "golden_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary = {
            "operator_name": operator_name,
            "test_framework": "ttk",
            "intermediate_model": str(canonical_cases),
            "selected_platform": selected_platform,
            "platforms": per_platform_counts,
            "per_platform_files": {k: str(v) for k, v in per_platform_paths.items()},
            "requested_per_platform": args.count,
            "total": conversion["case_count"],
            "semantically_clean_count": conversion["semantically_clean_count"],
            "hs_semantically_clean_count": hs_case_audit["semantically_clean_count"],
            "hs_scenario_counts": hs_case_audit["scenario_counts"],
            "scenario_generation": scenario_generation,
            "hs_case_audit": str(output_dir / "hs_case_audit.json"),
            "output": str(ttk_output),
            "golden_plugin": str(plugin),
            "golden_status": manifest.get("status", "missing"),
            "golden_covered_cases": coverage["covered_count"],
            "golden_manifest": str(ttk_output.parent / "golden_manifest.json"),
            "adapter": "scripts.atc_to_ttk.convert_file",
        }
        (ttk_output.parent / "generation_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (ttk_output.parent / "ttk_conversion_audit.json").write_text(
            json.dumps(conversion, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0

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
