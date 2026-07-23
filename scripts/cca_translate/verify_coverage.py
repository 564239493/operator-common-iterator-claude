"""verify-coverage：前提保留自检（advisory，不阻断）。

动机
----
cca fn-*.md 的分支 guard（经 cca 内联展开后）已把**多层调用链前提**累积为合取项
（入口层 CheckNotNull 的 `x != nullptr` ∧ 中间层 `if(groupType != SPLIT_K)` 的
`groupType != SPLIT_K` ∧ 末层谓词）。第 4 步 LLM 翻译把「失败分支」翻成「成功路径
约束」时，容易只取末层谓词、把外层 if 守卫合取项丢掉 → 过约束（Z3 仍可满足、仍
0 `[FAIL]`，求解器门禁查不到）。

本步对每条 IR 分支，抽出 guard 里出现的「入参值比较」前提参数，与翻译批次里链到
该分支（按 callee 名）的 code-expr 所引用的入参对账，列出**未被任何 code-expr
保留**的前提参数，提示翻译者复核：

- 多数是丢了 call-site 前提 → 必须补析取逃逸 `not cond or <predicate>`（见
  translate_guard.md「门控/条件守卫检查」规则）。
- 少数是该参数由 doc 已覆盖为值集（consistent） → 确认一致即可，不必再补。

仅做结构化提示，**不硬阻断**（exit 0）。Z3 求解器实测仍是唯一硬门禁。
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("cca_translate.verify_coverage")

# 已知 callee 名（cca source / guard 里出现）。匹配最长者优先。
_KNOWN_CALLEES = [
    "GetGMMResultByL0Api",
    "CheckCommonParam",
    "CheckTransposeStatus",
    "CheckEmptyTensor",
    "CheckFunctionParams",
    "CheckParamDifferentGroupType",
    "CorrectSplitItem",
    "ResetEmptyTensor",
    "UnpackB32ToB4",
    "PreCheckGroupType",
    "CheckNotNull",
]
_CALLEE_RE = re.compile("|".join(sorted(_KNOWN_CALLEES, key=len, reverse=True)))

# 值比较算符（含 CJK ∈ ∉ 与 Python in / not in）
_VALUE_OPS = r"(?:==|!=|∈|∉|<=|>=|<|>|in\b|not\s+in\b)"


def flatten_branches(roots: list, prefix: str = "") -> list[tuple[str, dict]]:
    """DFS 展开分支树，返回 [(path, branch), ...]。"""
    out: list[tuple[str, dict]] = []
    for i, b in enumerate(roots or []):
        path = f"{prefix}{i}" if not prefix else f"{prefix}.{i}"
        out.append((path, b))
        out.extend(flatten_branches(b.get("children", []), path))
    return out


def _input_param_names(doc: dict) -> set[str]:
    return set((doc.get("inputs") or {}).keys())


def _mask_attr_access(guard: str, inputs: set[str]) -> str:
    """把入参的属性/下标访问位（P-> / P. / P[ / (*P)）屏蔽为 §，
    使后续「裸值比较」扫描不会把属性访问误判为值比较前提。"""
    masked = guard
    for p in inputs:
        masked = re.sub(rf"\b{re.escape(p)}\b(?=\s*(?:->|\.|\[))", "§", masked)
        masked = re.sub(rf"\(\*{re.escape(p)}\)", "(§)", masked)
    return masked


def value_compare_params(guard: str, inputs: set[str]) -> set[str]:
    """guard 中以「裸入参 <op> 非空指针常量」形式出现的前提参数。

    排除属性访问（P->.../P[.../P.）与存在性比较（P == nullptr / != nullptr）。
    """
    masked = _mask_attr_access(guard, inputs)
    # 先抹掉存在性比较（nullptr/null），它们属 presence，由 doc `is None` 另行覆盖
    for p in inputs:
        masked = re.sub(rf"\b{re.escape(p)}\b\s*(?:==|!=)\s*nullptr\b", "", masked)
        masked = re.sub(rf"\b{re.escape(p)}\b\s*(?:==|!=)\s*null\b", "", masked)
    found: set[str] = set()
    for p in inputs:
        if re.search(rf"\b{re.escape(p)}\b\s*{_VALUE_OPS}", masked):
            found.add(p)
    return found


def expr_params(expr: str, inputs: set[str]) -> set[str]:
    """expr 里引用的入参名（ast.Name，id ∈ inputs）。stub/TODO 跳过。"""
    if not expr or expr.startswith("TODO"):
        return set()
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in inputs}


def callee_of(text: str) -> str | None:
    """从 source / src_text / branch_ref 文本里抽 callee 名。"""
    if not text:
        return None
    m = _CALLEE_RE.search(text)
    return m.group(0) if m else None


def doc_param_coverage(doc: dict, product: str | None) -> set[str]:
    """doc constraints(某产品) 里被任一 expr 引用的入参集合。"""
    cip = doc.get("constraints_in_parameters", [])
    if isinstance(cip, dict):
        if not product or product not in cip:
            return set()
        conds = cip[product]
    elif isinstance(cip, list):
        conds = cip
    else:
        return set()
    inputs = _input_param_names(doc)
    cov: set[str] = set()
    for c in conds:
        cov |= expr_params(c.get("expr", ""), inputs)
    return cov


def verify(parse_ir: dict, batches: list[dict], doc: dict | None,
           product: str | None) -> dict:
    roots = parse_ir.get("roots", [])
    inputs = _input_param_names(doc) if doc else set()
    # 若无 doc，从 IR guard 里出现的标识符兜底（仍按已知入参名过滤——无 doc 则空）
    branches = flatten_branches(roots)

    # 每条分支的前提参数（值比较）+ callee
    branch_info: list[dict] = []
    for path, b in branches:
        guard = b.get("guard", "") or ""
        src = (b.get("source") or "") + " " + guard
        callee = callee_of(src)
        premise = value_compare_params(guard, inputs) if inputs else set()
        if not premise:
            continue
        branch_info.append({
            "path": path,
            "callee": callee,
            "outcome_kind": b.get("outcome_kind"),
            "premise_params": sorted(premise),
            "guard_excerpt": guard[:160],
        })

    # 批次条目 → (callee -> set(params))；只统计 code 侧约束(喂 z3 / error / ub)
    BUCKETS = ("constraints_in_parameters", "error_branches", "ub_branches")
    callee_to_params: dict[str | None, set[str]] = {}
    for batch in batches:
        items = []
        for b in BUCKETS:
            items.extend(batch.get(b, []))
        for it in items:
            if it.get("stub") or (it.get("expr", "") or "").startswith("TODO"):
                continue
            text = (it.get("src_text") or "") + " " + (it.get("branch_ref") or "")
            cal = callee_of(text)
            ps = expr_params(it.get("expr", ""), inputs)
            callee_to_params.setdefault(cal, set()).update(ps)

    doc_cov = doc_param_coverage(doc, product) if doc else set()

    findings: list[dict] = []
    for bi in branch_info:
        cal = bi["callee"]
        linked = callee_to_params.get(cal, set())
        uncovered = [p for p in bi["premise_params"] if p not in linked]
        if not uncovered:
            continue
        annotated = []
        for p in uncovered:
            annotated.append({
                "param": p,
                "doc_covered": p in doc_cov,
            })
        findings.append({
            "branch_path": bi["path"],
            "callee": cal,
            "outcome_kind": bi["outcome_kind"],
            "premise_params": bi["premise_params"],
            "uncovered_in_code_expr": annotated,
            "guard_excerpt": bi["guard_excerpt"],
            "hint": (
                "复核：该分支 guard 的值比较前提参数未出现在链到本 callee 的 code-expr 里。"
                "若是 call-site if 守卫(如 groupType != SPLIT_K)→ 必须补析取逃逸 "
                "`not cond or <predicate>`(见 translate_guard.md 门控规则)；"
                "若是 callee 自身值集校验且 doc 已覆盖→ 确认一致即可。"
            ),
        })

    return {
        "branches_with_premises": len(branch_info),
        "findings": findings,
        "advisory": True,
        "note": "advisory：不阻断。逐条复核 findings；Z3 求解器实测仍是唯一硬门禁。",
    }
