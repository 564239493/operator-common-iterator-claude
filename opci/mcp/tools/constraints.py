"""constraints MCP tools: normalize_constraints, validate_constraints."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from opci.mcp._logging import log, log_elapsed
from opci.mcp._shared import normalize_constraints as _normalize_constraints
from opci.mcp._shared import validate_constraints as _validate_constraints


def normalize_constraints(path: str) -> dict[str, Any]:
    """Normalize constraints.json in-place (tensor format, dtype, dimensions)."""
    t0 = time.monotonic()
    log("normalize_constraints", "start", path=path)
    file_path = Path(path).resolve()
    if not file_path.is_file():
        log("normalize_constraints", "file_not_found", path=path)
        return {"ok": False, "error": f"File not found: {path}"}

    log("normalize_constraints", "read_file", resolved=str(file_path), size=file_path.stat().st_size)
    value: dict[str, Any] = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        log("normalize_constraints", "invalid_type", type=str(type(value)))
        return {"ok": False, "error": "constraints must be a JSON object"}

    log("normalize_constraints", "normalize_start", keys=list(value.keys())[:5])
    normalized_count = _normalize_constraints(value)
    log_elapsed("normalize_constraints", "normalize_done", t0, normalized=normalized_count)

    file_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log_elapsed("normalize_constraints", "done", t0, normalized=normalized_count, path=str(file_path))
    return {"ok": True, "normalized": normalized_count, "path": str(file_path)}


def validate_constraints(path: str) -> dict[str, Any]:
    """Validate constraints.json structure and semantics."""
    t0 = time.monotonic()
    log("validate_constraints", "start", path=path)
    file_path = Path(path).resolve()
    if not file_path.is_file():
        log("validate_constraints", "file_not_found", path=path)
        return {"valid": False, "errors": [f"File not found: {path}"]}

    log("validate_constraints", "read_file", resolved=str(file_path), size=file_path.stat().st_size)
    try:
        value: dict[str, Any] = json.loads(file_path.read_text(encoding="utf-8"))
        log("validate_constraints", "json_parsed", keys=list(value.keys())[:5])

        log("validate_constraints", "validate_start")
        errors = _validate_constraints(value, _log_step=True)
        log_elapsed("validate_constraints", "validate_done", t0, error_count=len(errors))
    except Exception as exc:
        log("validate_constraints", "exception", error=str(exc))
        errors = [str(exc)]

    result = {"valid": not errors, "errors": errors}
    log_elapsed("validate_constraints", "done", t0, valid=result["valid"])
    return result
