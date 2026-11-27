#!/usr/bin/env python3
"""Deterministic pack/unpack/lookup/verify for ACLNN bitfield parameters."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

FIELD_BITS = 16
FIELD_MASK = (1 << FIELD_BITS) - 1
DEFAULT_DOC = (
    Path(__file__).resolve().parents[1]
    / "knowledge"
    / "aclnn"
    / "reference"
    / "bitfield_pack.md"
)

FORMULAS: dict[str, dict[str, Any]] = {
    "group_size_v1": {
        "packed_param": "groupSize",
        "formula": "groupSize = groupSizeK | groupSizeN << 16 | groupSizeM << 32",
        "fields": [
            {"name": "groupSizeM", "shift": 32},
            {"name": "groupSizeN", "shift": 16},
            {"name": "groupSizeK", "shift": 0},
        ],
        "notes": "每个分量占16位，占用低48位，高16位无效；0表示该方向自动推导",
    },
    "block_size_v1": {
        "packed_param": "blockSize",
        "formula": "blockSize = blockSizeK | blockSizeN << 16 | blockSizeM << 32",
        "fields": [
            {"name": "blockSizeM", "shift": 32},
            {"name": "blockSizeN", "shift": 16},
            {"name": "blockSizeK", "shift": 0},
        ],
        "notes": "与 groupSize 同一套位布局",
    },
}

TABLE: list[dict[str, Any]] = [
    {
        "formula_id": "group_size_v1",
        "scenario": "G-B量化",
        "components": [1, 128, 128],
        "packed": 4303356032,
        "source": "aclnnQuantMatmulV5「约束说明」G-B 量化取值表",
    },
    {
        "formula_id": "group_size_v1",
        "scenario": "B-B量化",
        "components": [128, 128, 128],
        "packed": 549764202624,
        "source": "aclnnQuantMatmulV5「约束说明」B-B 量化取值表",
    },
    {
        "formula_id": "group_size_v1",
        "scenario": "MX全量化/MX伪量化",
        "components": [1, 1, 32],
        "packed": 4295032864,
        "source": "aclnnQuantMatmulV5「约束说明」MX 全量化/伪量化取值表",
    },
    {
        "formula_id": "group_size_v1",
        "scenario": "MX伪量化/T-CG量化",
        "components": [0, 0, 32],
        "packed": 32,
        "source": "aclnnQuantMatmulV5「约束说明」MX 伪量化/T-CG 量化取值表",
    },
    {
        "formula_id": "group_size_v1",
        "scenario": "MX自适应组合",
        "components": [0, 1, 0],
        "packed": 65536,
        "source": "aclnnAlltoAllQuantMatmulV2 / aclnnQuantMatmulAlltoAllV2「约束说明」MX groupSize 取值表",
    },
    {
        "formula_id": "group_size_v1",
        "scenario": "MX自适应组合",
        "components": [0, 1, 32],
        "packed": 65568,
        "source": "aclnnAlltoAllQuantMatmulV2 / aclnnQuantMatmulAlltoAllV2「约束说明」MX groupSize 取值表",
    },
    {
        "formula_id": "group_size_v1",
        "scenario": "MX自适应组合",
        "components": [1, 0, 0],
        "packed": 4294967296,
        "source": "aclnnAlltoAllQuantMatmulV2 / aclnnQuantMatmulAlltoAllV2「约束说明」MX groupSize 取值表",
    },
    {
        "formula_id": "group_size_v1",
        "scenario": "MX自适应组合",
        "components": [1, 0, 32],
        "packed": 4294967328,
        "source": "aclnnAlltoAllQuantMatmulV2 / aclnnQuantMatmulAlltoAllV2「约束说明」MX groupSize 取值表",
    },
    {
        "formula_id": "group_size_v1",
        "scenario": "MX自适应组合",
        "components": [1, 1, 0],
        "packed": 4295032832,
        "source": "aclnnAlltoAllQuantMatmulV2 / aclnnQuantMatmulAlltoAllV2「约束说明」MX groupSize 取值表",
    },
    {
        "formula_id": "group_size_v1",
        "scenario": "K-G量化",
        "components": [0, 0, 256],
        "packed": 256,
        "source": "aclnnQuantMatmulV5「约束说明」K-G 量化取值表",
    },
    {
        "formula_id": "group_size_v1",
        "scenario": "不生效/不支持groupSize，传0",
        "components": [0, 0, 0],
        "packed": 0,
        "source": "aclnnQuantMatmulWeightNz「约束说明」不生效/传 0",
    },
    {
        "formula_id": "block_size_v1",
        "scenario": "当前版本仅支持M=N=0、K=0",
        "components": [0, 0, 0],
        "packed": 0,
        "source": "aclnnAllGatherMatmulV2「参数说明」blockSize 仅支持 0",
    },
]


class BitfieldError(ValueError):
    pass


def _formula(formula_id: str) -> dict[str, Any]:
    try:
        return FORMULAS[formula_id]
    except KeyError as exc:
        raise BitfieldError(
            f"unknown formula_id: {formula_id}; known: {sorted(FORMULAS)}"
        ) from exc


def _parse_components(raw: str) -> list[int]:
    parts = [item.strip() for item in raw.split(",")]
    if len(parts) != 3 or any(not part for part in parts):
        raise BitfieldError("--components must be three integers M,N,K, e.g. 1,128,128")
    try:
        return [int(part, 0) for part in parts]
    except ValueError as exc:
        raise BitfieldError(f"invalid --components: {raw!r}") from exc


def _normalize_components(formula_id: str, values: list[int]) -> dict[str, int]:
    formula = _formula(formula_id)
    if len(values) != len(formula["fields"]):
        raise BitfieldError(
            f"{formula_id} expects {len(formula['fields'])} components, got {len(values)}"
        )
    return {
        field["name"]: value
        for field, value in zip(formula["fields"], values)
    }


def _validate_field(name: str, value: int) -> None:
    if not 0 <= value <= FIELD_MASK:
        raise BitfieldError(f"{name}={value} out of range 0..{FIELD_MASK} (16 bits)")


def pack(formula_id: str, values: list[int]) -> int:
    formula = _formula(formula_id)
    components = _normalize_components(formula_id, values)
    packed = 0
    for field in formula["fields"]:
        value = components[field["name"]]
        _validate_field(field["name"], value)
        packed |= value << field["shift"]
    return packed


def unpack(formula_id: str, packed: int) -> dict[str, Any]:
    formula = _formula(formula_id)
    if packed < 0:
        raise BitfieldError(f"packed value must be non-negative, got {packed}")
    components = {}
    for field in formula["fields"]:
        components[field["name"]] = (packed >> field["shift"]) & FIELD_MASK
    return {
        "formula_id": formula_id,
        "packed": packed,
        "hex": f"0x{packed:016x}",
        "high_bits": packed >> (FIELD_BITS * len(formula["fields"])),
        "components": components,
    }


def lookup(formula_id: str, values: list[int]) -> dict[str, Any]:
    _formula(formula_id)
    normalized = _normalize_components(formula_id, values)
    for row in TABLE:
        if row["formula_id"] != formula_id:
            continue
        if _normalize_components(formula_id, row["components"]) == normalized:
            return {
                "found": True,
                "formula_id": formula_id,
                "scenario": row["scenario"],
                "components": normalized,
                "packed": row["packed"],
                "source": row["source"],
            }
    return {
        "found": False,
        "formula_id": formula_id,
        "components": normalized,
        "packed": pack(formula_id, values),
        "source": "computed",
    }


def verify(formula_id: str, values: list[int], claimed: int) -> dict[str, Any]:
    expected = pack(formula_id, values)
    matched = lookup(formula_id, values)
    return {
        "ok": expected == claimed,
        "formula_id": formula_id,
        "components": _normalize_components(formula_id, values),
        "expected": expected,
        "claimed": claimed,
        "source": matched["source"],
    }


def self_test() -> dict[str, Any]:
    errors = []
    seen: set[tuple[str, tuple[int, ...]]] = set()
    for row in TABLE:
        key = (row["formula_id"], tuple(row["components"]))
        if key in seen:
            errors.append(
                {
                    "formula_id": row["formula_id"],
                    "components": row["components"],
                    "error": "duplicate formula_id + components row",
                }
            )
        seen.add(key)
        actual = pack(row["formula_id"], row["components"])
        if actual != row["packed"]:
            errors.append(
                {
                    "formula_id": row["formula_id"],
                    "components": row["components"],
                    "expected": row["packed"],
                    "actual": actual,
                }
            )
    return {"ok": not errors, "rows": len(TABLE), "errors": errors}


def _parse_components_cell(raw: str) -> list[int]:
    values = re.findall(r"-?\d+", raw)
    if len(values) != 3:
        raise BitfieldError(f"invalid components cell: {raw!r}")
    return [int(value) for value in values]


def parse_markdown_table(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip().startswith("## C.")),
        None,
    )
    if start is None:
        raise BitfieldError("markdown table section '## C.' not found")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    rows: list[dict[str, Any]] = []
    header_seen = False
    for line in lines[start:end]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not header_seen:
            if cells and cells[0] == "formula_id":
                header_seen = True
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        if len(cells) != 5:
            raise BitfieldError(f"invalid markdown table row: {stripped}")
        rows.append(
            {
                "formula_id": cells[0],
                "scenario": cells[1],
                "components": _parse_components_cell(cells[2]),
                "packed": int(cells[3].replace("`", "").strip(), 0),
                "source": cells[4],
            }
        )
    if not header_seen:
        raise BitfieldError("markdown table header 'formula_id' not found")
    return rows


def check_doc_table(path: Path = DEFAULT_DOC) -> list[str]:
    try:
        doc_rows = parse_markdown_table(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"bitfield_pack markdown table unreadable: {exc}"]
    errors: list[str] = []
    if len(doc_rows) != len(TABLE):
        errors.append(
            f"table row count mismatch: markdown={len(doc_rows)} script={len(TABLE)}"
        )
    for index, (doc_row, script_row) in enumerate(zip(doc_rows, TABLE), 1):
        for field in ("formula_id", "scenario", "components", "packed", "source"):
            if doc_row[field] != script_row[field]:
                errors.append(
                    f"row {index} field {field} mismatch: "
                    f"markdown={doc_row[field]!r} script={script_row[field]!r}"
                )
    return errors


def _emit(payload: dict[str, Any], ok: bool) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pack/unpack/lookup/verify ACLNN bitfield parameters."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pack_parser = subparsers.add_parser("pack")
    pack_parser.add_argument("--formula", required=True)
    pack_parser.add_argument("--components", required=True, help="M,N,K")

    unpack_parser = subparsers.add_parser("unpack")
    unpack_parser.add_argument("--formula", required=True)
    unpack_parser.add_argument("--value", required=True, type=lambda x: int(x, 0))

    lookup_parser = subparsers.add_parser("lookup")
    lookup_parser.add_argument("--formula", required=True)
    lookup_parser.add_argument("--components", required=True, help="M,N,K")

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--formula", required=True)
    verify_parser.add_argument("--components", required=True, help="M,N,K")
    verify_parser.add_argument("--value", required=True, type=lambda x: int(x, 0))

    check_doc_parser = subparsers.add_parser("check-doc")
    check_doc_parser.add_argument("--path", default=str(DEFAULT_DOC))

    subparsers.add_parser("table")
    subparsers.add_parser("self-test")

    args = parser.parse_args()
    try:
        if args.command == "pack":
            values = _parse_components(args.components)
            payload = {
                "formula_id": args.formula,
                "components": _normalize_components(args.formula, values),
                "packed": pack(args.formula, values),
                "source": "computed",
            }
            return _emit(payload, True)
        if args.command == "unpack":
            return _emit(unpack(args.formula, args.value), True)
        if args.command == "lookup":
            values = _parse_components(args.components)
            return _emit(lookup(args.formula, values), True)
        if args.command == "verify":
            values = _parse_components(args.components)
            result = verify(args.formula, values, args.value)
            return _emit(result, result["ok"])
        if args.command == "check-doc":
            errors = check_doc_table(Path(args.path))
            return _emit(
                {"ok": not errors, "path": str(Path(args.path)), "errors": errors},
                not errors,
            )
        if args.command == "table":
            return _emit({"formulas": FORMULAS, "table": TABLE}, True)
        result = self_test()
        return _emit(result, result["ok"])
    except BitfieldError as exc:
        return _emit({"ok": False, "error": str(exc)}, False)


if __name__ == "__main__":
    sys.exit(main())
