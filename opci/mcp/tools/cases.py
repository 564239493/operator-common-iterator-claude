"""cases MCP tools: generate_cases, validate_cases.

Strictly aligned with scripts/generate_cases.py generate_platform_outputs (L61-109).
Only adaptation: RuntimeError/ValueError → return {"ok": False, ...} for MCP protocol.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from opci.config import get_project_root, resolve_input_path
from opci.mcp._logging import log, log_elapsed
from opci.mcp._shared import normalize_constraints as _normalize_constraints_fn
from opci.mcp._shared import validate_cases as _validate_cases


def generate_cases(
    constraints: str,
    output: str,
    count: int = 10,
    seed: int = 42,
    jsonl_save_path: str | None = None,
    iter_dir: str | None = None,
) -> dict[str, Any]:
    """Generate cases.json from constraints.json using the deterministic TestCaseGenerator."""
    t0 = time.monotonic()
    log("generate_cases", "start", constraints=constraints, output=output, count=count, seed=seed)

    project_root = get_project_root()
    constraints_path = resolve_input_path(constraints, project_root)
    output_path = resolve_input_path(output, project_root)
    jsonl_save = (
        resolve_input_path(jsonl_save_path, project_root)
        if jsonl_save_path
        else output_path.parent / "jsonl_checkpoints"
    )

    log("generate_cases", "paths_resolved", constraints_path=str(constraints_path), output_path=str(output_path))

    constraints_data: dict[str, Any] = json.loads(constraints_path.read_text(encoding="utf-8"))

    # Normalize first (original: L154-166)
    log("generate_cases", "normalize_start")
    normalized_count = _normalize_constraints_fn(constraints_data)
    log_elapsed("generate_cases", "normalize_done", t0, normalized=normalized_count)

    # Heavy imports
    log("generate_cases", "import_generator_start")
    from opci.agent.generators.facade import TestCaseGenerator

    generator = TestCaseGenerator(constraints_data, seed=seed)
    log_elapsed("generate_cases", "generator_created", t0, operator=generator.operator_name)

    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    from opci.agent.generators.data_definition.param_models_def import RunPlatform
    platforms = generator.supported_platforms or [RunPlatform.DEFAULT_PLATFORM.value]
    log("generate_cases", "platforms", platforms=platforms)

    per_platform_paths: dict[str, str] = {}
    per_platform_counts: dict[str, int] = {}
    checkpoint_paths: dict[str, str] = {}

    # --- Strictly aligned with scripts/generate_cases.py:generate_platform_outputs (L70-109) ---
    for platform in platforms:
        log("generate_cases", "generate_platform_start", platform=platform, count=count)
        sanitized = platform.replace("/", "_")
        checkpoint_dir = jsonl_save / sanitized
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_file = checkpoint_dir / f"{generator.operator_name}.jsonl"
        converted_source = output_dir / f"{generator.operator_name}.json"
        target = output_dir / f"cases_{sanitized}.json"
        checkpoint_paths[platform] = str(checkpoint_file)

        # L83-84: clean stale files
        target.unlink(missing_ok=True)
        converted_source.unlink(missing_ok=True)

        # L86-97: try/finally with rename
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

        # L98-102: check target exists
        if not target.exists():
            log("generate_cases", "output_missing", platform=platform, target=str(target))
            return {"ok": False, "error": f"Final case JSON was not produced for platform={platform}, operator={generator.operator_name}"}

        # L103-105: validate payload type
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            log("generate_cases", "payload_not_list", platform=platform)
            return {"ok": False, "error": f"Converted case payload is not a list: {target}"}

        # L106-107: record results
        per_platform_paths[platform] = str(target)
        per_platform_counts[platform] = len(payload)
        log_elapsed("generate_cases", "generate_platform_done", t0, platform=platform, case_count=len(payload))

    # --- Strictly aligned with scripts/generate_cases.py:main (L181-202) ---
    summary = {
        "operator_name": generator.operator_name,
        "requested_per_platform": count,
        "platforms": per_platform_counts,
        "per_platform_files": per_platform_paths,
        "jsonl_checkpoint_files": checkpoint_paths,
        "jsonl_checkpoint_status": "converted_and_removed",
        "total": sum(per_platform_counts.values()),
        "seed": seed,
        "generator_version": (
            "opci.agent.generators.facade.TestCaseGenerator -> "
            "opci.agent.generators.operator_handle_main.single_operator_handle"
        ),
        "id_format": "platform 内 0 基整数 (per-platform 0,1,2,...)",
    }
    (output_dir / "generation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log_elapsed("generate_cases", "done", t0, total=summary["total"])
    return {"ok": True, **summary}


def validate_cases(path: str) -> dict[str, Any]:
    """Validate cases.json structure."""
    t0 = time.monotonic()
    log("validate_cases", "start", path=path)
    file_path = Path(path).resolve()
    if not file_path.is_file():
        log("validate_cases", "file_not_found", path=path)
        return {"valid": False, "errors": [f"File not found: {path}"]}
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
        log("validate_cases", "json_parsed", type=str(type(value)), size=file_path.stat().st_size)
        errors = _validate_cases(value)
        log_elapsed("validate_cases", "done", t0, valid=not errors, error_count=len(errors))
    except Exception as exc:
        log("validate_cases", "exception", error=str(exc))
        errors = [str(exc)]
    return {"valid": not errors, "errors": errors}
