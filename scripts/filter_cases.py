#!/usr/bin/env python3
"""Select cases whose inputs match simple name/value equality criteria.

Used for targeted executor verification (e.g. re-running only the failing
groupType=2 + x_transposed subset) without hand-editing case files.

Usage:
    python scripts/filter_cases.py --cases <cases.json> --out <filtered.json> \
        --where '{"groupType": 2, "x_transposed": true}'

Criteria keys match input entries by ``name``; a case is kept when every
(name, value) pair has an input entry whose ``range_values`` equals the
value after unwrapping single-element lists.  Nested input groups
(lists of entries) are searched as well.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _iter_input_entries(inputs):
    for entry in inputs or []:
        if isinstance(entry, list):
            yield from _iter_input_entries(entry)
        elif isinstance(entry, dict):
            yield entry


def _unwrap(value):
    while isinstance(value, list) and len(value) == 1:
        value = value[0]
    return value


def _matches(case: dict, where: dict) -> bool:
    entries = list(_iter_input_entries(case.get("inputs")))
    for name, expected in where.items():
        matched = False
        for entry in entries:
            if entry.get("name") != name:
                continue
            if _unwrap(entry.get("range_values")) == expected:
                matched = True
                break
        if not matched:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按 inputs 的 name/range_values 等值条件筛选用例子集。"
    )
    parser.add_argument("--cases", required=True, help="输入 cases JSON（紧凑或展开均可）")
    parser.add_argument("--out", required=True, help="筛选结果输出路径")
    parser.add_argument(
        "--where", required=True,
        help='条件 JSON 对象，如 \'{"groupType": 2, "x_transposed": true}\'',
    )
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        print("error: cases 根必须是数组", file=sys.stderr)
        return 2
    where = json.loads(args.where)
    if not isinstance(where, dict) or not where:
        print("error: --where 必须是非空 JSON 对象", file=sys.stderr)
        return 2

    kept = [case for case in cases if isinstance(case, dict) and _matches(case, where)]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(
        {
            "ok": True,
            "total": len(cases),
            "kept": len(kept),
            "kept_ids": [case.get("id") for case in kept],
            "out": str(out_path.resolve()),
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
