"""文档约束 vs cca 行为划分 对账。

确定性“参数共现缺口检测”：不做语义等价判定（cca guard 是冗长自然语言式，
等价判定需 LLM/人工），只做参数集合的交集匹配，定位：
- 文档条件无 cca 候选 → 疑似 oracle_only（文档有、代码 host 不查）
- cca 分支无文档候选 → 疑似 code_only（代码有、文档缺 → 补充）

参数来源：文档条件用 relation_params；cca 分支从 guard 文本抽标识符。
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from .cca_parse import Branch

log = logging.getLogger("cca_translate.reconcile")

# 标识符黑名单：从 guard 抽参数时排除这些语言/类型词
_STOPWORDS = {
    "DataType", "nullptr", "ACLNN_SUCCESS", "ACLNN_ERR_PARAM_NULLPTR",
    "ACLNN_ERR_PARAM_INVALID", "ACLNN_ERR_INNER_NULLPTR", "ACLNN_ERR_INNER",
    "true", "false", "return", "Size", "GetDataType", "GetViewShape",
    "GetDimNum", "GetDim", "GetStorageShape", "GetStorageFormat", "static_cast",
    "uint32_t", "int64_t", "bool", "int", "void", "const", "nullptr",
    "gmm", "GMMApiVersion", "V5", "GMMActType", "GMM_ACT_TYPE_NONE",
    "SPLIT_K", "SPLIT_M", "NO_SPLIT", "SPLIT_N", "X_Y_SEPARATED",
    "Y_SEPARATED", "X_SEPARATED", "GROUP_LIST_SPARSE_M",
    "DT_INT32", "DT_INT4", "DT_FLOAT", "DT_BFLOAT16", "DT_FLOAT16",
    "DAV_3510", "DAV_2002", "DAV_2201", "NpuArch", "UnpackB32ToB4",
    "CheckNotNull", "CheckCommonParam", "CheckParam", "Common", "Total",
    "total", "partial", "UB", "Outcome",
}
_IDENT_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")


def extract_guard_idents(guard: str) -> set[str]:
    return {m for m in _IDENT_RE.findall(guard) if m not in _STOPWORDS and not m.startswith("FORMAT_")}


def _flatten(branches: list[Branch]) -> list[Branch]:
    out: list[Branch] = []
    for b in branches:
        out.append(b)
        out.extend(_flatten(b.children))
    return out


@dataclass
class DocGap:
    expr: str
    expr_type: str | None
    relation_params: list[str]
    cca_candidates: list[int] = field(default_factory=list)  # 分支在扁平列表中的下标
    verdict: str = "unknown"  # matched / doc_only(无候选)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CcaGap:
    index: int
    guard: str
    outcome_kind: str | None
    idents: set[str]
    doc_candidates: list[int] = field(default_factory=list)
    verdict: str = "unknown"  # matched / code_only(无候选)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["idents"] = sorted(d["idents"])
        return d


def reconcile(
    doc_conditions: list[dict],
    cca_branches: list[Branch],
    min_overlap: float = 0.5,
) -> dict:
    """参数共现对账。

    doc_conditions: constraints.json 里某产品的 conditions 列表
      （每项含 expr/expr_type/relation_params）。
    cca_branches: parse_behavior_partition 的根列表。
    min_overlap: 文档条件参数中至少该比例出现在 cca 分支 guard 才算候选。
    """
    flat = _flatten(cca_branches)
    # 预算每个 cca 分支的标识符集合
    cca_idents = [extract_guard_idents(b.guard) for b in flat]

    doc_gaps: list[DocGap] = []
    for di, cond in enumerate(doc_conditions):
        params = list(cond.get("relation_params") or [])
        param_set = set(params)
        cands = []
        for ci, idents in enumerate(cca_idents):
            if not param_set:
                continue
            overlap = len(param_set & idents) / len(param_set)
            if overlap >= min_overlap:
                cands.append(ci)
        verdict = "matched" if cands else "doc_only"
        doc_gaps.append(
            DocGap(
                expr=cond.get("expr", ""),
                expr_type=cond.get("expr_type"),
                relation_params=params,
                cca_candidates=cands,
                verdict=verdict,
            )
        )

    cca_gaps: list[CcaGap] = []
    for ci, (b, idents) in enumerate(zip(flat, cca_idents, strict=True)):
        cands = []
        if idents:
            for di, cond in enumerate(doc_conditions):
                params = set(cond.get("relation_params") or [])
                if not params:
                    continue
                overlap = len(params & idents) / len(params)
                if overlap >= min_overlap:
                    cands.append(di)
        verdict = "matched" if cands else "code_only"
        cca_gaps.append(
            CcaGap(
                index=ci,
                guard=b.guard,
                outcome_kind=b.outcome_kind,
                idents=idents,
                doc_candidates=cands,
                verdict=verdict,
            )
        )

    doc_only = sum(1 for g in doc_gaps if g.verdict == "doc_only")
    code_only = sum(1 for g in cca_gaps if g.verdict == "code_only")
    log.info(
        "对账完成：文档 %d 条(matched %d / doc_only %d)；cca %d 分支(matched %d / code_only %d)",
        len(doc_gaps), len(doc_gaps) - doc_only, doc_only,
        len(cca_gaps), len(cca_gaps) - code_only, code_only,
    )
    return {
        "summary": {
            "doc_total": len(doc_gaps),
            "doc_only": doc_only,
            "cca_total": len(cca_gaps),
            "cca_code_only": code_only,
        },
        "doc_gaps": [g.to_dict() for g in doc_gaps],
        "cca_gaps": [g.to_dict() for g in cca_gaps],
    }
