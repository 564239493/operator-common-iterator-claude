"""把原 constraints.json + 补充批次合成最终文件（constraints_in_parameters 保持原字段格式）。

原 schema：constraints_in_parameters 是
  - 按平台分桶: {product: [{expr_type, expr, relation_params, src_text, origin}]}
  - 或扁平列表: [{expr_type, expr, relation_params, src_text, origin}]
补充的成功路径约束按原字段并入目标产品桶(或扁平列表)，verdict/note 折进 src_text
便于追溯；UB/normalize/error/unreachable/stubs 是原 schema 没有的类，作额外顶层键保留
(不丢信息)。
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from .merge_supplement import merge_batches

log = logging.getLogger("cca_translate.build_final")


def _to_orig_shape(item: dict) -> dict:
    """把补充条目映射回原字段 {expr_type, expr, relation_params, src_text, origin}。"""
    src = item.get("src_text", "")
    verdict = item.get("verdict")
    note = item.get("note")
    if verdict or note:
        src = f"{src} | verdict={verdict or ''} | note={note or ''}"
    return {
        "expr_type": item.get("expr_type", "value_dependency"),
        "expr": item.get("expr", ""),
        "relation_params": item.get("relation_params", []),
        "src_text": src,
        "origin": "code",
    }


def build_final(
    original_path: str | Path,
    batch_paths: list[str | Path],
    product: str | None,
) -> tuple[dict, dict]:
    """合成最终文件 + 代码侧 sidecar。

    返回 (final, code_side)：
    - final：与原 constraints.json **同格式**（OperatorRule 兼容，无额外顶层键），
      仅把补充的成功路径约束并入目标产品桶/扁平列表。原 inputs/outputs/return_info/…
      顶层字段原样不动。
    - code_side：原 schema 没有的类(UB/normalize/error/unreachable/stubs/对账日志)——
      OperatorRule 为 extra:forbid，塞进 final 会破坏与文档 constraints.json 的格式一致性，
      故单独成 sidecar 保留，不丢信息。调用方负责把 code_side 写到独立文件。

    original_path: 原 constraints.json（文档提取，保持其原格式）
    batch_paths: 补充批次(含 category 字段的 items + 已分桶的)
    product: 要并入的产品键(如 "Atlas A2 训练系列产品/Atlas A2 推理系列产品")。
        仅当原文件 constraints_in_parameters 为按平台分桶(dict)时需要；扁平列表(list)
        形式时传 None，直接并入列表。
    """
    orig = json.loads(Path(original_path).read_text(encoding="utf-8"))
    final = copy.deepcopy(orig)
    supp = merge_batches(batch_paths)

    cip = final.get("constraints_in_parameters")

    def _append_into(bucket: list) -> int:
        orig_exprs = {c.get("expr") for c in bucket}
        added = 0
        for item in supp["constraints_in_parameters"]:
            expr = item.get("expr", "")
            if item.get("stub") or not expr or expr.startswith("TODO"):
                continue
            if expr in orig_exprs:
                continue
            bucket.append(_to_orig_shape(item))
            orig_exprs.add(expr)
            added += 1
        return added

    if isinstance(cip, dict):
        if not product:
            raise ValueError(
                "原文件 constraints_in_parameters 为按平台分桶(dict)，必须提供 --product；"
                f"可选: {list(cip)}"
            )
        if product not in cip:
            raise ValueError(f"原文件无产品 {product}；可选: {list(cip)}")
        added = _append_into(cip[product])
        log.info("产品 %s 并入 %d 条成功路径约束", product, added)
    elif isinstance(cip, list):
        added = _append_into(cip)
        log.info("扁平列表并入 %d 条成功路径约束", added)
    else:
        raise ValueError(
            f"constraints_in_parameters 既非 dict 也非 list: {type(cip).__name__}"
        )

    # 原 schema 没有的类 → 不塞进 final（OperatorRule extra:forbid 会破坏与文档
    # constraints.json 的格式一致性），单独成 sidecar 保留，不丢信息。
    code_side = {
        "operator_name": supp.get("operator_name"),
        "product": supp.get("product"),
        "error_branches": supp["error_branches"],
        "ub_branches": supp["ub_branches"],
        "normalize_rules": supp["normalize_rules"],
        "unreachable": supp["unreachable"],
        "stubs": supp["stubs"],
        "reconciliation_log": supp.get("reconciliation_log", []),
    }
    return final, code_side
