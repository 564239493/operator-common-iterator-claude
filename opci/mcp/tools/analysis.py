"""analysis MCP tool: validate_analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opci.mcp._shared import validate_analysis as _validate_analysis


def validate_analysis(path: str) -> dict[str, Any]:
    """Validate analysis.json."""
    file_path = Path(path).resolve()
    if not file_path.is_file():
        return {"valid": False, "errors": [f"File not found: {path}"]}
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
        errors = _validate_analysis(value)
    except Exception as exc:
        errors = [str(exc)]
    return {"valid": not errors, "errors": errors}
