#!/usr/bin/env python3
"""Deterministically call the retained business case generator.

CLI-only adaptation of the retained generation pipeline in
``agent/generators`` and the reference ``executer/generate_atk.py``.
No NZ or shape post-processing is
applied here — those constraints live in the upstream operator doc
extraction step (constraints.json + agent.generators.facade outputs match
operator_case_generator's ``single_operator_handle`` semantics).

Outputs:
- ``<output_dir>/cases_<platform>.json`` — one JSON array per product_support
  entry, ``id`` is the integer the facade emits (per-platform 0, 1, 2, …)
- ``<output_dir>/cases.json``       — the selected execution platform's cases
  (atk and ttk alike; selection is ``_select_execution_platform``: explicit
  ``--platform`` → single scene-stage device → servers.json order). ttk
  additionally derives ``cases_ttk.csv`` from it (fixed name — never taken
  from ``--output``'s filename).
- ``<output>``                       — nominal overall cases path (kept for
  backward CLI compatibility); only its parent dir is used to place the
  artifacts above — the filename influences no artifact name
- ``<output_dir>/generation_summary.json`` — per-platform counts + paths +
  ``selected_platform``/``platform_selection_reason``
- ``<iter_dir>/generation.log``       — when ``--iter-dir`` is passed, a
  timestamped log mirror of the run for diagnostics
- with ``--csv-plugin`` (ttk only): ``cases_ttk.pre_plugin.csv`` preserves the
  base CSV, the plugin writes the final ``cases_ttk.csv``, and
  ``csv_plugin_report.json``/``csv_plugin.log`` record the invocation. The
  document-driven aux-parameter pipeline is skipped.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("generate_cases")


def _configure_utf8_stdio() -> None:
    """Keep redirected console logs UTF-8 on every host and entry path."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace a generated artifact only after its complete content is ready."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_CSV_PLUGIN_TIMEOUT_SECONDS = 1800


def _resolve_csv_plugin_path(raw: str) -> Path:
    """Resolve a user-provided local CSV post-processing script."""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    return candidate.resolve()


def _csv_header_and_row_count(path: Path) -> tuple[list[str], int]:
    """Read just the header and non-empty data-row count from a CSV file."""
    csv.field_size_limit(2**31 - 1)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{path}: CSV is empty") from exc
        row_count = sum(1 for row in reader if any(cell.strip() for cell in row))
    return header, row_count


def _fail_csv_plugin(
    *,
    generation_status_path: Path,
    report_path: Path,
    report: dict[str, Any],
    code: str,
    message: str,
) -> None:
    """Persist a failed CSV-plugin report/status and abort generation."""
    report["ok"] = False
    report.setdefault("errors", []).append(f"{code}: {message}")
    _atomic_write_json(report_path, report)
    _atomic_write_json(generation_status_path, {
        "state": "failed",
        "code": code,
        "message": message,
        "csv_plugin_report": str(report_path),
    })
    raise RuntimeError(f"{code}: {message}")


def _apply_csv_plugin(
    *,
    plugin_path: Path,
    output_dir: Path,
    ttk_output: Path,
    generation_status_path: Path,
    baseline_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the user CSV plugin on the converted TTK CSV.

    The base CSV is preserved as ``cases_ttk.pre_plugin.csv``; the plugin
    writes the final ``cases_ttk.csv`` used by EXECUTE. The plugin contract is
    the same as ``/home/fill_group_list_data.py``:
    ``--input_case_csv <base> --output_case_csv <final>``.
    """
    report_path = output_dir / "csv_plugin_report.json"
    log_path = output_dir / "csv_plugin.log"
    base_csv = output_dir / "cases_ttk.pre_plugin.csv"
    report: dict[str, Any] = {
        "enabled": True,
        "ok": False,
        "script": str(plugin_path),
        "input_csv": str(base_csv),
        "output_csv": str(ttk_output),
        "returncode": None,
        "log": str(log_path),
        "report": str(report_path),
        "errors": [],
    }
    base_renamed = False

    def fail(code: str, message: str) -> None:
        if base_renamed:
            # Never leave a partial/invalid plugin result at the fixed EXECUTE
            # path. The pre-plugin CSV remains available for diagnosis.
            ttk_output.unlink(missing_ok=True)
        _fail_csv_plugin(
            generation_status_path=generation_status_path,
            report_path=report_path,
            report=report,
            code=code,
            message=message,
        )

    if not plugin_path.is_file():
        fail("CSV_PLUGIN_NOT_FOUND", f"csv plugin not found: {plugin_path}")
    if not ttk_output.is_file():
        fail(
            "CSV_PLUGIN_INPUT_MISSING",
            f"TTK CSV was not produced before csv plugin: {ttk_output}",
        )

    try:
        input_header, input_rows = _csv_header_and_row_count(ttk_output)
    except (OSError, ValueError) as exc:
        fail("CSV_PLUGIN_INPUT_INVALID", f"cannot read generated TTK CSV: {exc}")

    base_csv.unlink(missing_ok=True)
    ttk_output.replace(base_csv)
    base_renamed = True

    command = [
        sys.executable,
        str(plugin_path),
        "--input_case_csv",
        str(base_csv),
        "--output_case_csv",
        str(ttk_output),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
            timeout=_CSV_PLUGIN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log_path.write_text(
            "$ " + " ".join(command) + "\n\n[timeout] "
            + f"{_CSV_PLUGIN_TIMEOUT_SECONDS}s\n",
            encoding="utf-8",
        )
        fail(
            "CSV_PLUGIN_TIMEOUT",
            f"csv plugin exceeded {_CSV_PLUGIN_TIMEOUT_SECONDS}s: {plugin_path}",
        )
    except OSError as exc:
        fail("CSV_PLUGIN_EXEC_FAILED", f"cannot execute csv plugin: {exc}")

    log_path.write_text(
        "$ " + " ".join(command)
        + "\n\n[stdout]\n" + (completed.stdout or "")
        + "\n[stderr]\n" + (completed.stderr or ""),
        encoding="utf-8",
    )
    report["returncode"] = completed.returncode
    if completed.returncode != 0:
        stderr_excerpt = (completed.stderr or "").strip()
        if len(stderr_excerpt) > 2000:
            stderr_excerpt = stderr_excerpt[-2000:]
        fail(
            "CSV_PLUGIN_FAILED",
            f"csv plugin exited with {completed.returncode}"
            + (f": {stderr_excerpt}" if stderr_excerpt else ""),
        )

    if not ttk_output.is_file():
        fail(
            "CSV_PLUGIN_OUTPUT_INVALID",
            f"csv plugin did not create output CSV: {ttk_output}",
        )
    try:
        output_header, output_rows = _csv_header_and_row_count(ttk_output)
    except (OSError, ValueError) as exc:
        fail("CSV_PLUGIN_OUTPUT_INVALID", f"cannot read plugin output CSV: {exc}")
    if output_header != input_header:
        fail(
            "CSV_PLUGIN_OUTPUT_INVALID",
            "plugin output header changed: "
            f"input={input_header!r}, output={output_header!r}",
        )
    if output_rows != input_rows:
        fail(
            "CSV_PLUGIN_OUTPUT_INVALID",
            f"plugin output row count changed: input={input_rows}, "
            f"output={output_rows}",
        )

    validation = None
    if baseline_validation is not None:
        from scripts.validate_ttk_aclnn_csv import validate_csv

        validation = validate_csv(ttk_output)
        report["validation"] = validation
        if baseline_validation.get("valid") and not validation.get("valid"):
            fail(
                "CSV_PLUGIN_OUTPUT_INVALID",
                "plugin output failed TTK ACLNN CSV validation: "
                + json.dumps(validation.get("issues", [])[:20], ensure_ascii=False),
            )
        if not baseline_validation.get("valid") and not validation.get("valid"):
            logger.warning(
                "csv plugin output keeps pre-existing TTK ACLNN validation issues: %s",
                json.dumps(validation.get("issues", [])[:10], ensure_ascii=False),
            )

    report.update({
        "ok": True,
        "row_count": output_rows,
        "artifact_hashes": {
            "input_csv": _sha256(base_csv),
            "output_csv": _sha256(ttk_output),
        },
    })
    _atomic_write_json(report_path, report)
    logger.info(
        "csv plugin applied: script=%s rows=%d input=%s output=%s",
        plugin_path,
        output_rows,
        base_csv,
        ttk_output,
    )
    return report


def _csv_plugin_summary(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        key: report.get(key)
        for key in (
            "enabled", "script", "input_csv", "output_csv", "report",
            "row_count", "artifact_hashes",
        )
    }


def _hs_generation_gate_errors(
    platform_audits: dict[str, dict[str, Any]],
    selected_platform: str,
    scenario_mode: str,
) -> list[str]:
    """Return blockers that make a real HS+TTK execution predictably invalid."""
    selected = platform_audits.get(selected_platform)
    if not isinstance(selected, dict):
        return [f"missing HS semantic audit for selected platform {selected_platform!r}"]

    case_count = int(selected.get("case_count") or 0)
    clean_count = int(selected.get("semantically_clean_count") or 0)
    errors: list[str] = []
    if case_count > 0 and clean_count == 0:
        errors.append(
            f"selected platform {selected_platform!r} has 0/{case_count} "
            "semantically clean HS cases"
        )

    missing_scenarios = selected.get("missing_scenarios") or []
    if scenario_mode == "planned" and missing_scenarios:
        errors.append(
            f"planned HS generation missed required scenarios for "
            f"{selected_platform!r}: {missing_scenarios}"
        )
    return errors


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


def _scene_platform_preference(output_dir: Path) -> str | None:
    """Return the single device chosen at the scene stage, if any.

    Reads ``<run-dir>/run_state.json`` → ``scene`` (written by
    ``render_scene_directive.py`` during PLAN). A preference exists only for
    an enabled ``subset``-scope scene narrowed to exactly one device — an
    explicit user device choice. ``scope=all`` lists every device that has
    scenes in the doc (not a user choice); multi-select, disabled scenes,
    and unreadable/absent state (ad-hoc output dirs outside a run) all
    yield ``None`` so selection falls back to servers.json order.
    """
    run_state_path = output_dir.parent / "run_state.json"
    try:
        payload = json.loads(run_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    scene = payload.get("scene") if isinstance(payload, dict) else None
    if not isinstance(scene, dict):
        return None
    if not scene.get("enabled") or scene.get("scope") != "subset":
        return None
    device_types = scene.get("device_types")
    if (
        isinstance(device_types, list)
        and len(device_types) == 1
        and isinstance(device_types[0], str)
        and device_types[0].strip()
    ):
        return device_types[0].strip()
    return None


def _dim_axes_scene_preference(output_dir: Path) -> str | None:
    """从 run_state.scene.selection 提取用户选中的场景名（仅当唯一时）。"""
    run_state_path = output_dir.parent / "run_state.json"
    try:
        payload = json.loads(run_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    scene = payload.get("scene") if isinstance(payload, dict) else None
    if not isinstance(scene, dict):
        return None
    selection = scene.get("selection")
    if not isinstance(selection, dict):
        return None
    names: set[str] = set()
    for device_selection in selection.values():
        if not isinstance(device_selection, dict):
            continue
        for name in device_selection:
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    if len(names) == 1:
        return next(iter(names))
    return None


def _dim_axes_coverage(cases_path: Path, operator_name: str, scene_name: str | None,
                       config_path: str | None) -> dict | None:
    if not scene_name:
        return None
    try:
        from agent.generators.operator_param_models.dim_axes import (
            load_dim_axes, resolve_scene,
        )

        config = load_dim_axes(config_path, strict=True)
        profile, scene, _ = resolve_scene(config, operator_name, scene_name)
        if scene is None:
            return None
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc), "scene": scene_name}

    layouts: set[str] = set()
    by_axis: dict[str, dict[str, int]] = {}
    for case in cases or []:
        for item in case.get("inputs") or []:
            if not isinstance(item, dict):
                continue
            layout = item.get("layout")
            if layout:
                layouts.add(layout)
            axis_values = item.get("axis_values")
            if not isinstance(axis_values, dict):
                continue
            for axis, value in axis_values.items():
                bucket = by_axis.setdefault(axis, {})
                key = str(value)
                bucket[key] = bucket.get(key, 0) + 1

    missing: list[str] = []
    for axis_name, axis in scene.axes.items():
        if axis.is_expr:
            continue
        observed = {int(value) for value in by_axis.get(axis_name, {}) if str(value).lstrip("-").isdigit()}
        for required in axis.coverage_schedule():
            if required not in observed:
                missing.append(f"{axis_name}={required}")

    return {
        "profile": profile.id if profile else None,
        "scene": scene_name,
        "layouts_total": len(scene.layouts),
        "layouts_used": len(layouts),
        "by_axis": by_axis,
        "missing": missing,
    }


def _select_execution_platform(
    per_platform_paths: dict[str, Path],
    requested_platform: str | None,
    server_config: str | None,
    scene_platform: str | None = None,
    strict: bool = True,
) -> tuple[str, str]:
    """Select the execution platform behind ``cases.json`` (atk and ttk).

    Priority: explicit ``--platform``; then a single device selected at the
    scene stage (authoritative user intent — product_support narrowing
    usually leaves only that platform generated; this keeps the choice when
    it does not, e.g. human-edited constraints); then servers/file order and
    each server's ``platforms`` order; finally the historical first
    generated platform only when no server configuration file is available.

    ``strict`` (ttk) raises when servers.json covers none of the generated
    platforms — the CSV must be oriented at a runnable platform. With
    ``strict=False`` (atk) that case degrades to the first generated
    platform: generation itself succeeded and EXECUTE can still retarget
    via its own server-based selection.
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

    if scene_platform:
        if scene_platform in per_platform_paths:
            return scene_platform, "scene_selection"
        logger.warning(
            "scene-selected device %r has no generated cases bucket; "
            "falling back to server-config selection",
            scene_platform,
        )

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
    if not strict:
        logger.warning(
            "servers.json platforms do not cover any generated operator "
            "platform; falling back to first generated platform %s "
            "(configured=%s, generated=%s)",
            available[0],
            configured,
            available,
        )
        return available[0], "first_generated_no_server_coverage"
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
        checkpoint_dir.mkdir(parents=True, exist_ok=True)  # 新增
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


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--constraints", required=True)
    parser.add_argument(
        "--output",
        required=True,
        help=(
            "输出目录定位 + legacy 兼容路径；产物文件名固定"
            "（cases.json / cases_ttk.csv / generation_summary.json），"
            "不取本参数的文件名"
        ),
    )
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--dim-axes-config",
        default=None,
        help="维度轴取值配置路径；缺省使用 agent/generators/configs/dim_axes.json（存在时）。",
    )
    parser.add_argument(
        "--dim-axes-scene",
        default=None,
        help="显式指定 dim_axes 场景名；缺省从 run_state.scene.selection 推断。",
    )
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
            "可选：显式指定执行平台（atk 的 cases.json 与 ttk 的 "
            "cases_ttk.csv 出自同一选择）。未指定时优先场景阶段单选设备，"
            "再按 servers.json 中的服务器及 platforms 顺序选择第一个已有 "
            "per-platform cases 的平台。"
        ),
    )
    parser.add_argument(
        "--csv-plugin",
        default=None,
        help=(
            "TTK 专用：基础 cases_ttk.csv 生成后，调用该本地 Python 脚本做 "
            "CSV 后处理。脚本契约：--input_case_csv <基础CSV> "
            "--output_case_csv <最终CSV>。启用时跳过文档驱动的辅助参数构造/"
            "groupListData materialize/aux golden.py；ttk_plugin 值契约保留。"
        ),
    )
    parser.add_argument(
        "--hs-scenario-mode",
        choices=("original", "planned"),
        default="original",
        help=(
            "torch_npu + TTK 用例生成模式；original（默认）直接使用 "
            "agent.generators 原生逻辑，不做场景拆分、属性固定或 case 投影；"
            "planned 显式按 "
            "TND/BSND/paged_attention 分场景固定并投影用例，"
            "对 ACLNN/ATK 无影响。"
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
    parser.add_argument(
        "--server-config",
        default=str(ROOT / "servers.json"),
        help=(
            "服务器配置路径：TTK 平台选择会读取 servers/platforms；fusion "
            "分布式算子还会读取 fusion_devices，将 rankSize 钉为实际卡数。"
            "默认项目根 servers.json；文件不存在或无 fusion server 时不钉值。"
        ),
    )
    args = parser.parse_args()
    if args.count < 1:
        raise SystemExit("count must be positive")
    if args.csv_plugin and args.test_framework != "ttk":
        raise SystemExit("--csv-plugin 仅支持 --test-framework ttk")
    csv_plugin_path = (
        _resolve_csv_plugin_path(args.csv_plugin) if args.csv_plugin else None
    )

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

    output_dir = output_path.parent
    dim_axes_scene = args.dim_axes_scene or _dim_axes_scene_preference(output_dir)
    generator = TestCaseGenerator(
        constraints,
        seed=args.seed,
        dim_axes_config_path=args.dim_axes_config,
        dim_axes_scene=dim_axes_scene,
    )
    generation_status_path = output_dir / "generation_status.json"
    if args.test_framework == "ttk":
        _atomic_write_json(generation_status_path, {
            "state": "in_progress",
            "message": (
                "TTK generation has not completed; EXECUTE must not reuse "
                "stale cases.json or CSV artifacts from an earlier attempt."
            ),
        })
        if csv_plugin_path is not None and not csv_plugin_path.is_file():
            message = f"csv plugin not found: {csv_plugin_path}"
            _atomic_write_json(generation_status_path, {
                "state": "failed",
                "code": "CSV_PLUGIN_NOT_FOUND",
                "message": message,
            })
            raise RuntimeError(f"CSV_PLUGIN_NOT_FOUND: {message}")
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

    # ── Execution platform selection (unified for atk and ttk) ──────────
    # cases.json is this iteration's framework-neutral case file: the
    # selected execution platform's cases, while every per-platform JSON
    # generated above is preserved for audit/replay. Priority: explicit
    # --platform, then a single scene-stage device selection, then
    # servers.json order (see _select_execution_platform).
    operator_name = generator.operator_name
    selected_platform, platform_selection_reason = _select_execution_platform(
        per_platform_paths,
        args.platform,
        args.server_config,
        scene_platform=_scene_platform_preference(output_dir),
        strict=(args.test_framework == "ttk"),
    )
    logger.info(
        "execution platform selected: %s (%s)",
        selected_platform,
        platform_selection_reason,
    )
    cases_json_path = output_dir / "cases.json"
    selected_source = per_platform_paths[selected_platform]
    materialization_report = None
    per_platform_execution_paths = dict(per_platform_paths)

    if args.test_framework == "ttk":
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
                per_platform_execution_paths[platform] = materialized_path
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
                per_platform_execution_paths[platform] = materialized_path
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
        if operator_name == "torch_npu.npu_quant_lightning_indexer":
            from scripts.atc_to_ttk import (
                materialize_quant_lightning_indexer_ttk_cases,
            )

            per_platform_materialization: dict[str, Any] = {}
            for platform, platform_path in per_platform_paths.items():
                platform_cases = json.loads(
                    platform_path.read_text(encoding="utf-8")
                )
                materialized_cases, report = (
                    materialize_quant_lightning_indexer_ttk_cases(
                        platform_cases
                    )
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
                per_platform_execution_paths[platform] = materialized_path
            materialization_report = {
                "operator": operator_name,
                "reason": (
                    "range-only TTK cannot represent multi-batch prefix sums "
                    "or unconstrained PageAttention indices safely; retain raw "
                    "generator output and execute a documented smoke projection"
                ),
                "per_platform": per_platform_materialization,
            }
            (output_dir / "ttk_materialization_report.json").write_text(
                json.dumps(
                    materialization_report, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
        selected_source = (
            selected_materialized_source
            or per_platform_execution_paths[selected_platform]
        )

    aux_report: dict[str, Any] = {
        "operator_name": operator_name,
        "specs": [],
        "updated_cases": 0,
        "updated_params": 0,
    }
    specs: list[dict[str, Any]] = []
    if args.test_framework == "ttk":
        from scripts.operator_aux_params import inject_aux_params_into_file

        if csv_plugin_path is not None:
            # ``--csv-plugin`` owns the document-driven auxiliary-parameter
            # construction. Keep only the ttk_plugin value contract
            # (plugin_requirements.json + value_constraints.json); do not run
            # doc/knowledge detection or write inputs/aux_params.json.
            from scripts.operator_aux_params import detect_plugin_value_specs

            specs = detect_plugin_value_specs(output_dir.parent, constraints)
        else:
            from scripts.operator_aux_params import ensure_run_aux_specs

            spec_payload = ensure_run_aux_specs(
                output_dir.parent, operator_name, constraints,
            )
            specs = spec_payload.get("aux_params", []) or []
        if specs:
            aux_report = inject_aux_params_into_file(
                selected_source, operator_name, specs=specs,
            )
        if csv_plugin_path is None:
            plugin = next(
                (spec.get("plugin") for spec in specs if spec.get("plugin")),
                None,
            )
            if plugin:
                plugin_path = ROOT / plugin
                if plugin_path.is_file():
                    shutil.copy2(plugin_path, output_dir / "golden.py")
                else:
                    logger.warning("aux plugin not found: %s", plugin_path)
            elif specs:
                aux_targets = {
                    spec["name"]: spec.get("signature_index")
                    for spec in specs
                    if spec.get("signature_index") is not None
                    and spec.get("value_kind") != "tensor_values"
                }
                if aux_targets:
                    template = (
                        ROOT / "scripts" / "aux_input_plugin_template.py"
                    ).read_text(encoding="utf-8")
                    template = template.replace(
                        "AUX_TARGETS = {}",
                        f"AUX_TARGETS = {aux_targets!r}",
                        1,
                    )
                    template = template.replace(
                        '__input__ = {"aclnn": {}}',
                        "__input__ = {\"aclnn\": {"
                        + json.dumps(operator_name)
                        + ': "apply_aux_params"}}',
                        1,
                    )
                    (output_dir / "golden.py").write_text(
                        template, encoding="utf-8",
                    )
                else:
                    logger.warning(
                        "aux params have no signature_index; generic input plugin "
                        "not generated"
                    )
        if aux_report.get("updated_params"):
            logger.info(
                "injected auxiliary params: %s",
                json.dumps(
                    {
                        "specs": [
                            spec.get("name") for spec in aux_report.get("specs", [])
                        ],
                        "updated_cases": aux_report.get("updated_cases"),
                        "updated_params": aux_report.get("updated_params"),
                    },
                    ensure_ascii=False,
                ),
            )

    # Framework-neutral cases.json: written for atk and ttk alike,
    # and before generation_summary.json, so completion detection (which
    # watches the summary) never reports a finished generation without
    # this artifact.
    _atomic_write_text(
        cases_json_path, selected_source.read_text(encoding="utf-8"),
    )

    if (
        args.test_framework == "ttk"
        and any(
            spec.get("value_kind") == "tensor_values" for spec in specs
        )
    ):
        try:
            from scripts.operator_aux_params import apply_plugin_case_contract
        except ModuleNotFoundError:
            from operator_aux_params import apply_plugin_case_contract
        contract_report = apply_plugin_case_contract(cases_json_path)
        _atomic_write_json(
            output_dir / "plugin_case_contract_report.json",
            contract_report,
        )

    csv_plugin_report: dict[str, Any] | None = None
    if args.test_framework == "ttk":
        if operator_name.startswith("aclnn"):
            from scripts.atk_to_ttk_aclnn import convert_file
            from scripts.validate_ttk_aclnn_csv import validate_csv

            ttk_output = output_dir / "cases_ttk.csv"
            ttk_temporary = ttk_output.with_name(f".{ttk_output.name}.tmp")
            plugin_value_params = {
                str(spec["target_param"]).casefold()
                for spec in specs
                if spec.get("target_param")
                and spec.get("value_kind") == "tensor_values"
            }
            conversion = convert_file(
                cases_json_path, ttk_temporary, constraints=constraints,
                plugin_value_params=plugin_value_params,
            )
            aux_materializations: list[dict[str, Any]] = []
            group_list_report = None
            if aux_report.get("updated_params"):
                from scripts.materialize_group_list_data import materialize_csv

                for spec in specs:
                    attr_name = spec.get("name")
                    if not attr_name:
                        continue
                    report_path = output_dir / (
                        "group_list_data_report.json"
                        if attr_name == "groupListData"
                        else f"{attr_name}_materialize_report.json"
                    )
                    report = materialize_csv(
                        ttk_temporary,
                        ttk_temporary,
                        target="aclnn",
                        clear_range=True,
                        api_name=operator_name,
                        attr_name=attr_name,
                        validator=spec.get("validator", "group_list_data"),
                        target_param=spec.get("target_param", ""),
                        group_list_index=spec.get("tensor_index", 8),
                        report_json=report_path,
                    )
                    if not report.get("ok"):
                        raise SystemExit(
                            f"{attr_name} materialization failed: "
                            + json.dumps(
                                report.get("errors", []),
                                ensure_ascii=False,
                            )
                        )
                    aux_materializations.append({
                        "attr_name": attr_name,
                        "spec": spec,
                        "report": report,
                        "report_path": str(report_path),
                    })
                    if attr_name == "groupListData":
                        group_list_report = report
                _atomic_write_json(
                    output_dir / "aux_params_report.json",
                    {
                        "ok": True,
                        "operator_name": operator_name,
                        "specs": specs,
                        "materializations": aux_materializations,
                    },
                )
            csv_validation = validate_csv(ttk_temporary)
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
            ttk_temporary.replace(ttk_output)
            conversion["destination"] = str(ttk_output)
            if csv_plugin_path is not None:
                csv_plugin_report = _apply_csv_plugin(
                    plugin_path=csv_plugin_path,
                    output_dir=output_dir,
                    ttk_output=ttk_output,
                    generation_status_path=generation_status_path,
                    baseline_validation=csv_validation,
                )
            for item in aux_materializations:
                report = item["report"]
                report["input_csv"] = str(ttk_output)
                report["output_csv"] = str(ttk_output)
                _atomic_write_json(Path(item["report_path"]), report)
            if aux_materializations:
                _atomic_write_json(
                    output_dir / "aux_params_report.json",
                    {
                        "ok": True,
                        "operator_name": operator_name,
                        "specs": specs,
                        "materializations": aux_materializations,
                    },
                )
            summary = {
                "operator_name": operator_name,
                "test_framework": "ttk",
                "ttk_mode": "aclnn",
                "intermediate_model": str(cases_json_path),
                "selected_platform": selected_platform,
                "platform_selection_reason": platform_selection_reason,
                "platforms": per_platform_counts,
                "per_platform_files": {
                    key: str(value) for key, value in per_platform_paths.items()
                },
                "per_platform_execution_files": {
                    key: str(value)
                    for key, value in per_platform_execution_paths.items()
                },
                "selected_execution_file": str(selected_source),
                "requested_per_platform": args.count,
                "total": conversion["case_count"],
                "semantically_clean_count": conversion["semantically_clean_count"],
                "output": str(ttk_output),
                "adapter": "scripts.atk_to_ttk_aclnn.convert_file",
                "csv_plugin": _csv_plugin_summary(csv_plugin_report),
                "aux_params": aux_report,
                "aux_params_report": (
                    str(output_dir / "aux_params_report.json")
                    if aux_materializations else None
                ),
                "aux_materializations": [
                    {
                        "attr_name": item["attr_name"],
                        "report": item["report_path"],
                    }
                    for item in aux_materializations
                ],
                "group_list_data_report": (
                    str(output_dir / "group_list_data_report.json")
                    if group_list_report is not None else None
                ),
                "ttk_materialization": (
                    str(output_dir / "ttk_materialization_report.json")
                    if materialization_report is not None else None
                ),
                "golden_required": False,
                "ttk_command": (
                    f"python3 -m ttk aclnn -i {ttk_output.name} --plat=<plat>"
                ),
            }
            summary["artifact_hashes"] = {
                "selected_execution_file": _sha256(selected_source),
                "cases_json": _sha256(cases_json_path),
                "cases_csv": _sha256(ttk_output),
            }
            _atomic_write_json(output_dir / "generation_summary.json", summary)
            _atomic_write_json(
                output_dir / "ttk_conversion_audit.json", conversion,
            )
            _atomic_write_json(generation_status_path, {
                "state": "complete",
                "selected_platform": selected_platform,
                "selected_execution_file": str(selected_source),
                "cases_json": str(cases_json_path),
                "cases_csv": str(ttk_output),
                "csv_plugin_report": (
                    str(output_dir / "csv_plugin_report.json")
                    if csv_plugin_report is not None else None
                ),
                "artifact_hashes": summary["artifact_hashes"],
            })
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
        concrete_cases = json.loads(cases_json_path.read_text(encoding="utf-8"))
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
        generation_gate_errors = _hs_generation_gate_errors(
            platform_audits,
            selected_platform,
            args.hs_scenario_mode,
        )
        if generation_gate_errors:
            failure_payload = {
                "state": "failed",
                "code": "HS_SEMANTIC_GATE_FAILED",
                "message": (
                    "HS semantic generation gate rejected cases before TTK "
                    "conversion/EXECUTE"
                ),
                "selected_platform": selected_platform,
                "hs_scenario_mode": args.hs_scenario_mode,
                "errors": generation_gate_errors,
                "per_platform_hs_case_audits": platform_audit_paths,
            }
            _atomic_write_json(generation_status_path, failure_payload)
            raise RuntimeError("; ".join(generation_gate_errors))
        ttk_output = output_dir / "cases_ttk.csv"
        tensor_order = _ordered_input_tensor_names(constraints)
        ttk_temporary = ttk_output.with_name(f".{ttk_output.name}.tmp")
        conversion = convert_file(
            cases_json_path, ttk_temporary, selected_platform, tensor_order,
        )
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
        ttk_temporary.replace(ttk_output)
        conversion["destination"] = str(ttk_output)
        if csv_plugin_path is not None:
            csv_plugin_report = _apply_csv_plugin(
                plugin_path=csv_plugin_path,
                output_dir=output_dir,
                ttk_output=ttk_output,
                generation_status_path=generation_status_path,
            )
        summary = {
            "operator_name": operator_name,
            "test_framework": "ttk",
            "intermediate_model": str(cases_json_path),
            "selected_platform": selected_platform,
            "platform_selection_reason": platform_selection_reason,
            "platforms": per_platform_counts,
            "per_platform_files": {k: str(v) for k, v in per_platform_paths.items()},
            "per_platform_execution_files": {
                key: str(value)
                for key, value in per_platform_execution_paths.items()
            },
            "selected_execution_file": str(selected_source),
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
            "csv_plugin": _csv_plugin_summary(csv_plugin_report),
            "ttk_content_generation_mode": conversion["content_generation_mode"],
            "ttk_content_generation_limitations": conversion["content_generation_limitations"],
            "ttk_materialization": (
                str(output_dir / "ttk_materialization_report.json")
                if materialization_report is not None else None
            ),
        }
        summary["artifact_hashes"] = {
            "selected_execution_file": _sha256(selected_source),
            "cases_json": _sha256(cases_json_path),
            "cases_csv": _sha256(ttk_output),
        }
        _atomic_write_json(ttk_output.parent / "generation_summary.json", summary)
        _atomic_write_json(
            ttk_output.parent / "ttk_conversion_audit.json", conversion,
        )
        _atomic_write_json(generation_status_path, {
            "state": "complete",
            "selected_platform": selected_platform,
            "selected_execution_file": str(selected_source),
            "cases_json": str(cases_json_path),
            "cases_csv": str(ttk_output),
            "csv_plugin_report": (
                str(output_dir / "csv_plugin_report.json")
                if csv_plugin_report is not None else None
            ),
            "artifact_hashes": summary["artifact_hashes"],
        })
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    _log_ranksize_distribution(per_platform_paths, pinned_ws)

    summary = {
        "operator_name": generator.operator_name,
        "test_framework": "atk",
        "requested_per_platform": args.count,
        "platforms": per_platform_counts,
        "selected_platform": selected_platform,
        "platform_selection_reason": platform_selection_reason,
        "cases_json_path": str(cases_json_path),
        "selected_execution_file": str(selected_source),
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
    dim_axes_coverage = _dim_axes_coverage(
        cases_json_path, operator_name, dim_axes_scene, args.dim_axes_config,
    )
    if dim_axes_coverage is not None:
        summary["dim_axes"] = dim_axes_coverage
    summary["artifact_hashes"] = {
        "selected_execution_file": _sha256(selected_source),
        "cases_json": _sha256(cases_json_path),
    }
    _atomic_write_json(output_dir / "generation_summary.json", summary)

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


def _record_generation_failure(argv: list[str], exc: BaseException) -> None:
    """Best-effort terminal status for a failed TTK generation attempt."""
    status_parser = argparse.ArgumentParser(add_help=False)
    status_parser.add_argument("--output")
    status_parser.add_argument("--test-framework", default="atk")
    try:
        status_args, _ = status_parser.parse_known_args(argv)
    except SystemExit:
        return
    if status_args.test_framework != "ttk" or not status_args.output:
        return

    status_path = Path(status_args.output).parent / "generation_status.json"
    try:
        if status_path.is_file():
            current = json.loads(status_path.read_text(encoding="utf-8"))
            if current.get("state") in {"complete", "failed"}:
                return
    except (OSError, json.JSONDecodeError):
        pass

    message = str(exc).strip() or type(exc).__name__
    try:
        _atomic_write_json(status_path, {
            "state": "failed",
            "error_type": type(exc).__name__,
            "message": message,
        })
    except OSError:
        logger.exception("failed to persist generation failure status: %s", status_path)


def main() -> int:
    _configure_utf8_stdio()
    try:
        return _main()
    except BaseException as exc:
        _record_generation_failure(sys.argv[1:], exc)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
