"""Convert this project's compact ATK cases to current TTK E2E CSV cases.

The current ops-test-kit format is selected by an ``api_name`` column.  A
``torch_npu.*`` API is therefore an E2E case, not the legacy
``input_desc|dtype|format`` ACLNN CSV format.
"""
from __future__ import annotations

import argparse
import ast
import csv
from copy import deepcopy
import json
from pathlib import Path
from typing import Any


DTYPES = {
    "bf16": "bfloat16",
    "fp16": "float16",
    "float16": "float16",
    "fp32": "float32",
    "float32": "float32",
    "float": "float32",
    "int8": "int8",
    # The source generator represents packed INT4 storage as int4; torch/TTK
    # must allocate its documented int32 carrier tensor.
    "int4": "int32",
    "int32": "int32",
    "int64": "int64",
    "uint8": "uint8",
    "uint64": "uint64",
    "bool": "bool",
}

_DTYPE_BOUNDS = {
    "int8": (-128, 127),
    "uint8": (0, 255),
    "int32": (-2_147_483_648, 2_147_483_647),
    "uint32": (0, 4_294_967_295),
    "int64": (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
    "uint64": (0, 18_446_744_073_709_551_615),
}


def _clamp_range_to_dtype(dtype: str, lo: Any, hi: Any) -> tuple[Any, Any]:
    """Clamp both endpoints to an integer dtype's representable bounds.

    This is TTK adapter defence in depth.  The pre-conversion audit still
    reports the original invalid range, so clamping cannot silently turn an
    invalid compact case into a clean one.
    """
    bounds = _DTYPE_BOUNDS.get(dtype)
    if bounds is None:
        return lo, hi
    try:
        new_lo = None if lo is None else max(lo, bounds[0])
        new_hi = None if hi is None else min(hi, bounds[1])
    except TypeError:
        return lo, hi
    return new_lo, new_hi

HEADERS = (
    "testcase_name",
    "api_name",
    "tensor_view_shapes",
    "tensor_dtypes",
    "tensor_formats",
    "tensor_storage_shapes",
    "tensor_view_offsets",
    "tensor_view_strides",
    "attributes",
    "input_data_ranges",
    "precision_tolerances",
    "absolute_precision",
    "golden_api",
    "output_tensor_indexes",
    "is_enabled",
    "soc_series",
    "priority",
    "remark",
)


def _tuple_repr(items: list[Any]) -> str:
    return repr(tuple(items))


def _output_names(case: dict[str, Any]) -> set[str]:
    value = case.get("outputs") or ""
    if isinstance(value, str):
        return {part.strip() for part in value.split(",") if part.strip()}
    return set(value)


def _attr_value(inp: dict[str, Any], case_id: int = 0) -> Any:
    value = inp.get("range_values")
    if inp.get("type") == "attrs":
        length = int(inp.get("length") or 0)
        if length <= 0:
            return []
        if isinstance(value, list) and len(value) == length:
            return value
        # FIA accepts a single sequence length and broadcasts it to all batches.
        # Keeping one value also avoids CSV fields larger than Python csv's
        # 128-KiB parser limit when a generated case contains length=65534.
        return [value]
    # ATK samples scalar ranges/domains during execution; TTK attributes must
    # already be concrete. Select deterministically so replay remains stable.
    if isinstance(value, list):
        if not value:
            return None
        return value[case_id % len(value)]
    return value


def _data_range(inp: dict[str, Any]) -> tuple[Any, Any]:
    value = inp.get("range_values")
    dtype = (inp.get("dtype") or "").lower()
    if isinstance(value, list) and len(value) == 2:
        lo, hi = value[0], value[1]
    elif value is None:
        # Present tensor with no explicit data range (TTK generates data at
        # runtime). Emit a concrete benign range instead of (None, None) so the
        # CSV field stays numeric.
        lo, hi = 0, 0
    else:
        lo, hi = value, value
    # Defence in depth: keep ``(lo, hi)`` inside the dtype's inclusive upper
    # bound. Float and unknown dtypes pass through unchanged.
    return _clamp_range_to_dtype(dtype, lo, hi)


def _is_absent_tensor(inp: dict[str, Any]) -> bool:
    # A tensor is "absent" (passed as None to the API) only when it cannot be
    # allocated: it has no concrete shape, or its value is an explicit null.
    #
    # Previously any tensor with ``range_values is None`` was treated as absent.
    # That wrongly collapsed REQUIRED tensors (e.g. sparse_indices, block_table,
    # actual_seq_lengths_*) — which merely lack an explicit data range, not a
    # value — to a ``None`` view shape, producing empty tensors on device
    # (``RuntimeError: Tensor sparse_indices is empty``). Shape presence, not
    # range_values, is the correct absence signal.
    value = inp.get("range_values")
    explicit_null = value == "null" or (
        isinstance(value, list) and len(value) == 1 and value[0] in (None, "null")
    )
    shape = inp.get("shape")
    has_shape = isinstance(shape, list) and len(shape) > 0
    return explicit_null or not has_shape


def _precision_policy(api_name: str, num_outputs: int = 1) -> tuple[str, str]:
    # MLA Prolog V3 returns 5 outputs (query_out, query_rope_out, and three
    # reserved empty tensors). TTK's resolve_tolerance indexes
    # absolute_precision/precision_tolerances per output; a bare scalar or a
    # single-element ((0.02, 0.001),) tuple is wrapped to a length-1 container
    # and raises IndexError on outputs 1..4. Provide one entry per output.
    if api_name == "torch_npu.npu_mla_prolog_v3":
        # The API currently has five outputs. Prefer the parsed output count,
        # but retain five as the safe fallback for legacy cases without an
        # ``outputs`` field.
        rtol, atol, abs_prec = 0.02, 0.001, 0.02
        n = num_outputs if num_outputs > 1 else 5
        ptol_parts = ", ".join([f"({rtol}, {atol})"] * n)
        atol_parts = ", ".join([str(abs_prec)] * n)
        return f"({ptol_parts})", f"({atol_parts})"
    # For multi-output operators (e.g. sparse_flash_attention returns
    # attention_out + softmax_max + softmax_sum), provide one tolerance
    # entry per output so the TTK framework can index them.
    if num_outputs > 1:
        rtol, atol = 0.005, 0.001
        ptol_parts = ", ".join([f"({rtol}, {atol})"] * num_outputs)
        atol_parts = ", ".join([str(atol)] * num_outputs)
        return f"({ptol_parts})", f"({atol_parts})"
    return "((0.005, 0.001),)", "0.005"


def _ordered_input_tensor_names(constraints: dict[str, Any]) -> list[str]:
    """Signature-ordered names of every Tensor-typed input parameter.

    TTK e2e maps ``tensor_view_shapes`` positionally onto the API's tensor
    parameters (``info.tensors``), so the CSV must carry a slot for EVERY
    documented Tensor input — including optional ones the generator drops when
    they resolve to None (e.g. reserved ``*_dequant_scale``). Missing a slot
    shifts every later tensor left (observed: block_table receives a 1-D
    actual_seq_lengths tensor -> ``EZ9999 block_table dim is 1``).
    """
    names: list[str] = []
    inputs = constraints.get("inputs") or {}
    if not isinstance(inputs, dict):
        return names
    for name, per_platform in inputs.items():
        if not isinstance(per_platform, dict):
            continue
        # per_platform maps platform -> field dict; any platform's type suffices.
        first = next(iter(per_platform.values()), None)
        if not isinstance(first, dict):
            continue
        type_field = first.get("type")
        type_value = type_field.get("value") if isinstance(type_field, dict) else type_field
        if type_value and "Tensor" in str(type_value):
            names.append(name)
    return names


def convert_case(case: dict[str, Any], platform: str = "",
                 tensor_order: list[str] | None = None) -> dict[str, str]:
    outputs = _output_names(case)
    present = {
        item.get("name"): item
        for item in case.get("inputs", [])
        if item.get("type") in ("tensor", "tensors") and item.get("name") not in outputs
    }
    # When the full signature tensor order is known, iterate it so absent
    # optional tensors become explicit None placeholder slots (keeping
    # positional alignment with the API's tensor params). Otherwise fall back
    # to the tensors present in the case.
    if tensor_order:
        ordered = [(name, present.get(name)) for name in tensor_order if name not in outputs]
    else:
        ordered = [
            (item.get("name"), item)
            for item in case.get("inputs", [])
            if item.get("type") == "tensor" and item.get("name") not in outputs
        ]

    view_shapes: list[Any] = []
    dtypes: list[Any] = []
    formats: list[Any] = []
    data_ranges: list[Any] = []
    for _name, item in ordered:
        if item is None or _is_absent_tensor(item):
            # Absent optional tensor -> None view shape (TTK passes None to the
            # API and does not read the dtype). Keep the other tuples aligned
            # with a benign placeholder so all four columns stay the same length.
            view_shapes.append(None)
            dtypes.append("float16")
            formats.append("ND")
            data_ranges.append((0, 0))
            continue
        else:
            view_shapes.append(tuple(item.get("shape") or ()))
            dtypes.append(DTYPES[item["dtype"].lower()])
            formats.append(item.get("format") or "ND")
            data_ranges.append(_data_range(item))

    case_id = int(case.get("id", 0))
    attrs: dict[str, Any] = {}
    for item in case.get("inputs", []):
        kind = item.get("type")
        if kind in {"attr", "attrs"}:
            attr_value = _attr_value(item, case_id)
            # Omit optional params whose value resolves to None. Emitting an
            # explicit None makes TTK treat the slot as a tensor-like input and
            # reject it (input_generation.override_tensors_from_attributes:
            # "has a tensor in view_shapes but attributes specifies None").
            # Absent-from-attributes correctly lets the API use its default.
            if attr_value is None:
                continue
            attrs[item["name"]] = attr_value

    api_name = case.get("name") or case.get("aclnn_name")
    num_outputs = len(_output_names(case))
    precision_tolerances, absolute_precision = _precision_policy(api_name, num_outputs)
    return {
        "testcase_name": f"{api_name.replace('.', '_')}_{case_id:03d}",
        "api_name": api_name,
        "tensor_view_shapes": _tuple_repr(view_shapes),
        "tensor_dtypes": _tuple_repr(dtypes),
        "tensor_formats": _tuple_repr(formats),
        "tensor_storage_shapes": "()",
        "tensor_view_offsets": "()",
        "tensor_view_strides": "()",
        "attributes": repr(attrs),
        "input_data_ranges": _tuple_repr(data_ranges),
        "precision_tolerances": precision_tolerances,
        "absolute_precision": absolute_precision,
        "golden_api": "",
        "output_tensor_indexes": "()",
        "is_enabled": "True",
        "soc_series": "('Ascend910B',)" if "A2" in platform else "",
        "priority": "0",
        "remark": "mechanically converted from compact ATK case; semantic audit required",
    }


def audit_common_case(case: dict[str, Any]) -> list[str]:
    """Framework-neutral checks that catch unsafe concrete cases early."""
    issues: list[str] = []
    for item in case.get("inputs", []):
        dtype = str(item.get("dtype", "")).lower()
        value = item.get("range_values")
        bounds = _DTYPE_BOUNDS.get(dtype)
        if bounds:
            values = value if isinstance(value, list) else [value]
            invalid = [
                candidate for candidate in values
                if isinstance(candidate, (int, float))
                and not bounds[0] <= candidate <= bounds[1]
            ]
            if invalid:
                issues.append(
                    f"{item.get('name')}: data range {value!r} exceeds {dtype} bounds {bounds}"
                )
        shape = item.get("shape") or []
        elements = 1
        for dim in shape:
            elements *= dim
        if elements > 500_000_000:
            issues.append(f"{item.get('name')}: shape has {elements} elements and is unsafe to allocate")
    return issues


def audit_fia_case(case: dict[str, Any]) -> list[str]:
    """FIA-specific semantic checks in addition to common safety checks."""
    by_name = {item.get("name"): item for item in case.get("inputs", [])}
    issues = audit_common_case(case)
    layout = by_name.get("input_layout", {}).get("range_values")
    if layout not in {"BSH", "BSND", "BNSD", "TND", "NSD", "BNSD_BSND"}:
        issues.append(f"input_layout: unsupported value {layout!r}")
    for name in ("query", "key", "value"):
        item = by_name.get(name, {})
        rank = len(item.get("shape") or [])
        if rank not in {3, 4}:
            issues.append(f"{name}: rank {rank} is incompatible with BSH/BSND/BNSD layouts")
    query = by_name.get("query", {})
    pse = by_name.get("pse_shift", {})
    if pse and query.get("dtype") != pse.get("dtype"):
        issues.append("pse_shift dtype must match query dtype")
    return issues


def _fixed_tensor_value(item: dict[str, Any] | None) -> Any:
    if not item:
        return None
    value = item.get("range_values")
    if isinstance(value, list) and len(value) == 2 and value[0] == value[1]:
        return value[0]
    if not isinstance(value, list):
        return value
    return None


def audit_kv_quant_ttk_case(case: dict[str, Any]) -> list[str]:
    """Reject content semantics that TTK's range-only CSV cannot guarantee."""
    by_name = {item.get("name"): item for item in case.get("inputs", [])}
    issues = audit_common_case(case)
    layout_q = (by_name.get("layout_query") or {}).get("range_values")
    layout_kv = (by_name.get("layout_kv") or {}).get("range_values")
    query_shape = (by_name.get("query") or {}).get("shape") or []
    key_shape = (by_name.get("key") or {}).get("shape") or []

    # A multi-element prefix sum cannot be represented by one random range in
    # the current TTK E2E CSV.  Require the exact B=1 construction until TTK
    # gains literal tensor data/builders.
    for name, required, terminal in (
        ("actual_seq_lengths_query", layout_q == "TND", query_shape[0] if query_shape else None),
        ("actual_seq_lengths_kv", layout_kv == "TND",
         (key_shape[0] if layout_kv == "TND" and key_shape else None)),
    ):
        item = by_name.get(name)
        if not required:
            continue
        if not item or item.get("shape") != [1]:
            issues.append(
                f"{name}: current TTK range-only adapter requires exact B=1 data"
            )
            continue
        exact = _fixed_tensor_value(item)
        if exact is None:
            issues.append(f"{name}: current TTK adapter requires a fixed exact value")
        elif terminal is not None and exact != terminal:
            issues.append(f"{name}: exact value {exact} must equal terminal token count {terminal}")

    sparse = by_name.get("sparse_indices")
    sparse_value = _fixed_tensor_value(sparse)
    if sparse and sparse_value is None:
        issues.append(
            "sparse_indices: range-only random data cannot guarantee valid-before-invalid ordering"
        )
    if layout_kv == "PA_BSND":
        sparse_block_size = (
            by_name.get("sparse_block_size") or {}
        ).get("range_values")
        block_size = key_shape[1] if len(key_shape) > 1 else None
        if not isinstance(sparse_block_size, int) or sparse_block_size <= 0:
            issues.append(
                "sparse_block_size: PA requires a positive integer"
            )
        elif isinstance(block_size, int) and block_size % sparse_block_size != 0:
            issues.append(
                "sparse_block_size: PA key block_size "
                f"{block_size} must be divisible by {sparse_block_size}"
            )
        actual_kv = by_name.get("actual_seq_lengths_kv") or {}
        expected_batch = query_shape[0] if query_shape else None
        if expected_batch is not None and actual_kv.get("shape") != [expected_batch]:
            issues.append(
                "actual_seq_lengths_kv: PA shape must be [query batch]"
            )
        actual_kv_value = _fixed_tensor_value(actual_kv)
        if actual_kv_value is None:
            issues.append(
                "actual_seq_lengths_kv: PA range-only adapter requires a fixed value"
            )
        block_table = by_name.get("block_table") or {}
        block_num = key_shape[0] if key_shape else 0
        values = block_table.get("range_values")
        valid_scalar = isinstance(values, int) and 0 <= values < block_num
        valid_range = (
            isinstance(values, list) and len(values) == 2
            and all(isinstance(value, int) for value in values)
            and 0 <= values[0] <= values[1] < block_num
        )
        if not (valid_scalar or valid_range):
            issues.append(
                "block_table: PA random range must stay inside [0, block_num-1]"
            )
        block_shape = block_table.get("shape") or []
        if (
            isinstance(actual_kv_value, int) and len(key_shape) > 1
            and len(block_shape) > 1
            and not (0 <= actual_kv_value <= block_shape[1] * key_shape[1])
        ):
            issues.append(
                "actual_seq_lengths_kv: PA fixed value exceeds block-table capacity"
            )
    return issues


def _set_tensor_case_value(
    by_name: dict[str, dict[str, Any]], name: str,
    *, shape: list[int], value: int,
) -> bool:
    item = by_name.get(name)
    if not item:
        return False
    changed = item.get("shape") != shape or item.get("range_values") != value
    item["shape"] = list(shape)
    item["range_values"] = value
    item["required"] = True
    return changed


def materialize_sparse_attention_ttk_cases(
    cases: list[dict[str, Any]], *, safe_token_cap: int = 4096,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Make npu_sparse_flash_attention cases representable by range-only TTK.

    The generic generator describes tensor contents as one scalar/range.  It
    therefore cannot materialize multi-batch prefix sums or ordered sparse
    indices.  For the functional smoke path, use an exact B=1 TND prefix sum,
    fixed valid sparse index 0, and a valid PA block-table entry.  This is an
    adapter repair, not a new operator constraint or a scenario-planner mode.
    """
    repaired = deepcopy(cases)
    audit: list[dict[str, Any]] = []
    for index, case in enumerate(repaired):
        api_name = case.get("name") or case.get("aclnn_name")
        if api_name != "torch_npu.npu_sparse_flash_attention":
            audit.append({"id": case.get("id", index), "changes": []})
            continue
        by_name = {
            item.get("name"): item for item in case.get("inputs", [])
            if isinstance(item, dict) and item.get("name")
        }
        changes: list[str] = []
        layout_q = (by_name.get("layout_query") or {}).get("range_values")
        layout_kv = (by_name.get("layout_kv") or {}).get("range_values")
        query = by_name.get("query") or {}
        key = by_name.get("key") or {}
        query_shape = list(query.get("shape") or [])
        key_shape = list(key.get("shape") or [])

        # Bound generated positive smoke tensors without claiming a universal
        # operator limit.  Keep all TND-correlated first axes aligned.
        if layout_q == "TND" and query_shape:
            q_tokens = min(max(int(query_shape[0]), 1), safe_token_cap)
            if q_tokens != query_shape[0]:
                query_shape[0] = q_tokens
                query["shape"] = query_shape
                changes.append(f"query T capped to {q_tokens}")
            for name in ("query_rope", "attention_out"):
                shape = list((by_name.get(name) or {}).get("shape") or [])
                if shape and shape[0] != q_tokens:
                    shape[0] = q_tokens
                    by_name[name]["shape"] = shape
                    changes.append(f"{name}.shape[0]={q_tokens}")
            for name in ("softmax_max", "softmax_sum"):
                shape = list((by_name.get(name) or {}).get("shape") or [])
                if len(shape) > 1 and shape[1] != q_tokens:
                    shape[1] = q_tokens
                    by_name[name]["shape"] = shape
                    changes.append(f"{name}.shape[1]={q_tokens}")
            if _set_tensor_case_value(
                by_name, "actual_seq_lengths_query", shape=[1], value=q_tokens,
            ):
                changes.append("actual_seq_lengths_query exact B=1 prefix sum")
        elif layout_q == "BSND" and len(query_shape) >= 2:
            batch, seq = int(query_shape[0]), max(int(query_shape[1]), 0)
            if _set_tensor_case_value(
                by_name, "actual_seq_lengths_query", shape=[batch], value=seq,
            ):
                changes.append("actual_seq_lengths_query bounded to query S")

        if layout_kv == "TND" and key_shape:
            kv_tokens = min(max(int(key_shape[0]), 1), safe_token_cap)
            if kv_tokens != key_shape[0]:
                key_shape[0] = kv_tokens
                key["shape"] = key_shape
                changes.append(f"key T capped to {kv_tokens}")
            for name in ("value", "key_rope"):
                shape = list((by_name.get(name) or {}).get("shape") or [])
                if shape and shape[0] != kv_tokens:
                    shape[0] = kv_tokens
                    by_name[name]["shape"] = shape
                    changes.append(f"{name}.shape[0]={kv_tokens}")
            if _set_tensor_case_value(
                by_name, "actual_seq_lengths_kv", shape=[1], value=kv_tokens,
            ):
                changes.append("actual_seq_lengths_kv exact B=1 prefix sum")
        elif layout_kv == "BSND" and len(key_shape) >= 2:
            batch, seq = int(key_shape[0]), max(int(key_shape[1]), 0)
            if _set_tensor_case_value(
                by_name, "actual_seq_lengths_kv", shape=[batch], value=seq,
            ):
                changes.append("actual_seq_lengths_kv bounded to key S")
        elif layout_kv == "PA_BSND" and len(key_shape) >= 2:
            batch = int(query_shape[0]) if query_shape else 1
            block_size = max(int(key_shape[1]), 1)
            seq = min(block_size, safe_token_cap)
            sparse_block = by_name.get("sparse_block_size")
            if sparse_block is None:
                sparse_block = {
                    "name": "sparse_block_size", "type": "attr",
                    "required": True, "dtype": "int64", "shape": None,
                    "range_values": None, "length": None, "format": None,
                    "backward": False, "align_32B": None,
                    "outlier_values": None,
                }
                case.setdefault("inputs", []).append(sparse_block)
                by_name["sparse_block_size"] = sparse_block
            requested_sparse_size = sparse_block.get("range_values")
            if (
                not isinstance(requested_sparse_size, int)
                or requested_sparse_size <= 0
                or block_size % requested_sparse_size != 0
            ):
                documented_sizes = (1, 2, 4, 8, 16, 32, 64, 128)
                compatible = [
                    value for value in documented_sizes
                    if block_size % value == 0
                ]
                bounded = [
                    value for value in compatible
                    if isinstance(requested_sparse_size, int)
                    and value <= requested_sparse_size
                ]
                repaired_sparse_size = max(bounded or compatible)
                sparse_block["range_values"] = repaired_sparse_size
                changes.append(
                    "PA sparse_block_size repaired from "
                    f"{requested_sparse_size!r} to divisor {repaired_sparse_size} "
                    f"of block_size {block_size}"
                )
            if _set_tensor_case_value(
                by_name, "actual_seq_lengths_kv", shape=[batch], value=seq,
            ):
                changes.append("PA actual_seq_lengths_kv set to one valid block")
            if _set_tensor_case_value(
                by_name, "block_table", shape=[batch, 1], value=0,
            ):
                changes.append("PA block_table fixed to valid block 0")

        # A constant valid index has no invalid tail, so it satisfies the
        # documented valid-before-invalid ordering without guessing a sentinel.
        sparse = by_name.get("sparse_indices") or {}
        sparse_shape = list(sparse.get("shape") or [])
        sparse_size = max(int(sparse_shape[-1]), 1) if sparse_shape else 1
        if layout_q == "TND" and query_shape:
            target = [int(query_shape[0]), 1, sparse_size]
        elif layout_q == "BSND" and len(query_shape) >= 2:
            target = [int(query_shape[0]), int(query_shape[1]), 1, sparse_size]
        else:
            target = sparse_shape
        if target and _set_tensor_case_value(
            by_name, "sparse_indices", shape=target, value=0,
        ):
            changes.append("sparse_indices fixed to valid index 0 with KV_N=1")

        audit.append({"id": case.get("id", index), "changes": changes})
    return repaired, {
        "operator": "torch_npu.npu_sparse_flash_attention",
        "mode": "range_only_functional_smoke_repair",
        "safe_token_cap": safe_token_cap,
        "changed_case_count": sum(bool(entry["changes"]) for entry in audit),
        "audit": audit,
    }


def materialize_mla_prolog_v3_ttk_cases(
    cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project original-mode MLA samples onto one documented A2 smoke scene.

    MLA's mode tuple, dtype matrix, optional tensors, ranks and formats form a
    single correlated scenario.  The retained generic generator intentionally
    remains unchanged and can sample those axes independently.  For the
    current functional-first TTK path, materialize the documented unquantized
    PA_BSND BF16 baseline after generation while preserving the raw platform
    cases for audit.
    """
    repaired = deepcopy(cases)
    audit: list[dict[str, Any]] = []

    def tensor(
        name: str, shape: list[int], value: Any, *, fmt: str = "ND",
        dtype: str = "bf16",
    ) -> dict[str, Any]:
        return {
            "name": name, "type": "tensor", "required": True,
            "dtype": dtype, "shape": shape, "range_values": value,
            "length": None, "format": fmt, "backward": False,
            "align_32B": None, "outlier_values": None,
        }

    def attr(name: str, dtype: str, value: Any) -> dict[str, Any]:
        return {
            "name": name, "type": "attr", "required": True,
            "dtype": dtype, "shape": None, "range_values": value,
            "length": None, "format": None, "backward": False,
            "align_32B": None, "outlier_values": None,
        }

    # Smallest documented fixed dimensions keep the smoke allocation modest.
    batch, seq, he, hcq = 1, 1, 1024, 1536
    heads, nope_dim, rope_dim, hckv = 1, 128, 64, 512
    blocks, block_size = 1, 16
    baseline_inputs = [
        tensor("token_x", [batch, seq, he], [-0.125, 0.125]),
        tensor("weight_dq", [he, hcq], [-0.125, 0.125], fmt="FRACTAL_NZ"),
        tensor(
            "weight_uq_qr", [hcq, heads * (nope_dim + rope_dim)],
            [-0.125, 0.125], fmt="FRACTAL_NZ",
        ),
        tensor("weight_uk", [heads, nope_dim, hckv], [-0.125, 0.125]),
        tensor(
            "weight_dkv_kr", [he, hckv + rope_dim],
            [-0.125, 0.125], fmt="FRACTAL_NZ",
        ),
        tensor("rmsnorm_gamma_cq", [hcq], [1.0, 1.0]),
        tensor("rmsnorm_gamma_ckv", [hckv], [1.0, 1.0]),
        tensor("rope_sin", [batch, seq, rope_dim], [0.0, 0.0]),
        tensor("rope_cos", [batch, seq, rope_dim], [1.0, 1.0]),
        tensor(
            "kv_cache", [blocks, block_size, 1, hckv], [0.0, 0.0]
        ),
        tensor(
            "kr_cache", [blocks, block_size, 1, rope_dim], [0.0, 0.0]
        ),
        tensor("cache_index", [batch, seq], [0, 0], dtype="int64"),
        attr("rmsnorm_epsilon_cq", "fp32", 1e-5),
        attr("rmsnorm_epsilon_ckv", "fp32", 1e-5),
        attr("cache_mode", "string", "PA_BSND"),
        attr("query_norm_flag", "bool", False),
        attr("weight_quant_mode", "int64", 0),
        attr("kv_cache_quant_mode", "int64", 0),
        attr("query_quant_mode", "int64", 0),
        attr("ckvkr_repo_mode", "int64", 0),
        attr("quant_scale_repo_mode", "int64", 0),
        attr("tile_size", "int64", 128),
        attr("qc_qr_scale", "fp32", 1.0),
        attr("kc_scale", "fp32", 1.0),
    ]

    for index, case in enumerate(repaired):
        api_name = case.get("name") or case.get("aclnn_name")
        if api_name != "torch_npu.npu_mla_prolog_v3":
            audit.append({"id": case.get("id", index), "changes": []})
            continue
        case["id"] = index
        case["name"] = api_name
        case["aclnn_name"] = api_name
        case["outputs"] = (
            "query_out,query_rope_out,dequant_scale_q_nope,"
            "query_norm,dequant_scale_q_norm"
        )
        case["inputs"] = deepcopy(baseline_inputs)
        audit.append({
            "id": index,
            "changes": [
                "projected to documented unquantized PA_BSND BF16 baseline",
                "bound weight/kv/query quant modes to (0,0,0)",
                "materialized three weight tensors as FRACTAL_NZ",
                "removed inactive quantization tensors and dtype overrides",
                "bound token/cache/cache_index ranks to PA_BSND",
            ],
        })
    return repaired, {
        "operator": "torch_npu.npu_mla_prolog_v3",
        "mode": "documented_a2_pa_bsnd_bf16_functional_smoke",
        "source_generation_mode": "original",
        "changed_case_count": sum(bool(entry["changes"]) for entry in audit),
        "audit": audit,
    }


def audit_mla_prolog_v3_case(case: dict[str, Any]) -> list[str]:
    """Validate the adapter's functional MLA baseline as one atomic scene."""
    issues = audit_common_case(case)
    by_name = {item.get("name"): item for item in case.get("inputs", [])}
    expected_attrs = {
        "cache_mode": "PA_BSND", "weight_quant_mode": 0,
        "kv_cache_quant_mode": 0, "query_quant_mode": 0,
    }
    for name, expected in expected_attrs.items():
        actual = (by_name.get(name) or {}).get("range_values")
        if actual != expected:
            issues.append(f"{name}: expected smoke value {expected!r}, got {actual!r}")
    for name in ("weight_dq", "weight_uq_qr", "weight_dkv_kr"):
        item = by_name.get(name) or {}
        if item.get("dtype") != "bf16" or item.get("format") != "FRACTAL_NZ":
            issues.append(f"{name}: smoke baseline requires bf16 FRACTAL_NZ")
    ranks = {
        "token_x": 3, "rope_sin": 3, "rope_cos": 3,
        "kv_cache": 4, "kr_cache": 4, "cache_index": 2,
    }
    for name, expected in ranks.items():
        actual = len((by_name.get(name) or {}).get("shape") or [])
        if actual != expected:
            issues.append(f"{name}: PA_BSND smoke rank must be {expected}, got {actual}")
    return issues


def audit_case(case: dict[str, Any]) -> list[str]:
    api_name = case.get("name") or case.get("aclnn_name")
    if api_name == "torch_npu.npu_mla_prolog_v3":
        return audit_mla_prolog_v3_case(case)
    if api_name == "torch_npu.npu_fused_infer_attention_score":
        return audit_fia_case(case)
    if api_name in {
        "torch_npu.npu_kv_quant_sparse_flash_attention",
        "torch_npu.npu_sparse_flash_attention",
    }:
        return audit_kv_quant_ttk_case(case)
    return audit_common_case(case)


def self_check(rows: list[dict[str, str]], tensor_order: list[str] | None) -> list[str]:
    """Cheap post-conversion checks that catch TTK positional misalignment.

    Returns a list of human-readable warnings (empty == clean). Two invariants
    that have caused real NPU rejections in the past:
      1. All four per-tensor tuples (view_shapes / dtypes / formats / data_ranges)
         must have the same length per row; otherwise TTK rejects the CSV.
      2. If ``tensor_order`` is known, that length must equal the signature
         tensor-parameter count; otherwise downstream positional mapping will
         silently misalign.
    """
    issues: list[str] = []
    expected_len = len(tensor_order) if tensor_order else None
    for i, row in enumerate(rows):
        try:
            lens = {
                col: len(ast.literal_eval(row[col]))
                for col in ("tensor_view_shapes", "tensor_dtypes",
                            "tensor_formats", "input_data_ranges")
            }
        except (ValueError, SyntaxError) as exc:
            issues.append(f"row {i}: malformed tuple field ({exc})")
            continue
        unique = set(lens.values())
        if len(unique) != 1:
            issues.append(
                f"row {i}: per-tensor tuple lengths differ {lens} (TTK will reject)"
            )
        if expected_len is not None and lens["tensor_view_shapes"] != expected_len:
            issues.append(
                f"row {i}: tensor_view_shapes has {lens['tensor_view_shapes']} slots "
                f"but signature has {expected_len} tensor params (positional misalignment)"
            )
    return issues


def convert_file(source: Path, destination: Path, platform: str = "",
                 tensor_order: list[str] | None = None) -> dict[str, Any]:
    cases = json.loads(source.read_text(encoding="utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows = [convert_case(case, platform, tensor_order) for case in cases]
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    audit = [
        {"id": case.get("id"), "issues": audit_case(case)}
        for case in cases
    ]
    warnings = self_check(rows, tensor_order)
    return {
        "source": str(source),
        "destination": str(destination),
        "case_count": len(cases),
        "semantically_clean_count": sum(not entry["issues"] for entry in audit),
        "audit": audit,
        "self_check_warnings": warnings,
        "content_generation_mode": "range_only",
        "content_generation_limitations": [
            "multi-element prefix sums require a literal tensor builder",
            "ordered valid/invalid sparse indices require a literal tensor builder",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert compact ATK JSON to TTK E2E CSV")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--platform", default="")
    parser.add_argument("--audit-output", type=Path)
    parser.add_argument(
        "--constraints", type=Path, default=None,
        help="Optional constraints.json; used to recover the full signature "
             "tensor-parameter order so absent optional tensors get None slots.",
    )
    args = parser.parse_args()
    tensor_order = None
    if args.constraints:
        constraints = json.loads(args.constraints.read_text(encoding="utf-8"))
        tensor_order = _ordered_input_tensor_names(constraints)
    result = convert_file(args.source, args.destination, args.platform, tensor_order)
    if args.audit_output:
        args.audit_output.parent.mkdir(parents=True, exist_ok=True)
        args.audit_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
