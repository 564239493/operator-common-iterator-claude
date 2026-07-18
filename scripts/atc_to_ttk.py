"""Convert this project's compact ATK cases to current TTK E2E CSV cases.

The current ops-test-kit format is selected by an ``api_name`` column.  A
``torch_npu.*`` API is therefore an E2E case, not the legacy
``input_desc|dtype|format`` ACLNN CSV format.
"""
from __future__ import annotations

import argparse
import ast
import csv
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


def _precision_policy(api_name: str) -> tuple[str, str]:
    if api_name == "torch_npu.npu_mla_prolog_v3":
        return "((0.02, 0.001),)", "0.02"
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
    precision_tolerances, absolute_precision = _precision_policy(api_name)
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
        ("actual_seq_lengths_kv", layout_kv in {"TND", "PA_BSND"},
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
        block_table = by_name.get("block_table") or {}
        block_num = key_shape[0] if key_shape else 0
        values = block_table.get("range_values")
        if not (
            isinstance(values, list) and len(values) == 2
            and all(isinstance(value, int) for value in values)
            and 0 <= values[0] <= values[1] < block_num
        ):
            issues.append(
                "block_table: PA random range must stay inside [0, block_num-1]"
            )
    return issues


def audit_case(case: dict[str, Any]) -> list[str]:
    api_name = case.get("name") or case.get("aclnn_name")
    if api_name == "torch_npu.npu_fused_infer_attention_score":
        return audit_fia_case(case)
    if api_name == "torch_npu.npu_kv_quant_sparse_flash_attention":
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
