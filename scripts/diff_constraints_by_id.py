#!/usr/bin/env python3
"""按 id 对比两版 constraints.json，校验执行反馈轮的 id 与约束对应关系。

流程约定：每轮迭代必须在上一轮 constraints.json 基础上原位修改——

* 同一 id 的约束可以因 update/repair 最小修改而变化（changed_fields 记录差异）；
* 同一份约束内容不得在新版本中换用另一个 id（重新编号/重新提取的特征）；
* 新增约束必须分配新 id；删除约束（removed）允许但必须能被 change 记录解释。

退出码：0 = 无 id 对应关系违规；2 = 发现重新编号/内容换 id。
用法：
    python scripts/diff_constraints_by_id.py <old.json> <new.json> [--output <report.json>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 参与同 id 内容同一性判定的字段（id 本身除外）。
CONTENT_FIELDS = (
    "expr_type",
    "expr",
    "relation_params",
    "src_text",
    "src_txt_line",
    "origin",
)


def load_entries(path: Path) -> dict[str, tuple[str, dict[str, Any]]]:
    """读取 constraints.json，返回 id -> (平台, 条目)。

    constraints_in_parameters 支持按平台分桶（Dict[str, List]）与扁平列表两种形态，
    与 validate_artifacts 的处理保持一致。
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    cip = payload.get("constraints_in_parameters")
    entries: dict[str, tuple[str, dict[str, Any]]] = {}
    if isinstance(cip, dict):
        for platform, relations in cip.items():
            if isinstance(relations, list):
                for item in relations:
                    if isinstance(item, dict) and str(item.get("id") or "").strip():
                        entries[str(item["id"])] = (str(platform), item)
    elif isinstance(cip, list):
        for item in cip:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                entries[str(item["id"])] = ("", item)
    else:
        raise ValueError(f"{path}: constraints_in_parameters 缺失或类型非法")
    if not entries:
        raise ValueError(f"{path}: 未找到任何带 id 的约束条目")
    return entries


def content_view(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item.get(field) for field in CONTENT_FIELDS}


def build_report(old_path: Path, new_path: Path) -> dict[str, Any]:
    old_entries = load_entries(old_path)
    new_entries = load_entries(new_path)

    added = sorted(set(new_entries) - set(old_entries))
    removed = sorted(set(old_entries) - set(new_entries))
    modified: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for cid in sorted(set(old_entries) & set(new_entries)):
        before = content_view(old_entries[cid][1])
        after = content_view(new_entries[cid][1])
        if before == after:
            unchanged.append(cid)
            continue
        modified.append(
            {
                "id": cid,
                "platform": new_entries[cid][0],
                "changed_fields": [f for f in CONTENT_FIELDS if before[f] != after[f]],
            }
        )

    # 重新编号检测：新版本某 id 的语义内容 (expr_type, expr) 与旧版本另一 id 完全一致。
    old_semantic: dict[tuple[Any, Any], str] = {}
    for cid, (_, item) in old_entries.items():
        old_semantic[(item.get("expr_type"), item.get("expr"))] = cid
    renumbered: list[dict[str, str]] = []
    for cid in added:
        key = (
            new_entries[cid][1].get("expr_type"),
            new_entries[cid][1].get("expr"),
        )
        old_id = old_semantic.get(key)
        if old_id is not None and old_id != cid:
            renumbered.append({"new_id": cid, "old_id": old_id})

    return {
        "old_file": str(old_path),
        "new_file": str(new_path),
        "old_count": len(old_entries),
        "new_count": len(new_entries),
        "unchanged": unchanged,
        "modified": modified,
        "added": added,
        "removed": removed,
        "renumbered": renumbered,
        "ok": not renumbered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按 id 对比两版 constraints.json，校验原位修改与 id 对应关系。"
    )
    parser.add_argument("old", help="上一轮 constraints.json 路径")
    parser.add_argument("new", help="当前轮 constraints.json 路径")
    parser.add_argument("--output", help="可选：JSON 报告输出路径")
    args = parser.parse_args()

    old_path = Path(args.old)
    new_path = Path(args.new)
    for path in (old_path, new_path):
        if not path.is_file():
            print(f"constraints 文件不存在: {path}", file=sys.stderr)
            return 1

    report = build_report(old_path, new_path)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(
        f"unchanged={len(report['unchanged'])} "
        f"modified={len(report['modified'])} "
        f"added={len(report['added'])} "
        f"removed={len(report['removed'])} "
        f"renumbered={len(report['renumbered'])}"
    )
    for item in report["modified"]:
        print(
            f"  modified {item['id']}: {', '.join(item['changed_fields'])}"
        )
    for cid in report["added"]:
        print(f"  added    {cid}")
    for cid in report["removed"]:
        print(f"  removed  {cid}")
    for item in report["renumbered"]:
        print(
            f"  RENUMBERED: 约束内容从 {item['old_id']} 变为 {item['new_id']} "
            "(同一份约束换 id，违反原位修改约定)"
        )
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
