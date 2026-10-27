#!/usr/bin/env python3
"""Materialize/validate explicit groupListOptional sequences in a TTK CSV.

The script reads a TTK ACLNN CSV and uses the ``groupListData`` sequence that
is already present in each row's ``attributes``. By default it never invents
or recalculates values: a missing or invalid ``groupListData`` is an error.
Use ``--compute-missing`` only when the CSV intentionally has no sequence yet.

When a sequence is present, it is validated against groupType=0/2 and
groupListType=0/1/2:

- type 0: cumulative counts, non-negative and non-decreasing, last value <= M
- type 1: per-group counts, non-negative, sum <= M
- type 2: ``[groupIdx, groupSize]`` pairs, non-zero groups first, sum <= M

With ``--compute-missing``/``--recompute`` the derived sequence is
deterministic: each group gets ``M // g`` rows and the first ``M % g`` groups
get one extra row.

Important: the TTK ACLNN input generator currently reads tensor content from
``input_data_ranges`` only. It does not consume ``attributes.groupListData``.
This script prepares the CSV; a TTK build that understands the attribute (or
an E2E framework_api path using ``attributes.groupListOptional``) must consume
it to make the sequence effective.
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
import sys
from pathlib import Path
from typing import Any


def _parse_literal(raw: str, label: str) -> Any:
    if raw is None or not str(raw).strip():
        return None
    try:
        return ast.literal_eval(raw)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"{label}: cannot parse Python literal: {exc}") from exc


def _first_shape(shape: Any) -> list[int] | None:
    if not isinstance(shape, (list, tuple)) or not shape:
        return None
    if isinstance(shape[0], (list, tuple)):
        shape = shape[0]
    if isinstance(shape, (list, tuple)) and all(
        isinstance(dim, int) and not isinstance(dim, bool) for dim in shape
    ):
        return [int(dim) for dim in shape]
    return None


def _shape_at(shapes: Any, index: int, label: str) -> list[int] | None:
    if not isinstance(shapes, (list, tuple)):
        raise ValueError(f"{label}: tensor_view_shapes must be a tuple")
    if index >= len(shapes):
        return None
    return _first_shape(shapes[index])


def _attr(attrs: dict[str, Any], camel: str, snake: str) -> Any:
    if camel in attrs:
        return attrs[camel]
    return attrs.get(snake)


def _set_attr(attrs: dict[str, Any], camel: str, value: Any) -> None:
    attrs[camel] = value
    snake = "group_list_data" if camel == "groupListData" else ""
    if snake and snake in attrs:
        del attrs[snake]


def _balanced_counts(m: int, g: int) -> list[int]:
    if m < 0 or g <= 0:
        raise ValueError(f"cannot split M={m} across g={g} groups")
    base, remainder = divmod(m, g)
    return [base + (1 if index < remainder else 0) for index in range(g)]


def _build_group_list(
    group_type: int, group_list_type: int, m: int, g: int,
) -> list[Any]:
    counts = _balanced_counts(m, g)
    if group_list_type == 0:
        data: list[Any] = []
        running = 0
        for count in counts:
            running += count
            data.append(running)
        return data
    if group_list_type == 1:
        return counts
    if group_list_type == 2 and group_type == 0:
        return [[index, count] for index, count in enumerate(counts)]
    raise ValueError(
        f"unsupported groupType/groupListType combination: "
        f"groupType={group_type}, groupListType={group_list_type}"
    )


def _require_int_list(data: Any, expected_len: int, label: str) -> list[int]:
    if not isinstance(data, (list, tuple)) or len(data) != expected_len:
        raise ValueError(
            f"{label}: expected a list of length {expected_len}, got {data!r}"
        )
    values: list[int] = []
    for item in data:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{label}: non-integer value {item!r}")
        values.append(int(item))
    return values


def _validate_group_list(
    data: Any,
    *,
    group_type: int,
    group_list_type: int,
    m: int,
    g: int,
) -> list[Any]:
    if group_list_type in (0, 1):
        values = _require_int_list(data, g, "groupListData")
        if any(value < 0 for value in values):
            raise ValueError("groupListData must be non-negative")
        if group_list_type == 0:
            if any(
                values[index] > values[index + 1]
                for index in range(len(values) - 1)
            ):
                raise ValueError(
                    "groupListType=0 requires a non-decreasing cumulative sequence"
                )
            if values and values[-1] > m:
                raise ValueError(
                    f"groupListType=0 last value {values[-1]} exceeds M={m}"
                )
        else:
            if sum(values) > m:
                raise ValueError(
                    f"groupListType=1 sum {sum(values)} exceeds M={m}"
                )
        return values

    if group_list_type != 2:
        raise ValueError(f"unsupported groupListType={group_list_type}")
    if group_type != 0:
        raise ValueError("groupListType=2 is only supported for groupType=0")
    if not isinstance(data, (list, tuple)) or len(data) != g:
        raise ValueError(
            f"groupListType=2 expected {g} [groupIdx, groupSize] pairs, got {data!r}"
        )
    pairs: list[list[int]] = []
    seen: set[int] = set()
    zero_seen = False
    previous_index = -1
    total_size = 0
    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(
                f"groupListType=2 requires [groupIdx, groupSize] pairs, got {item!r}"
            )
        group_index, group_size = item
        for value in (group_index, group_size):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"groupListType=2 non-integer value {value!r}")
        group_index, group_size = int(group_index), int(group_size)
        if group_index < 0 or group_index >= g:
            raise ValueError(
                f"groupListType=2 groupIdx {group_index} outside [0, {g})"
            )
        if group_index in seen:
            raise ValueError(
                f"groupListType=2 duplicate groupIdx {group_index}"
            )
        if group_size < 0:
            raise ValueError("groupListType=2 groupSize must be non-negative")
        if group_size == 0:
            zero_seen = True
        else:
            if zero_seen:
                raise ValueError(
                    "groupListType=2 requires non-zero groups before zero groups"
                )
            if group_index <= previous_index:
                raise ValueError(
                    "groupListType=2 non-zero groups must be ordered by groupIdx"
                )
            previous_index = group_index
        seen.add(group_index)
        total_size += group_size
        pairs.append([group_index, group_size])
    if total_size > m:
        raise ValueError(
            f"groupListType=2 second-column sum {total_size} exceeds M={m}"
        )
    return pairs


def _clear_group_list_range(
    ranges: Any, group_list_index: int, label: str,
) -> tuple[Any, bool]:
    if ranges is None:
        return ranges, False
    if not isinstance(ranges, (list, tuple)):
        raise ValueError(f"{label}: input_data_ranges must be a tuple")
    updated = list(ranges)
    while len(updated) <= group_list_index:
        updated.append((None, None))
    changed = updated[group_list_index] != (None, None)
    updated[group_list_index] = (None, None)
    return tuple(updated), changed


def _derive_m_and_g(
    group_type: int,
    shapes: Any,
    x_index: int,
    weight_index: int,
    out_index: int,
) -> tuple[int, int]:
    x_shape = _shape_at(shapes, x_index, "x")
    weight_shape = _shape_at(shapes, weight_index, "weight")
    out_shape = _shape_at(shapes, out_index, "out")
    if weight_shape is None:
        raise ValueError("weight shape is missing")
    g = int(weight_shape[0])
    if group_type == 0:
        if x_shape is None or len(x_shape) < 1:
            raise ValueError("groupType=0 requires a concrete x shape")
        m = int(x_shape[0])
    elif group_type == 2:
        if out_shape is not None and len(out_shape) >= 2:
            m = int(out_shape[1])
        elif x_shape is not None and len(x_shape) >= 2:
            m = int(x_shape[1])
        elif x_shape is not None and len(x_shape) >= 1:
            m = int(x_shape[0])
        else:
            raise ValueError("groupType=2 requires x or out shape to derive M")
    else:
        raise ValueError(f"unsupported groupType={group_type}")
    if m < 0 or g <= 0:
        raise ValueError(f"invalid M={m} or g={g}")
    return m, g


def _coerce_group_list_data(value: Any, label: str) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        raise ValueError(f"{label}: empty groupListData string")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(
                f"{label}: groupListData string is not valid JSON/Python literal: {exc}"
            ) from exc


def _preview(data: list[Any], full: bool) -> list[Any]:
    if full or len(data) <= 16:
        return data
    head = data[:8]
    tail = data[-8:]
    return [*head, "...", *tail]


def _process_row(row: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    testcase_name = row.get("testcase_name", "")
    api_name = row.get("api_name", "")
    if args.api_name and api_name != args.api_name:
        return {"testcase_name": testcase_name, "action": "skipped_api"}

    attrs = _parse_literal(row.get("attributes", ""), f"{testcase_name}.attributes")
    if not isinstance(attrs, dict):
        raise ValueError(f"{testcase_name}.attributes must be a dict")
    group_type = _attr(attrs, "groupType", "group_type")
    group_list_type = _attr(attrs, "groupListType", "group_list_type")
    if group_type is None or group_list_type is None:
        return {"testcase_name": testcase_name, "action": "skipped_missing_attrs"}
    if isinstance(group_type, bool) or not isinstance(group_type, int):
        raise ValueError(f"{testcase_name}.groupType must be an integer")
    if isinstance(group_list_type, bool) or not isinstance(group_list_type, int):
        raise ValueError(f"{testcase_name}.groupListType must be an integer")
    if group_type not in (0, 2):
        return {"testcase_name": testcase_name, "action": "skipped_group_type"}

    shapes = _parse_literal(
        row.get("tensor_view_shapes", ""), f"{testcase_name}.tensor_view_shapes"
    )
    m, g = _derive_m_and_g(
        group_type, shapes, args.x_index, args.weight_index, args.out_index
    )
    group_shape = _shape_at(shapes, args.group_list_index, "groupListOptional")
    expected_shape = (g, 2) if group_list_type == 2 else (g,)
    if group_shape is not None and tuple(group_shape) != expected_shape:
        raise ValueError(
            f"groupListOptional shape {tuple(group_shape)} does not match "
            f"groupListType={group_list_type} expected {expected_shape}"
        )

    existing = _attr(attrs, "groupListData", "group_list_data")
    if existing is not None:
        existing = _coerce_group_list_data(
            existing, f"{testcase_name}.groupListData"
        )
    if existing is None:
        if not args.compute_missing:
            raise ValueError(
                "groupListData is missing; provide it in the CSV or pass "
                "--compute-missing"
            )
        data = _build_group_list(group_type, group_list_type, m, g)
        action = "computed"
    elif args.recompute:
        data = _build_group_list(group_type, group_list_type, m, g)
        action = "recomputed"
    else:
        data = _validate_group_list(
            existing,
            group_type=group_type,
            group_list_type=group_list_type,
            m=m,
            g=g,
        )
        action = "preserved"
    data = _validate_group_list(
        data,
        group_type=group_type,
        group_list_type=group_list_type,
        m=m,
        g=g,
    )
    _set_attr(attrs, "groupListData", data)
    if args.target in ("e2e", "both"):
        attrs["groupListOptional"] = data
    row["attributes"] = repr(attrs)

    range_cleared = False
    if args.clear_range:
        ranges, range_cleared = _clear_group_list_range(
            _parse_literal(
                row.get("input_data_ranges", ""),
                f"{testcase_name}.input_data_ranges",
            ),
            args.group_list_index,
            testcase_name,
        )
        row["input_data_ranges"] = repr(ranges)

    return {
        "testcase_name": testcase_name,
        "action": action,
        "group_type": group_type,
        "group_list_type": group_list_type,
        "m": m,
        "g": g,
        "range_cleared": range_cleared,
        "value_count": len(data),
        "values": _preview(data, args.full_values),
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def materialize_csv(
    input_path: Path,
    output_path: Path | None = None,
    *,
    target: str = "aclnn",
    compute_missing: bool = False,
    recompute: bool = False,
    clear_range: bool = False,
    api_name: str = "aclnnGroupedMatmulV5",
    x_index: int = 0,
    weight_index: int = 1,
    out_index: int = 12,
    group_list_index: int = 8,
    full_values: bool = False,
    dry_run: bool = False,
    report_json: Path | None = None,
) -> dict[str, Any]:
    input_path = Path(input_path).expanduser().resolve()
    output_path = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else input_path
    )
    if not input_path.is_file():
        return {"ok": False, "error": f"CSV not found: {input_path}"}

    args = argparse.Namespace(
        api_name=api_name,
        x_index=x_index,
        weight_index=weight_index,
        out_index=out_index,
        group_list_index=group_list_index,
        target=target,
        compute_missing=compute_missing,
        recompute=recompute,
        clear_range=clear_range,
        full_values=full_values,
    )
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    details: list[dict[str, Any]] = []
    errors: list[str] = []
    changed = 0
    for index, row in enumerate(rows, 1):
        try:
            detail = _process_row(row, args)
        except ValueError as exc:
            errors.append(f"row {index} ({row.get('testcase_name', '?')}): {exc}")
            continue
        details.append(detail)
        if detail.get("action") in ("computed", "preserved", "recomputed"):
            changed += 1

    report: dict[str, Any] = {
        "ok": not errors,
        "input_csv": str(input_path),
        "output_csv": str(output_path),
        "target": target,
        "clear_range": bool(clear_range),
        "rows": len(rows),
        "changed": changed,
        "errors": errors,
        "details": details,
    }
    if errors:
        return report
    if not dry_run:
        _write_csv(output_path, fieldnames, rows)
    if report_json is not None:
        Path(report_json).expanduser().resolve().write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Use/validate attributes.groupListData in a TTK CSV and optionally "
            "clear the groupList tensor input_data_ranges."
        )
    )
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--output-csv", help="defaults to --input-csv (in place)")
    parser.add_argument(
        "--target",
        choices=("aclnn", "e2e", "both"),
        default="aclnn",
        help=(
            "aclnn: write attributes.groupListData; e2e/both: also write "
            "attributes.groupListOptional for TTK framework_api override."
        ),
    )
    parser.add_argument(
        "--compute-missing",
        action="store_true",
        help=(
            "derive groupListData from M/g when the CSV row has none; "
            "by default a missing value is an error"
        ),
    )
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="ignore an existing groupListData and rebuild it from M/g",
    )
    parser.add_argument(
        "--clear-range",
        action="store_true",
        help="set the groupList tensor input_data_ranges entry to (None, None)",
    )
    parser.add_argument("--api-name", default="aclnnGroupedMatmulV5")
    parser.add_argument("--x-index", type=int, default=0)
    parser.add_argument("--weight-index", type=int, default=1)
    parser.add_argument("--out-index", type=int, default=12)
    parser.add_argument("--group-list-index", type=int, default=8)
    parser.add_argument("--report-json")
    parser.add_argument(
        "--full-values",
        action="store_true",
        help="include the complete groupListData sequence in the JSON report",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report without writing the CSV",
    )
    args = parser.parse_args()
    report = materialize_csv(
        Path(args.input_csv),
        Path(args.output_csv) if args.output_csv else None,
        target=args.target,
        compute_missing=args.compute_missing,
        recompute=args.recompute,
        clear_range=args.clear_range,
        api_name=args.api_name,
        x_index=args.x_index,
        weight_index=args.weight_index,
        out_index=args.out_index,
        group_list_index=args.group_list_index,
        full_values=args.full_values,
        dry_run=args.dry_run,
        report_json=Path(args.report_json) if args.report_json else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
