#!/usr/bin/env python3
"""Convert ATK compact JSON cases to TTK ACLNN-mode CSV.

TTK ACLNN mode uses ``python3 -m ttk aclnn -i <csv>`` (executed from within
the ops-test-kit directory).  Unlike E2E mode, ACLNN mode:
- calls the native aclnn* C API directly → no custom golden plugin needed
- uses ``ApiTestcaseStructure``: tensor_view_shapes / tensor_dtypes / attributes / …
- TensorList params are nested tuples: ((sub0, sub1),) vs regular (d0, d1)

This script is a standalone converter -- it does NOT touch the existing E2E
pipeline (atc_to_ttk.py, generate_cases.py, execute_cases.py).
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# dtype mapping (ATK JSON → TTK ACLNN)
# ---------------------------------------------------------------------------
DTYPE_MAP = {
    "fp16": "float16",
    "float16": "float16",
    "fp32": "float32",
    "float32": "float32",
    "float": "float32",
    "bf16": "bfloat16",
    "int8": "int8",
    # int4 is packed → TTK/CANN allocates int32 carrier
    "int4": "int32",
    "int32": "int32",
    "int64": "int64",
    "uint8": "uint8",
    "uint64": "uint64",
    "bool": "bool",
}


# ---------------------------------------------------------------------------
# full tensor-parameter signature (ordered)
# ---------------------------------------------------------------------------
# Each entry: (name, is_tensor_list, is_output)
# Order MUST match the aclnnGroupedMatmulV5GetWorkspaceSize C signature.
_SIGNATURE: list[dict[str, Any]] = [
    {"name": "x",                             "tensor_list": True,  "output": False},
    {"name": "weight",                        "tensor_list": True,  "output": False},
    {"name": "biasOptional",                  "tensor_list": True,  "output": False},
    {"name": "scaleOptional",                 "tensor_list": True,  "output": False},
    {"name": "offsetOptional",                "tensor_list": True,  "output": False},
    {"name": "antiquantScaleOptional",        "tensor_list": True,  "output": False},
    {"name": "antiquantOffsetOptional",       "tensor_list": True,  "output": False},
    {"name": "perTokenScaleOptional",         "tensor_list": True,  "output": False},
    {"name": "groupListOptional",             "tensor_list": False, "output": False},
    {"name": "activationInputOptional",       "tensor_list": True,  "output": False},
    {"name": "activationQuantScaleOptional",  "tensor_list": True,  "output": False},
    {"name": "activationQuantOffsetOptional", "tensor_list": True,  "output": False},
    {"name": "out",                           "tensor_list": True,  "output": True},
    {"name": "activationFeatureOutOptional",  "tensor_list": True,  "output": True},
    {"name": "dynQuantScaleOutOptional",      "tensor_list": True,  "output": True},
]

# attr names (non-tensor params that go into the ``attributes`` CSV column)
_ATTR_NAMES = {
    "splitItem", "groupType", "groupListType", "actType", "tuningConfigOptional",
}

_OUTPUT_NAMES = {e["name"] for e in _SIGNATURE if e["output"]}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _clamp_to_dtype(dtype: str, value: Any) -> Any:
    """Clamp integer values to dtype bounds (defence in depth)."""
    bounds = {
        "int8": (-128, 127), "uint8": (0, 255),
        "int32": (-2_147_483_648, 2_147_483_647),
        "uint32": (0, 4_294_967_295),
        "int64": (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
        "uint64": (0, 18_446_744_073_709_551_615),
    }.get(dtype)
    if bounds is None or not isinstance(value, (int, float)):
        return value
    lo, hi = bounds
    if isinstance(value, list):
        return [max(lo, min(hi, v)) if isinstance(v, (int, float)) else v for v in value]
    return max(lo, min(hi, value))


def _resolve_range_value(item: dict[str, Any]) -> Any:
    """Resolve a concrete scalar from an ATK range_values field."""
    value = item.get("range_values")
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], (int, float)):
        return value[0]  # deterministic: take lower bound
    return value


def _format_tensor_list_shape(shape: list[int] | None) -> str | None:
    """Format a single TensorList element shape for CSV."""
    if shape is None:
        return None
    return repr(tuple(shape))


def _format_tensor_list_shapes(shapes: list[list[int] | None]) -> str:
    """Build nested tuple string for a TensorList param.

    Single sub-tensor:   ``((M, K),)``
    Multiple sub-tensors: ``((M1,K), (M2,K))``
    Absent (None):        ``None``
    """
    filtered = [s for s in shapes if s is not None]
    if not filtered:
        return "None"
    if len(filtered) == 1:
        inner = repr(tuple(filtered[0]))
    else:
        inner = repr(tuple(tuple(s) for s in filtered))
    return f"({inner},)"


def _format_single_tensor_shape(shape: list[int] | None) -> str:
    """Format a single (non-TensorList) tensor shape."""
    if shape is None:
        return "None"
    return repr(tuple(shape))


def _format_dtype(dtype_str: str | None) -> str:
    """Map ATK dtype string to TTK dtype string."""
    if dtype_str is None:
        return "None"
    return DTYPE_MAP.get(dtype_str.lower(), dtype_str.lower())


def _format_attrs(case: dict[str, Any]) -> dict[str, Any]:
    """Extract non-tensor attributes from ATK case inputs."""
    attrs: dict[str, Any] = {}
    for item in case.get("inputs", []):
        name = item.get("name", "")
        if name not in _ATTR_NAMES:
            continue
        value = item.get("range_values")
        length = item.get("length") or 0
        if value is None:
            continue
        # tuningConfigOptional is aclIntArray* — TTK AcLArray needs a list
        if name == "tuningConfigOptional":
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], (int, float)):
                # Range pair [lo, hi] with explicit length
                if length == 3:
                    value = [value[0], value[1], -1]
                else:
                    value = list(value)
            elif not isinstance(value, list):
                value = [value]
        attrs[name] = value
    return attrs


# ---------------------------------------------------------------------------
# per-case conversion
# ---------------------------------------------------------------------------
def convert_case(case: dict[str, Any], case_index: int) -> dict[str, str]:
    """Convert one ATK compact case to a TTK ACLNN CSV row dict."""
    # index ATK inputs by name
    by_name: dict[str, dict[str, Any]] = {}
    for item in case.get("inputs", []):
        name = item.get("name", "")
        if name:
            by_name[name] = item

    shapes_parts: list[str] = []
    dtypes_parts: list[str] = []
    output_indexes: list[int] = []

    for idx, sig in enumerate(_SIGNATURE):
        name = sig["name"]
        is_tensor_list = sig["tensor_list"]
        is_output = sig["output"]
        item = by_name.get(name)

        if item is None or item.get("type") not in ("tensor", "tensors"):
            # absent optional tensor
            shapes_parts.append("None")
            dtypes_parts.append("None")
            continue

        shape = item.get("shape")
        dtype_raw = item.get("dtype", "")
        dtype_ttk = _format_dtype(dtype_raw)

        if is_tensor_list:
            # For TensorList, wrap in nested tuple
            if shape and isinstance(shape, list) and shape and isinstance(shape[0], list):
                # multiple sub-tensors
                shapes_parts.append(_format_tensor_list_shapes(shape))
            elif shape:
                shapes_parts.append(f"({repr(tuple(shape))},)")
            else:
                shapes_parts.append("None")
            dtypes_parts.append(f"('{dtype_ttk}',)")
        else:
            shapes_parts.append(_format_single_tensor_shape(shape))
            dtypes_parts.append(f"'{dtype_ttk}'")

        if is_output:
            output_indexes.append(idx)

    # Build the outer tuples
    tensor_view_shapes = f"({','.join(shapes_parts)})"
    tensor_dtypes = f"({','.join(dtypes_parts)})"

    attrs = _format_attrs(case)
    api_name = case.get("name") or case.get("aclnn_name", "aclnnGroupedMatmulV5")
    case_id = int(case.get("id", case_index))

    output_tensor_indexes = repr(tuple(output_indexes)) if output_indexes else "()"

    return {
        "testcase_name": f"{api_name}_{case_id:03d}",
        "api_name": api_name,
        "tensor_view_shapes": tensor_view_shapes,
        "tensor_dtypes": tensor_dtypes,
        "attributes": repr(attrs),
        "output_tensor_indexes": output_tensor_indexes,
        "is_enabled": "True",
        "remark": "converted from ATK compact JSON; TTK ACLNN mode",
    }


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
def audit_case(case: dict[str, Any]) -> list[str]:
    """Basic sanity checks."""
    issues: list[str] = []
    by_name = {item.get("name"): item for item in case.get("inputs", [])}
    # x and weight are required
    for name in ("x", "weight"):
        if name not in by_name:
            issues.append(f"missing required input: {name}")
    return issues


# ---------------------------------------------------------------------------
# CSV headers (TTK ACLNN ApiTestcaseStructure)
# ---------------------------------------------------------------------------
HEADERS = (
    "testcase_name",
    "api_name",
    "tensor_view_shapes",
    "tensor_dtypes",
    "tensor_formats",
    "attributes",
    "input_data_ranges",
    "precision_tolerances",
    "absolute_precision",
    "output_tensor_indexes",
    "is_enabled",
    "remark",
    "soc_series",
    "priority",
)


# ---------------------------------------------------------------------------
# file-level convert
# ---------------------------------------------------------------------------
def convert_file(source: Path, destination: Path) -> dict[str, Any]:
    cases = json.loads(source.read_text(encoding="utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)

    rows = [convert_case(case, i) for i, case in enumerate(cases)]
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    audit = [
        {"id": case.get("id", i), "issues": audit_case(case)}
        for i, case in enumerate(cases)
    ]
    return {
        "source": str(source),
        "destination": str(destination),
        "case_count": len(cases),
        "semantically_clean_count": sum(not entry["issues"] for entry in audit),
        "audit": audit,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert ATK compact JSON to TTK ACLNN CSV"
    )
    parser.add_argument("source", type=Path, help="ATK compact JSON (e.g. aclnnGroupedMatmulV5.json)")
    parser.add_argument("destination", type=Path, help="Output TTK ACLNN CSV")
    parser.add_argument("--audit-output", type=Path, help="Optional JSON audit report")
    args = parser.parse_args()

    result = convert_file(args.source, args.destination)
    if args.audit_output:
        args.audit_output.parent.mkdir(parents=True, exist_ok=True)
        args.audit_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
