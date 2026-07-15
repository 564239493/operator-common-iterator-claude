"""cases MCP tools: generate_cases, validate_cases."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

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

    from opci.config import get_project_root, resolve_input_path

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

    # Normalize first
    log("generate_cases", "normalize_start")
    normalized_count = _normalize_constraints_fn(constraints_data)
    log_elapsed("generate_cases", "normalize_done", t0, normalized=normalized_count)

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

    for platform in platforms:
        log("generate_cases", "generate_platform_start", platform=platform, count=count)
        sanitized = platform.replace("/", "_")
        checkpoint_dir = jsonl_save / sanitized
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        try:
            generator.generate_for_platform(
                platform,
                count,
                jsonl_save_path=str(checkpoint_dir),
                json_save_path=str(output_dir),
            )
        except Exception as exc:
            log("generate_cases", "generate_platform_error", platform=platform, error=str(exc))
            return {"ok": False, "error": f"Generation failed for platform {platform}: {exc}"}

        target = output_dir / f"cases_{sanitized}.json"
        if not target.exists():
            log("generate_cases", "output_missing", platform=platform, target=str(target))
            return {"ok": False, "error": f"Cases file not produced for platform {platform}"}

        payload = json.loads(target.read_text(encoding="utf-8"))
        per_platform_paths[platform] = str(target)
        per_platform_counts[platform] = len(payload)
        log_elapsed("generate_cases", "generate_platform_done", t0, platform=platform, case_count=len(payload))

    summary = {
        "operator_name": generator.operator_name,
        "requested_per_platform": count,
        "platforms": per_platform_counts,
        "per_platform_files": per_platform_paths,
        "total": sum(per_platform_counts.values()),
        "seed": seed,
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
