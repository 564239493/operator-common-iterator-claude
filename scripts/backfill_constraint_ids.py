#!/usr/bin/env python3
"""为 constraints.json 的 constraints_in_parameters 条目回填全局唯一 id（C-<NNN>）。

按条目在文件中的出现顺序连续编号（跨平台连续）；已带合法 id 的条目保持不动，
仅给缺失 id 的条目补号（幂等）。供旧 run 产物回填或人工补漏使用。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ID_RE = re.compile(r"^C-(\d+)$")


def backfill_constraint_ids(constraints: dict) -> tuple[int, list[str]]:
    """回填缺失的约束 id，返回 (回填数, 日志)。"""
    cip = constraints.get("constraints_in_parameters")
    if isinstance(cip, dict):
        buckets = list(cip.values())
    elif isinstance(cip, list):
        buckets = [cip]
    else:
        return 0, []
    max_num = 0
    for bucket in buckets:
        if not isinstance(bucket, list):
            continue
        for entry in bucket:
            if isinstance(entry, dict):
                match = ID_RE.fullmatch(str(entry.get("id") or ""))
                if match:
                    max_num = max(max_num, int(match.group(1)))
    filled = 0
    log: list[str] = []
    for bucket in buckets:
        if not isinstance(bucket, list):
            continue
        for entry in bucket:
            if not isinstance(entry, dict):
                continue
            if ID_RE.fullmatch(str(entry.get("id") or "")):
                continue
            max_num += 1
            entry["id"] = f"C-{max_num:03d}"
            filled += 1
            log.append(f"{entry['id']}: {entry.get('expr', '')[:60]}")
    return filled, log


def main() -> int:
    parser = argparse.ArgumentParser(
        description="回填 constraints.json 约束条目缺失的 id（C-<NNN>，幂等）"
    )
    parser.add_argument("constraints", help="constraints.json 路径（原地改写）")
    args = parser.parse_args()
    path = Path(args.constraints).resolve()
    if not path.is_file():
        print(json.dumps({"ok": False, "code": "CONSTRAINTS_NOT_FOUND",
                          "constraints": str(path)}, ensure_ascii=False))
        return 2
    constraints = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(constraints, dict):
        print(json.dumps({"ok": False, "code": "CONSTRAINTS_NOT_OBJECT",
                          "constraints": str(path)}, ensure_ascii=False))
        return 2
    filled, log = backfill_constraint_ids(constraints)
    if filled:
        path.write_text(
            json.dumps(constraints, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({"ok": True, "filled": filled, "entries": log},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
