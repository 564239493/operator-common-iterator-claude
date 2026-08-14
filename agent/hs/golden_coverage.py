"""Check whether unified concrete cases are covered by a verified CPU Golden."""
from __future__ import annotations

from typing import Any

_DTYPE_ALIASES = {"fp16": "float16", "fp32": "float32", "bf16": "bfloat16"}


def _normal(value: Any) -> Any:
    if isinstance(value, str):
        return _DTYPE_ALIASES.get(value.lower(), value)
    return value


def _is_absent(item: dict[str, Any]) -> bool:
    value = item.get("range_values")
    return value is None or value == "null" or (
        isinstance(value, list) and len(value) == 1 and value[0] in (None, "null")
    )


def audit_golden_coverage(cases: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    """Return per-case coverage; unknown mode keys deliberately fail closed."""
    modes = manifest.get("verified_modes") or {}
    verified = manifest.get("status") == "verified" and bool(modes)
    entries: list[dict[str, Any]] = []
    for case in cases:
        inputs = {item.get("name"): item for item in case.get("inputs", [])}
        reasons: list[str] = []
        if not verified:
            reasons.append("manifest is not verified")
        for key, allowed_values in modes.items():
            allowed = {_normal(value) for value in allowed_values}
            if key == "quantized":
                actual = any(
                    "quant" in str(item.get("name", "")).lower()
                    and (
                        (item.get("type") == "tensor" and not _is_absent(item))
                        or (item.get("type") in {"attr", "attrs"} and item.get("range_values") not in (None, 0, False))
                    )
                    for item in case.get("inputs", [])
                )
                if actual not in allowed:
                    reasons.append(f"quantized={actual!r} is outside {sorted(map(str, allowed))}")
                continue
            if key == "dtype":
                dtype_inputs = manifest.get("dtype_inputs") or ["query", "token_x"]
                actual = {
                    _normal(item.get("dtype"))
                    for item in case.get("inputs", [])
                    if item.get("type") == "tensor"
                    and item.get("name") in dtype_inputs
                    and not _is_absent(item)
                }
                if not actual or not actual.issubset(allowed):
                    reasons.append(f"dtype={sorted(map(str, actual))} is outside {sorted(map(str, allowed))}")
                continue
            item = inputs.get(key)
            if item is None:
                reasons.append(f"verified mode {key!r} cannot be determined")
                continue
            actual = _normal(item.get("range_values"))
            if actual not in allowed:
                reasons.append(f"{key}={actual!r} is outside {sorted(map(str, allowed))}")
        entries.append({"id": case.get("id"), "covered": not reasons, "reasons": reasons})
    covered_count = sum(entry["covered"] for entry in entries)
    return {
        "status": "verified" if cases and covered_count == len(cases) else "partial",
        "case_count": len(cases),
        "covered_count": covered_count,
        "uncovered_count": len(cases) - covered_count,
        "cases": entries,
    }
