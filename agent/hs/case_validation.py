"""Concrete, post-generation semantic checks for HS cases."""
from __future__ import annotations

from collections import Counter
from typing import Any

from .constraint_evaluator import evaluate_case_relations
from .scenario_planner import (
    classify_case_scenario, hs_coverage_domains, plan_hs_scenarios,
)


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


def _exact_tensor_value(items: dict[str, dict[str, Any]], name: str) -> int | None:
    value = (items.get(name) or {}).get("range_values")
    if isinstance(value, int):
        return value
    if (
        isinstance(value, list) and len(value) == 2
        and isinstance(value[0], int) and value[0] == value[1]
    ):
        return value[0]
    return None


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
    if q and len(q) >= 2 and q[-2] not in {1, 2, 4, 8, 16, 32, 64, 128}:
        issues.append(f"query head count {q[-2]} is outside the documented domain")
    for name, shape in (("key", k), ("value", v)):
        if shape and len(shape) >= 2 and shape[-2] != 1:
            issues.append(f"{name} KV head count {shape[-2]} must be 1")
    if sparse and len(sparse) >= 2 and sparse[-2] != 1:
        issues.append(f"sparse_indices KV head count {sparse[-2]} must be 1")
    block_table = items.get("block_table")
    if layout_kv == "PA_BSND":
        if _absent(block_table):
            issues.append("PA_BSND requires block_table")
        elif len(_shape(items, "block_table") or []) != 2:
            issues.append("block_table must be rank 2")
        elif q and (_shape(items, "block_table") or [None])[0] != q[0]:
            issues.append("block_table.shape[0] must equal query batch")
        if _absent(items.get("actual_seq_lengths_kv")):
            issues.append("PA_BSND requires actual_seq_lengths_kv")
        if k and len(k) > 1 and (k[1] % 16 != 0 or k[1] > 1024):
            issues.append("PA_BSND key block_size must be a multiple of 16 and <= 1024")
        sparse_block_size = _attr(items, "sparse_block_size")
        if (
            k and len(k) > 1 and isinstance(sparse_block_size, int)
            and sparse_block_size > 0 and k[1] % sparse_block_size != 0
        ):
            issues.append("sparse_block_size must divide PA block_size")
    elif layout_kv in {"BSND", "TND"} and not _absent(block_table):
        issues.append(f"{layout_kv} requires block_table to be absent")
    if layout_q == "TND" and _absent(items.get("actual_seq_lengths_query")):
        issues.append("TND requires actual_seq_lengths_query")
    if layout_kv == "TND" and _absent(items.get("actual_seq_lengths_kv")):
        issues.append("TND requires actual_seq_lengths_kv")
    if layout_q == "BSND" and layout_kv == "BSND" and q and k and q[0] != k[0]:
        issues.append("non-PA BSND query/key batch dimensions must match")
    if layout_q == "TND" and q:
        actual_q = _exact_tensor_value(items, "actual_seq_lengths_query")
        if actual_q is not None and actual_q != q[0]:
            issues.append("TND actual_seq_lengths_query must end at query token count")
    if layout_kv == "TND" and k:
        actual_kv = _exact_tensor_value(items, "actual_seq_lengths_kv")
        if actual_kv is not None and actual_kv != k[0]:
            issues.append("TND actual_seq_lengths_kv must end at key token count")
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
    output = _shape(items, "out")
    query = _shape(items, "query")
    if output and query and output != query:
        issues.append(f"out.shape must equal query.shape: {output} != {query}")
    return issues


def validate_hs_cases(
    cases: list[dict[str, Any]], constraints: dict[str, Any],
    platform: str | None = None,
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
        issues.extend(evaluate_case_relations(case, constraints, platform))
        audit.append({"id": case.get("id", index), "issues": sorted(set(issues))})

    scenarios = Counter(classify_case_scenario(case) for case in cases)
    planned = plan_hs_scenarios(constraints, len(cases), platform)
    missing_scenarios = [
        item.name for item in planned
        if item.name != "default" and scenarios[item.name] == 0
    ]
    expected_domains = hs_coverage_domains(constraints, platform)
    observed_domains: dict[str, set[Any]] = {
        name: set() for name in expected_domains
    }
    for case in cases:
        items = _items(case)
        query = items.get("query") or {}
        query_shape = _shape(items, "query")
        if query.get("dtype"):
            raw_dtype = str(query["dtype"]).lower()
            observed_domains.get("query_dtype", set()).add(
                {"float16": "fp16", "bfloat16": "bf16"}.get(raw_dtype, raw_dtype)
            )
        if query_shape and len(query_shape) >= 2:
            observed_domains.get("query_heads", set()).add(query_shape[-2])
        for name in ("sparse_block_size", "sparse_mode"):
            value = _attr(items, name)
            if value is not None:
                observed_domains.get(name, set()).add(value)
        if _attr(items, "layout_kv") == "PA_BSND":
            key_shape = _shape(items, "key")
            if key_shape and len(key_shape) > 1:
                observed_domains.get("pa_block_size", set()).add(key_shape[1])
    domain_coverage = {
        name: {
            "expected": list(expected),
            "observed": sorted(observed_domains.get(name, set())),
            "missing": [value for value in expected if value not in observed_domains.get(name, set())],
        }
        for name, expected in expected_domains.items()
    }
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
        "domain_coverage": domain_coverage,
        "domain_coverage_complete": all(
            not item["missing"] for item in domain_coverage.values()
        ),
    }
