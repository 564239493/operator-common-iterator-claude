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


def _select_ttk_platform(
    per_platform_paths: dict[str, Path],
    requested_platform: str | None,
    server_config: str | None,
) -> tuple[str, str]:
    """Select the canonical TTK platform, preferring executable servers.

    Priority: explicit ``--platform``; then servers/file order and each
    server's ``platforms`` order; finally the historical first generated
    platform only when no server configuration file is available.
    """
    available = list(per_platform_paths)
    if not available:
        raise RuntimeError("no per-platform cases were generated")
    if requested_platform:
        if requested_platform not in per_platform_paths:
            raise RuntimeError(
                f"requested platform {requested_platform!r} has no generated cases; "
                f"available={available}"
            )
        return requested_platform, "explicit_cli"

    config_path = Path(server_config or "servers.json").expanduser()
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    if not config_path.is_file():
        logger.warning(
            "server config %s not found; falling back to first generated platform %s",
            config_path,
            available[0],
        )
        return available[0], "first_generated_no_server_config"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read server config {config_path}: {exc}") from exc
    servers = payload.get("servers") if isinstance(payload, dict) else None
    if not isinstance(servers, list):
        raise RuntimeError(f"server config {config_path} has no servers array")

    available_set = set(available)
    configured: list[str] = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        for platform in server.get("platforms") or []:
            platform = str(platform)
            configured.append(platform)
            if platform in available_set:
                return platform, "server_config_match"
    raise RuntimeError(
        "servers.json platforms do not cover any generated operator platform: "
        f"configured={configured}, generated={available}"
    )


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
    from agent.hs.scenario_planner import (
        plan_hs_scenarios,
        pin_scenario_constraints,
        project_hs_case,
    )

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
            payload = [
                project_hs_case(
                    case.model_dump(), operator_name, scenario,
                    len(combined) + ordinal, constraints, platform,
                )
                for ordinal, case in enumerate(generated)
            ]
            if len(payload) != scenario.count:
                raise RuntimeError(
                    "HS_SCENARIO_GENERATION_INCOMPLETE: "
                    f"platform={platform}, scenario={scenario.name}, "
                    f"requested={scenario.count}, generated={len(payload)}"
                )
            combined.extend(payload)
            # The retained generator converts its transient JSONL to this JSON
            # file. Overwrite it with the projected payload so checkpoints and
            # final per-platform files describe the same runnable cases.
            scenario_checkpoint = scenario_root / f"{operator_name}.json"
            scenario_checkpoint.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
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
        help=(
            "测试框架；ttk 会按算子名分流：aclnn* 输出 TTK ACLNN CSV，"
            "torch_npu.* 输出 TTK E2E CSV。"
        ),
    )
    parser.add_argument(
        "--platform",
        default=None,
        help=(
            "可选：显式指定 TTK canonical/CSV 平台。未指定时按 servers.json "
            "中的服务器及 platforms 顺序选择第一个已有 per-platform cases 的平台。"
        ),
    )
    parser.add_argument(
        "--server-config",
        default="servers.json",
        help="平台选择使用的服务器配置（默认 servers.json）。",
    )
    parser.add_argument(
        "--hs-scenario-mode",
        choices=("planned", "original"),
        default="planned",
        help=(
            "torch_npu + TTK 用例生成模式；planned 按 "
            "TND/BSND/paged_attention 分场景固定并投影用例（默认），"
            "original 直接使用 agent.generators 原生逻辑，不做场景"
            "拆分、属性固定或 case 投影。对 ACLNN/ATK 无影响。"
        ),
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
        "start: constraints=%s output=%s count=%d seed=%d iter_dir=%s "
        "hs_scenario_mode=%s",
        args.constraints,
        args.output,
        args.count,
        args.seed,
        iter_dir or "(none)",
        args.hs_scenario_mode,
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
    if str(constraints.get("operator_name", "")).startswith(("torch_npu.", "torch.npu.")):
        from agent.hs.constraint_validation import validate_hs_constraints

        hs_constraint_errors = validate_hs_constraints(constraints)
        if hs_constraint_errors:
            logger.warning(
                "HS constraint semantic warnings (non-blocking): %s",
                json.dumps(hs_constraint_errors[:20], ensure_ascii=False),
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

        if (
            is_hs_operator(generator.operator_name)
            and args.hs_scenario_mode == "planned"
        ):
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
        operator_name = generator.operator_name
        # cases.json is the canonical, framework-neutral concrete-case model.
        # Keep one selected platform canonical for execution while preserving
        # every per-platform JSON generated above for audit/replay.
        selected_platform, platform_selection_reason = _select_ttk_platform(
            per_platform_paths,
            args.platform,
            args.server_config,
        )
        logger.info(
            "TTK canonical platform selected: %s (%s)",
            selected_platform,
            platform_selection_reason,
        )
        selected_source = per_platform_paths[selected_platform]
        canonical_cases = output_dir / "cases.json"
        materialization_report = None
        selected_materialized_source: Path | None = None
        if operator_name == "aclnnScatterPaKvCache":
            from scripts.atk_to_ttk_aclnn import (
                materialize_scatter_pa_kv_cache_cases,
            )

            per_platform_materialization: dict[str, Any] = {}
            for platform, platform_path in per_platform_paths.items():
                platform_cases = json.loads(
                    platform_path.read_text(encoding="utf-8")
                )
                materialized_cases, report = (
                    materialize_scatter_pa_kv_cache_cases(platform_cases)
                )
                materialized_path = platform_path.with_name(
                    f"{platform_path.stem}_ttk_materialized.json"
                )
                materialized_path.write_text(
                    json.dumps(
                        materialized_cases, ensure_ascii=False, indent=2
                    ),
                    encoding="utf-8",
                )
                per_platform_materialization[platform] = {
                    **report,
                    "source_file": str(platform_path),
                    "materialized_file": str(materialized_path),
                }
                if platform == selected_platform:
                    selected_materialized_source = materialized_path
            materialization_report = {
                "operator": operator_name,
                "reason": (
                    "generic generation does not yet solve the operator's "
                    "correlated documented scenarios atomically"
                ),
                "per_platform": per_platform_materialization,
            }
            (output_dir / "ttk_materialization_report.json").write_text(
                json.dumps(
                    materialization_report, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
        if operator_name == "torch_npu.npu_mla_prolog_v3":
            from scripts.atc_to_ttk import materialize_mla_prolog_v3_ttk_cases

            per_platform_materialization: dict[str, Any] = {}
            for platform, platform_path in per_platform_paths.items():
                platform_cases = json.loads(
                    platform_path.read_text(encoding="utf-8")
                )
                materialized_cases, report = (
                    materialize_mla_prolog_v3_ttk_cases(platform_cases)
                )
                materialized_path = platform_path.with_name(
                    f"{platform_path.stem}_ttk_materialized.json"
                )
                materialized_path.write_text(
                    json.dumps(
                        materialized_cases, ensure_ascii=False, indent=2
                    ),
                    encoding="utf-8",
                )
                per_platform_materialization[platform] = {
                    **report,
                    "source_file": str(platform_path),
                    "materialized_file": str(materialized_path),
                }
                if platform == selected_platform:
                    selected_materialized_source = materialized_path
            materialization_report = {
                "operator": operator_name,
                "reason": (
                    "original generation samples MLA's correlated mode, dtype, "
                    "presence, rank and format axes independently; use a "
                    "documented functional baseline for TTK execution"
                ),
                "per_platform": per_platform_materialization,
            }
            (output_dir / "ttk_materialization_report.json").write_text(
                json.dumps(
                    materialization_report, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
        if operator_name == "torch_npu.npu_sparse_flash_attention":
            from scripts.atc_to_ttk import materialize_sparse_attention_ttk_cases

            per_platform_materialization: dict[str, Any] = {}
            for platform, platform_path in per_platform_paths.items():
                platform_cases = json.loads(
                    platform_path.read_text(encoding="utf-8")
                )
                platform_cases, report = materialize_sparse_attention_ttk_cases(
                    platform_cases
                )
                platform_path.write_text(
                    json.dumps(platform_cases, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                per_platform_materialization[platform] = report
            materialization_report = {
                "operator": operator_name,
                "per_platform": per_platform_materialization,
            }
            (output_dir / "ttk_materialization_report.json").write_text(
                json.dumps(materialization_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        selected_source = (
            selected_materialized_source
            or per_platform_paths[selected_platform]
        )
        canonical_cases.write_text(
            selected_source.read_text(encoding="utf-8"), encoding="utf-8"
        )

        if operator_name.startswith("aclnn"):
            from scripts.atk_to_ttk_aclnn import convert_file
            from scripts.validate_ttk_aclnn_csv import validate_csv

            ttk_output = (
                output_path
                if output_path.suffix.lower() == ".csv"
                else output_path.with_suffix(".csv")
            )
            conversion = convert_file(
                canonical_cases, ttk_output, constraints=constraints
            )
            csv_validation = validate_csv(ttk_output)
            conversion_failures = [
                entry for entry in conversion["audit"] if entry["issues"]
            ]
            if conversion_failures or not csv_validation["valid"]:
                logger.warning(
                    "TTK ACLNN adapter validation warnings (non-blocking): %s",
                    json.dumps(
                        {
                            "case_failures": conversion_failures[:5],
                            "csv_issues": csv_validation["issues"][:20],
                            "csv_warnings": csv_validation["warnings"][:20],
                        }, ensure_ascii=False,
                    ),
                )
            conversion = {**conversion, "csv_validation": csv_validation}
            summary = {
                "operator_name": operator_name,
                "test_framework": "ttk",
                "ttk_mode": "aclnn",
                "intermediate_model": str(canonical_cases),
                "selected_platform": selected_platform,
                "platform_selection_reason": platform_selection_reason,
                "platforms": per_platform_counts,
                "per_platform_files": {
                    key: str(value) for key, value in per_platform_paths.items()
                },
                "requested_per_platform": args.count,
                "total": conversion["case_count"],
                "semantically_clean_count": conversion["semantically_clean_count"],
                "output": str(ttk_output),
                "adapter": "scripts.atk_to_ttk_aclnn.convert_file",
                "ttk_materialization": (
                    str(output_dir / "ttk_materialization_report.json")
                    if materialization_report is not None else None
                ),
                "golden_required": False,
                "ttk_command": (
                    f"python3 -m ttk aclnn -i {ttk_output.name} --plat=<plat>"
                ),
            }
            (output_dir / "generation_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (output_dir / "ttk_conversion_audit.json").write_text(
                json.dumps(conversion, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(summary, ensure_ascii=False))
            return 0

        from agent.hs import is_hs_operator, validate_hs_cases
        from scripts.atc_to_ttk import convert_file, _ordered_input_tensor_names

        if not is_hs_operator(operator_name):
            raise SystemExit(
                "--test-framework ttk requires an aclnn* or documented "
                f"torch_npu operator: {operator_name}"
            )
        platform_audits: dict[str, dict[str, Any]] = {}
        platform_audit_paths: dict[str, str] = {}
        for platform, platform_path in per_platform_paths.items():
            platform_cases = json.loads(platform_path.read_text(encoding="utf-8"))
            audit = validate_hs_cases(platform_cases, constraints, platform)
            sanitized = platform.replace("/", "_")
            audit_path = output_dir / f"hs_case_audit_{sanitized}.json"
            audit_path.write_text(
                json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            platform_audits[platform] = audit
            platform_audit_paths[platform] = str(audit_path)
        concrete_cases = json.loads(canonical_cases.read_text(encoding="utf-8"))
        hs_case_audit = platform_audits[selected_platform]
        (output_dir / "hs_case_audit.json").write_text(
            json.dumps(hs_case_audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        failing_platforms = {
            platform: [entry for entry in audit["audit"] if entry["issues"]]
            for platform, audit in platform_audits.items()
            if audit["semantically_clean_count"] != audit["case_count"]
        }
        if failing_platforms:
            logger.warning(
                "HS case semantic warnings (non-blocking): %s",
                json.dumps(
                    {
                        platform: failures[:5]
                        for platform, failures in failing_platforms.items()
                    }, ensure_ascii=False,
                ),
            )
        missing_by_platform = {
            platform: audit["missing_scenarios"]
            for platform, audit in platform_audits.items()
            if audit["missing_scenarios"]
        }
        if missing_by_platform:
            logger.warning(
                "HS scenario coverage warnings (non-blocking): %s",
                json.dumps(missing_by_platform, ensure_ascii=False),
            )
        ttk_output = output_path if output_path.suffix.lower() == ".csv" else output_path.with_suffix(".csv")
        tensor_order = _ordered_input_tensor_names(constraints)
        conversion = convert_file(canonical_cases, ttk_output, selected_platform, tensor_order)
        conversion_failures = [
            entry for entry in conversion["audit"] if entry["issues"]
        ]
        if conversion_failures or conversion["self_check_warnings"]:
            logger.warning(
                "TTK adapter validation warnings (non-blocking): %s",
                json.dumps(
                    {
                        "case_failures": conversion_failures[:5],
                        "self_check_warnings": conversion["self_check_warnings"][:10],
                    }, ensure_ascii=False,
                ),
            )
        summary = {
            "operator_name": operator_name,
            "test_framework": "ttk",
            "intermediate_model": str(canonical_cases),
            "selected_platform": selected_platform,
            "platform_selection_reason": platform_selection_reason,
            "platforms": per_platform_counts,
            "per_platform_files": {k: str(v) for k, v in per_platform_paths.items()},
            "requested_per_platform": args.count,
            "total": conversion["case_count"],
            "semantically_clean_count": conversion["semantically_clean_count"],
            "hs_semantically_clean_count": hs_case_audit["semantically_clean_count"],
            "hs_scenario_counts": hs_case_audit["scenario_counts"],
            "hs_domain_coverage": hs_case_audit["domain_coverage"],
            "hs_domain_coverage_complete": hs_case_audit["domain_coverage_complete"],
            "hs_scenario_mode": args.hs_scenario_mode,
            "scenario_generation": scenario_generation,
            "hs_case_audit": str(output_dir / "hs_case_audit.json"),
            "per_platform_hs_case_audits": platform_audit_paths,
            "output": str(ttk_output),
            "golden_required": False,
            "golden_status": "optional_non_blocking_at_execute",
            "golden_covered_cases": None,
            "golden_manifest": None,
            "adapter": "scripts.atc_to_ttk.convert_file",
            "ttk_content_generation_mode": conversion["content_generation_mode"],
            "ttk_content_generation_limitations": conversion["content_generation_limitations"],
            "ttk_materialization": (
                str(output_dir / "ttk_materialization_report.json")
                if materialization_report is not None else None
            ),
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
