"""合并多个补充批次 → 完整 supplemented constraints。

batch1 用“已分桶”schema(constraints_in_parameters/error_branches/... 直接是数组)；
batch2/batch3 用“带 category 字段”的 items 数组。本合并器把后者按 category 路由进
前者同款桶，去重(expr 文本相同则跳过)，产出统一文件。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("cca_translate.merge")

BUCKETS = [
    "constraints_in_parameters",
    "error_branches",
    "ub_branches",
    "normalize_rules",
    "unreachable",
]


def merge_batches(batch_files: list[str | Path]) -> dict:
    """合并。第一个文件作为基线(已分桶)，其余按 category 路由并入。"""
    if not batch_files:
        raise ValueError("无批次文件")

    merged: dict = {
        "operator_name": None,
        "product": None,
        "constraints_in_parameters": [],
        "error_branches": [],
        "ub_branches": [],
        "normalize_rules": [],
        "unreachable": [],
        "stubs": [],
        "reconciliation_log": [],
    }
    seen_exprs: set[str] = set()

    def _dedup_key(item: dict, bucket: str) -> str:
        """normalize 类用 when/rewrite 去重，其余用 expr。"""
        if "expr" in item and item["expr"]:
            return item["expr"] + "|" + bucket
        return (item.get("when", "") + "|" + item.get("rewrite", "")) + "|" + bucket

    for path in batch_files:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not merged["operator_name"]:
            merged["operator_name"] = data.get("operator_name")
            merged["product"] = data.get("product")
        # 已分桶字段(batch1)
        for b in BUCKETS:
            for item in data.get(b, []):
                key = _dedup_key(item, b)
                if key in seen_exprs:
                    continue
                seen_exprs.add(key)
                merged[b].append(item)
        # 带 category 的 items(batch2/batch3)
        for item in data.get("items", []):
            cat = item.get("category")
            if cat not in BUCKETS:
                log.warning("未知 category %s，跳过: %s", cat, item.get("branch_ref"))
                continue
            key = _dedup_key(item, cat)
            if key in seen_exprs:
                continue
            seen_exprs.add(key)
            # 落桶时去掉 category 字段(桶名已表达)
            entry = {k: v for k, v in item.items() if k != "category"}
            merged[cat].append(entry)
        # stubs
        for s in data.get("stubs", []):
            merged["stubs"].append(s)
        # reconciliation_log(batch1)
        for e in data.get("reconciliation_log", []):
            if e not in merged["reconciliation_log"]:
                merged["reconciliation_log"].append(e)

    log.info(
        "合并完成: constraints=%d error=%d ub=%d normalize=%d unreachable=%d stubs=%d",
        len(merged["constraints_in_parameters"]), len(merged["error_branches"]),
        len(merged["ub_branches"]), len(merged["normalize_rules"]),
        len(merged["unreachable"]), len(merged["stubs"]),
    )
    return merged
