"""analysis MCP tool: validate_analysis."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from opci.mcp._logging import log, log_elapsed
from opci.mcp._shared import validate_analysis as _validate_analysis


def validate_analysis(path: str) -> dict[str, Any]:
    """Validate analysis.json."""
    t0 = time.monotonic()
    log("validate_analysis", "start", path=path)
    file_path = Path(path).resolve()
    if not file_path.is_file():
        log("validate_analysis", "file_not_found", path=path)
        return {"valid": False, "errors": [f"File not found: {path}"]}
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
        log("validate_analysis", "json_parsed", root_cause=value.get("root_cause"))
        errors = _validate_analysis(value)
        log_elapsed("validate_analysis", "done", t0, valid=not errors, error_count=len(errors))
    except Exception as exc:
        log("validate_analysis", "exception", error=str(exc))
        errors = [str(exc)]
    return {"valid": not errors, "errors": errors}
