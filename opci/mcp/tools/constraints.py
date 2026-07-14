"""constraints MCP tools: normalize_constraints, validate_constraints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opci.mcp._shared import normalize_constraints as _normalize_constraints
from opci.mcp._shared import validate_constraints as _validate_constraints


def normalize_constraints(path: str) -> dict[str, Any]:
    """Normalize constraints.json in-place (tensor format, dtype, dimensions)."""
    file_path = Path(path).resolve()
    if not file_path.is_file():
        return {"ok": False, "error": f"File not found: {path}"}

    value: dict[str, Any] = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return {"ok": False, "error": "constraints must be a JSON object"}

    normalized_count = _normalize_constraints(value)
    file_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"ok": True, "normalized": normalized_count, "path": str(file_path)}


def validate_constraints(path: str) -> dict[str, Any]:
    """Validate constraints.json structure and semantics."""
    file_path = Path(path).resolve()
    if not file_path.is_file():
        return {"valid": False, "errors": [f"File not found: {path}"]}

    try:
        value: dict[str, Any] = json.loads(file_path.read_text(encoding="utf-8"))
        errors = _validate_constraints(value)
    except Exception as exc:
        errors = [str(exc)]

    return {"valid": not errors, "errors": errors}
