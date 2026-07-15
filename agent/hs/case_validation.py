"""Concrete, post-generation semantic checks for HS cases."""
from __future__ import annotations

from collections import Counter
from typing import Any

from .scenario_planner import classify_case_scenario, plan_hs_scenarios


_DTYPE_LIMITS = {
    "int8": (-128, 127), "uint8": (0, 255),
    "int32": (-(1 << 31), (1 << 31) - 1),
    "uint32": (0, (1 << 32) - 1),
    "int64": (-(1 << 63), (1 << 63) - 1),
    "uint64": (0, (1 << 64) - 1),
}
_ALIASES = {"fp16": "float16", "fp32": "float32", "bf16": "bfloat16"}


def _items(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item.get("name"): item
        for item in case.get("inputs", [])
        if isinstance(item, dict) and item.get("name")
    }


def _absent(item: dict[str, Any] | None) -> bool:
    if not item:
        return True
    value = item.get("range_values")
    return item.get("shape") in (None, []) or value == "null" or (
        isinstance(value, list) and len(value) == 1 and value[0] in (None, "null")
    )


def _attr(items: dict[str, dict[str, Any]], name: str) -> Any:
    item = items.get(name) or {}
    value = item.get("range_values")
    if isinstance(value, list):
        return value[0] if len(value) == 1 else None
    return value


def _shape(items: dict[str, dict[str, Any]], name: str) -> list[int] | None:
    value = (items.get(name) or {}).get("shape")
    return value if isinstance(value, list) and value else None


def _check_integer_range(item: dict[str, Any]) -> str | None:
    dtype = _ALIASES.get(str(item.get("dtype", "")).lower(), str(item.get("dtype", "")).lower())
    limits = _DTYPE_LIMITS.get(dtype)
    value = item.get("range_values")
    if not limits or value in (None, "null"):
        return None
    values = value if isinstance(value, list) else [value]
    numeric = [number for number in values if isinstance(number, (int, float))]
    if any(number < limits[0] or number > limits[1] for number in numeric):
        return f"{item.get('name')}: data range {value!r} exceeds {dtype} {limits}"
    return None


def _validate_sparse_attention_case(
    case: dict[str, Any], *, require_key_value_equal: bool = True
) -> list[str]:
    issues: list[str] = []
    items = _items(case)
    q = _shape(items, "query")
    k = _shape(items, "key")
    v = _shape(items, "value")
    sparse = _shape(items, "sparse_indices")
    layout_q = _attr(items, "layout_query")
    layout_kv = _attr(items, "layout_kv")
    if layout_q == "BSND" and q and len(q) != 4:
        issues.append(f"query rank {len(q)} must be 4 for BSND")
    if layout_q == "TND" and q and len(q) != 3:
        issues.append(f"query rank {len(q)} must be 3 for TND")
    expected_k_rank = 3 if layout_kv == "TND" else 4
    for name, shape in (("key", k), ("value", v)):
        if layout_kv in {"TND", "BSND", "PA_BSND"} and shape and len(shape) != expected_k_rank:
            issues.append(f"{name} rank {len(shape)} must be {expected_k_rank} for {layout_kv}")
    if require_key_value_equal and k and v and k != v:
        issues.append(f"key/value shapes differ: {k} != {v}")
    if q and sparse:
        expected_sparse_rank = 4 if layout_q == "BSND" else 3
        if layout_q in {"BSND", "TND"} and len(sparse) != expected_sparse_rank:
            issues.append(f"sparse_indices rank {len(sparse)} must be {expected_sparse_rank} for {layout_q}")
        if sparse[0] != q[0]:
            issues.append("sparse_indices.shape[0] must equal query.shape[0]")
        if layout_q == "BSND" and len(sparse) > 1 and sparse[1] != q[1]:
            issues.append("BSND sparse_indices.shape[1] must equal query.shape[1]")
    block_table = items.get("block_table")
    if layout_kv == "PA_BSND":
        if _absent(block_table):
            issues.append("PA_BSND requires block_table")
        elif len(_shape(items, "block_table") or []) != 2:
            issues.append("block_table must be rank 2")
    elif layout_kv in {"BSND", "TND"} and not _absent(block_table):
        issues.append(f"{layout_kv} requires block_table to be absent")
    return issues


def _validate_kv_quant_case(case: dict[str, Any]) -> list[str]:
    # Unlike npu_sparse_flash_attention, the published quantized operator
    # document does not explicitly require complete key/value shape equality.
    issues = _validate_sparse_attention_case(case, require_key_value_equal=False)
    items = _items(case)
    expected_dtypes = {
        "key": "int8", "value": "int8", "sparse_indices": "int32",
        "actual_seq_lengths_query": "int32", "actual_seq_lengths_kv": "int32",
    }
    for name, expected in expected_dtypes.items():
        item = items.get(name)
        if item and not _absent(item):
            actual = _ALIASES.get(str(item.get("dtype", "")).lower(), str(item.get("dtype", "")).lower())
            if actual != expected:
                issues.append(f"{name} dtype {actual!r} must be {expected}")
    for name, expected in {
        "key_quant_mode": 2, "value_quant_mode": 2,
        "attention_mode": 2, "quant_scale_repo_mode": 1,
        "tile_size": 128, "rope_head_dim": 64,
    }.items():
        actual = _attr(items, name)
        if actual is not None and actual != expected:
            issues.append(f"{name}={actual!r} must be {expected}")
    for name, last_dim in (("query", 576), ("key", 656), ("value", 656)):
        shape = _shape(items, name)
        if shape and shape[-1] != last_dim:
            issues.append(f"{name}.shape[-1]={shape[-1]} must be {last_dim}")
    return issues


def validate_hs_cases(
    cases: list[dict[str, Any]], constraints: dict[str, Any]
) -> dict[str, Any]:
    operator = str(constraints.get("operator_name", ""))
    audit: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        issues: list[str] = []
        for item in case.get("inputs", []):
            if isinstance(item, dict) and item.get("type") == "tensor":
                issue = _check_integer_range(item)
                if issue:
                    issues.append(issue)
        if operator == "torch_npu.npu_kv_quant_sparse_flash_attention":
            issues.extend(_validate_kv_quant_case(case))
        elif operator == "torch_npu.npu_sparse_flash_attention":
            issues.extend(_validate_sparse_attention_case(case))
        audit.append({"id": case.get("id", index), "issues": sorted(set(issues))})

    scenarios = Counter(classify_case_scenario(case) for case in cases)
    planned = plan_hs_scenarios(constraints, len(cases))
    missing_scenarios = [
        item.name for item in planned
        if item.name != "default" and scenarios[item.name] == 0
    ]
    return {
        "case_count": len(cases),
        "semantically_clean_count": sum(not item["issues"] for item in audit),
        "audit": audit,
        "scenario_counts": dict(scenarios),
        "planned_scenarios": [
            {"name": item.name, "count": item.count, "fixed_attrs": item.fixed_attrs}
            for item in planned
        ],
        "missing_scenarios": missing_scenarios,
    }
