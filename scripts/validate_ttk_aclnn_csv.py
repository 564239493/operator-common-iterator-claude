#!/usr/bin/env python3
"""Local format validator for TTK ACLNN CSV files.

Validates CSV structure, tuple syntax, dtype names, and shape/dtype
alignment without requiring a CANN environment.

Usage: python scripts/validate_ttk_aclnn_csv.py <csv_file>
"""
from __future__ import annotations

import ast
import csv
import sys
from pathlib import Path

# Valid dtype names (TTK accepts both forms)
VALID_DTYPES = {
    "float16", "fp16", "float32", "fp32", "float64", "fp64",
    "bfloat16", "bf16", "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
    "bool", "complex64", "complex128",
    "None",  # absent optional
}

# Required columns for ACLNN mode
REQUIRED_COLUMNS = {"testcase_name", "api_name", "tensor_view_shapes", "tensor_dtypes"}

# All known ACLNN columns
KNOWN_COLUMNS = {
    # common (9)
    "testcase_name", "network_name", "input_data_ranges",
    "precision_tolerances", "absolute_precision",
    "is_enabled", "remark", "soc_series", "priority",
    # aclnn-specific (15)
    "api_name",
    "tensor_view_shapes", "tensor_dtypes", "tensor_formats",
    "tensor_storage_shapes", "tensor_view_offsets", "tensor_view_strides",
    "output_tensor_indexes", "output_inplace_indexes",
    "attributes", "scalar_dtypes", "scalar_data_ranges",
    "dump_file_prefix", "manual_tensor_binaries", "manual_golden_binaries",
}


def _safe_parse(value: str, expect_type: type, label: str) -> tuple[object | None, str | None]:
    """Safely parse a Python literal. Returns (parsed, error_message)."""
    if not value or value.strip() == "":
        return None, None  # empty is OK (default)
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError) as exc:
        return None, f"{label}: cannot parse: {exc}"
    if expect_type and not isinstance(parsed, expect_type):
        # Accept both tuple and list representations
        if expect_type is tuple and isinstance(parsed, list):
            parsed = tuple(parsed)
        elif not isinstance(parsed, expect_type):
            return parsed, f"{label}: expected {expect_type.__name__}, got {type(parsed).__name__}"
    return parsed, None


def _flatten_dtypes(dtypes: object, path: str = "") -> list[str]:
    """Recursively flatten nested dtype tuples to a list of dtype strings."""
    result: list[str] = []
    if isinstance(dtypes, tuple):
        for i, item in enumerate(dtypes):
            sub = _flatten_dtypes(item, f"{path}[{i}]")
            result.extend(sub)
    elif isinstance(dtypes, str):
        result.append(dtypes)
    elif dtypes is None:
        pass  # None entries are not counted
    return result


def _flatten_shapes(shapes: object, path: str = "") -> list[tuple | None]:
    """Recursively flatten nested shape tuples to list of shape tuples.

    None is treated as a leaf (absent optional tensor).
    A tuple of ints is a leaf shape.  Nested tuples are recursed.
    """
    result: list[tuple | None] = []
    if shapes is None:
        result.append(None)
        return result
    if isinstance(shapes, tuple):
        # Leaf shape: tuple where every element is an int
        if shapes and all(isinstance(d, int) for d in shapes):
            result.append(shapes)
        else:
            for i, item in enumerate(shapes):
                sub = _flatten_shapes(item, f"{path}[{i}]")
                result.extend(sub)
    return result


def _check_dtype(dtype_str: str) -> str | None:
    """Return error message if dtype is invalid, else None."""
    if dtype_str == "None":
        return None
    if dtype_str not in VALID_DTYPES:
        # Case-insensitive check
        if dtype_str.lower() not in {d.lower() for d in VALID_DTYPES}:
            return f"unknown dtype: '{dtype_str}'"
    return None


def validate_csv(csv_path: Path) -> dict:
    """Validate a TTK ACLNN CSV file. Returns a result dict."""
    issues: list[str] = []
    warnings: list[str] = []

    with open(csv_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])

        # Check required columns
        missing = REQUIRED_COLUMNS - headers
        if missing:
            issues.append(f"missing required columns: {missing}")

        # Check unknown columns
        unknown = headers - KNOWN_COLUMNS
        if unknown:
            warnings.append(f"unknown columns (will be ignored by TTK): {unknown}")

        # Validate each row
        rows = list(reader)
        if not rows:
            issues.append("CSV has no data rows")
            return {"valid": False, "issues": issues, "warnings": warnings,
                    "row_count": 0, "clean_rows": 0}

        for row_idx, row in enumerate(rows):
            prefix = f"row {row_idx + 2}"  # +2 for 1-indexed + header

            # --- api_name ---
            api_name = row.get("api_name", "")
            if not api_name:
                issues.append(f"{prefix}: api_name is empty")
            elif not api_name.startswith("aclnn"):
                issues.append(f"{prefix}: api_name '{api_name}' does not start with 'aclnn'")

            # --- tensor_view_shapes ---
            shapes_raw = row.get("tensor_view_shapes", "")
            shapes, err = _safe_parse(shapes_raw, tuple, f"{prefix} tensor_view_shapes")
            if err:
                issues.append(err)
            elif shapes is not None:
                flat = _flatten_shapes(shapes)
                if not flat:
                    issues.append(f"{prefix}: tensor_view_shapes resolves to empty list")
                for j, s in enumerate(flat):
                    if s is None:
                        continue
                    if not isinstance(s, tuple):
                        issues.append(f"{prefix}: shape[{j}] is not a tuple: {s}")
                    elif not all(isinstance(d, int) and d > 0 for d in s):
                        bad = [d for d in s if not (isinstance(d, int) and d > 0)]
                        issues.append(f"{prefix}: shape[{j}]={s} has non-positive dims: {bad}")

            # --- tensor_dtypes ---
            dtypes_raw = row.get("tensor_dtypes", "")
            dtypes, err = _safe_parse(dtypes_raw, tuple, f"{prefix} tensor_dtypes")
            if err:
                issues.append(err)
            elif dtypes is not None:
                flat_dtypes = _flatten_dtypes(dtypes)
                if not flat_dtypes:
                    issues.append(f"{prefix}: tensor_dtypes resolves to empty list")
                for j, dt in enumerate(flat_dtypes):
                    dt_err = _check_dtype(dt)
                    if dt_err:
                        issues.append(f"{prefix}: dtype[{j}] {dt_err}")

            # --- Cross-check shapes vs dtypes count ---
            if shapes is not None and dtypes is not None:
                shape_count = len(shapes) if isinstance(shapes, tuple) else 1
                dtype_count = len(dtypes) if isinstance(dtypes, tuple) else 1
                if shape_count != dtype_count:
                    issues.append(
                        f"{prefix}: tensor count mismatch: "
                        f"{shape_count} shapes vs {dtype_count} dtypes"
                    )

            # --- attributes ---
            attrs_raw = row.get("attributes", "")
            if attrs_raw and attrs_raw.strip():
                attrs, err = _safe_parse(attrs_raw, dict, f"{prefix} attributes")
                if err:
                    issues.append(err)

            # --- output_tensor_indexes ---
            out_idx_raw = row.get("output_tensor_indexes", "")
            if out_idx_raw and out_idx_raw.strip():
                out_idx, err = _safe_parse(out_idx_raw, tuple, f"{prefix} output_tensor_indexes")
                if err:
                    issues.append(err)
                elif out_idx is not None and shapes is not None:
                    tensor_count = len(shapes) if isinstance(shapes, tuple) else 1
                    for idx in out_idx:
                        if not isinstance(idx, int) or idx < 0 or idx >= tensor_count:
                            issues.append(
                                f"{prefix}: output_tensor_indexes {idx} out of range "
                                f"[0, {tensor_count - 1}]"
                            )

            # --- Other tuple fields ---
            for field in ("tensor_formats", "input_data_ranges", "precision_tolerances",
                          "scalar_dtypes", "scalar_data_ranges"):
                raw = row.get(field, "")
                if raw and raw.strip():
                    _, err = _safe_parse(raw, tuple, f"{prefix} {field}")
                    if err:
                        issues.append(err)

        clean = len(rows) - len({i.split()[0] for i in issues})
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "row_count": len(rows),
            "clean_rows": max(0, clean),
        }


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <csv_file>")
        sys.exit(2)

    csv_path = Path(sys.argv[1])
    if not csv_path.is_file():
        print(f"ERROR: file not found: {csv_path}")
        sys.exit(1)

    result = validate_csv(csv_path)

    if result["warnings"]:
        print("=== WARNINGS ===")
        for w in result["warnings"]:
            print(f"  [WARN] {w}")

    if result["issues"]:
        print(f"\n=== ISSUES ({len(result['issues'])}) ===")
        for issue in result["issues"]:
            print(f"  [FAIL] {issue}")

    print(f"\n=== SUMMARY ===")
    print(f"  rows: {result['row_count']}")
    print(f"  clean: {result['clean_rows']}/{result['row_count']}")
    print(f"  valid: {result['valid']}")

    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
