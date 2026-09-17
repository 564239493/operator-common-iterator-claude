# -*- coding: utf-8 -*-
"""Post-generation Python-side constraint re-check for aclnnGroupedMatmulV5.

Per .claude/skills/generate-cases/SKILL.md: catch Z3 pseudo-SAT / unmodeled
value_dependencies by eval'ing each constraints_in_parameters[].expr against
each case. Namespace wraps every param in an object exposing .format/.dtype/
.shape/.range_value with __len__ (tensorList length, None->1 per executor
generator.py:708) and __eq__ comparing range_value. Absent optional params map
to Python None so `xxx is None` guards work. Writes post_check_report.json.
"""
import json
import os
import sys
import traceback

CASES = r"C:/software/operator-common-iterator-claude/runs/aclnnGroupedMatmulV5-20260724-153651-738128/iter_001/cases_Atlas A2 训练系列产品_Atlas A2 推理系列产品.json"
CONS = r"C:/software/operator-common-iterator-claude/runs/aclnnGroupedMatmulV5-20260724-153651-738128/iter_001/constraints.json"
OUT = r"C:/software/operator-common-iterator-claude/runs/aclnnGroupedMatmulV5-20260724-153651-738128/iter_001/post_check_report.json"

SAFE_BUILTINS = {
    "len": len, "max": max, "min": min, "abs": abs, "sum": sum,
    "any": any, "all": all, "range": range, "True": True, "False": False,
    "None": None, "int": int, "float": float, "str": str, "bool": bool,
    "sorted": sorted, "list": list, "tuple": tuple, "enumerate": enumerate,
    "zip": zip, "map": map, "filter": filter,
}


class Param:
    __slots__ = ("format", "dtype", "shape", "range_value", "_length", "_type")

    def __init__(self, p):
        self.format = p.get("format")
        self.dtype = p.get("dtype")
        self.shape = p.get("shape")          # list or None
        self.range_value = p.get("range_values")
        self._length = p.get("length")
        self._type = p.get("type")

    def __len__(self):
        # tensorList / scalar-list types: length field; None -> single entry (1)
        # per executer/resources/generator.py:708 ("length 为 None/0 → 单个条目")
        if self._type in ("tensors", "scalars", "attrs"):
            if self._length is not None and int(self._length) > 0:
                return int(self._length)
            return 1
        raise TypeError("object of type 'Param' (type=%s) has no len()" % self._type)

    def __eq__(self, other):
        ov = other.range_value if isinstance(other, Param) else other
        return self.range_value == ov

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        try:
            return hash(self.range_value)
        except Exception:
            return id(self)

    def __repr__(self):
        return "Param(type=%s,dtype=%s,shape=%s,rv=%s,fmt=%s)" % (
            self._type, self.dtype, self.shape, self.range_value, self.format)


def build_namespace(case):
    ns = dict(SAFE_BUILTINS)
    # all params default to None (absent optional -> None so `is None` works)
    all_names = [
        "x", "weight", "biasOptional", "scaleOptional", "offsetOptional",
        "antiquantScaleOptional", "antiquantOffsetOptional", "perTokenScaleOptional",
        "groupListOptional", "activationInputOptional", "activationQuantScaleOptional",
        "activationQuantOffsetOptional", "splitItem", "groupType", "groupListType",
        "actType", "tuningConfigOptional", "out", "activationFeatureOutOptional",
        "dynQuantScaleOutOptional",
    ]
    for n in all_names:
        ns[n] = None
    for p in case.get("inputs", []):
        ns[p["name"]] = Param(p)
    return ns


def main():
    cases = json.load(open(CASES, encoding="utf-8"))
    cons = json.load(open(CONS, encoding="utf-8"))
    platform = "Atlas A2 训练系列产品/Atlas A2 推理系列产品"
    exprs = cons["constraints_in_parameters"][platform]

    total_cases = len(cases)
    total_exprs = len(exprs)
    violations = []        # {case_id, expr_index, expr, expr_type, value}
    eval_errors = []       # {case_id, expr_index, expr, error}
    per_expr_viol_count = {i: 0 for i in range(total_exprs)}
    per_expr_evalerr_count = {i: 0 for i in range(total_exprs)}
    cases_with_violation = set()

    for case in cases:
        cid = case.get("id")
        ns = build_namespace(case)
        for i, c in enumerate(exprs):
            expr = c["expr"]
            etype = c.get("expr_type")
            try:
                result = eval(expr, {"__builtins__": {}}, ns)
            except Exception as e:
                eval_errors.append({
                    "case_id": cid, "expr_index": i, "expr": expr,
                    "expr_type": etype, "error": f"{type(e).__name__}: {e}",
                })
                per_expr_evalerr_count[i] += 1
                continue
            # truthiness: bool False or 0 or None-treated-as-false -> violation
            if not result:
                violations.append({
                    "case_id": cid, "expr_index": i, "expr": expr,
                    "expr_type": etype, "evaluated": repr(result),
                    "case_summary": _case_summary(case),
                })
                per_expr_viol_count[i] += 1
                cases_with_violation.add(cid)

    # per-expr summary
    per_expr_summary = []
    for i, c in enumerate(exprs):
        per_expr_summary.append({
            "expr_index": i,
            "expr_type": c.get("expr_type"),
            "expr": c["expr"],
            "origin": c.get("origin"),
            "violation_count": per_expr_viol_count[i],
            "eval_error_count": per_expr_evalerr_count[i],
        })

    report = {
        "operator_name": cons.get("operator_name"),
        "platform": platform,
        "cases_total": total_cases,
        "exprs_total": total_exprs,
        "cases_with_violation": len(cases_with_violation),
        "cases_with_violation_ids": sorted(cases_with_violation),
        "cases_clean": total_cases - len(cases_with_violation),
        "total_violation_instances": len(violations),
        "total_eval_error_instances": len(eval_errors),
        "per_expr_summary": per_expr_summary,
        "violations_sample": violations[:40],
        "violations_total_count": len(violations),
        "eval_errors_sample": eval_errors[:30],
        "eval_errors_total_count": len(eval_errors),
        "namespace_notes": (
            "Param wraps each present param: .format/.dtype/.shape(list|None)/"
            ".range_value; __len__ for type in {tensors,scalars,attrs} = length "
            "field or 1 if None (per executer/resources/generator.py:708: "
            "'length 为 None/0 -> single entry'); absent optional params -> "
            "Python None so `xxx is None` guards work. Scalars also wrapped."
        ),
    }
    json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("Wrote", OUT)
    print("cases_total:", total_cases)
    print("exprs_total:", total_exprs)
    print("cases_with_violation:", len(cases_with_violation))
    print("  ids:", sorted(cases_with_violation))
    print("cases_clean:", total_cases - len(cases_with_violation))
    print("total_violation_instances:", len(violations))
    print("total_eval_error_instances:", len(eval_errors))
    print()
    print("=== per-expr violation/evalerr counts (viol>0 or err>0) ===")
    for row in per_expr_summary:
        if row["violation_count"] or row["eval_error_count"]:
            print(f"  [{row['expr_index']:2d}] viol={row['violation_count']:3d} "
                  f"err={row['eval_error_count']:3d} type={row['expr_type']} "
                  f"expr={row['expr']}")


def _case_summary(case):
    parts = []
    for p in case.get("inputs", []):
        parts.append("%s(dt=%s,sh=%s,rv=%s,len=%s)" % (
            p.get("name"), p.get("dtype"), p.get("shape"),
            p.get("range_values"), p.get("length")))
    return " ".join(parts)


if __name__ == "__main__":
    main()
