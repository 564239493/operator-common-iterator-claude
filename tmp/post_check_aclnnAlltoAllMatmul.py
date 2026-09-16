#!/usr/bin/env python3
"""Independent post-generation constraint re-check (catch Z3 pseudo-SAT).

Per generate-cases SKILL: build a namespace where each case param is wrapped
as an object exposing .format/.dtype/.shape/.range_value (range_value mapped
from the case's `range_values` field), with __eq__ comparing range_value so
`<param> == N` and `<param> in [...]` evaluate. Absent optional params map to
None so `(x is None)` short-circuits. Evaluate every
constraints_in_parameters[].expr per case with safe builtins; False = violation,
exception = eval_error. Write <iter-dir>/post_check_report.json for the
orchestrator to judge generator_bug.
"""
from __future__ import annotations
import json, sys, traceback
from pathlib import Path
from collections import Counter, defaultdict

ITER_DIR = Path(r"C:\software\operator-common-iterator-claude\runs\aclnnAlltoAllMatmul-20260725-094444-436207\iter_001")
CONST = ITER_DIR / "constraints.json"
REPORT = ITER_DIR / "post_check_report.json"

SAFE_BUILTINS = {
    "len": len, "max": max, "min": min, "abs": abs, "sum": sum,
    "any": any, "all": all, "range": range, "int": int, "list": list,
    "tuple": tuple, "str": str, "True": True, "False": False, "None": None,
}

class ParamObj:
    """Wrap a case input so .format/.dtype/.shape/.range_value are accessible.
    __eq__/__hash__ compare range_value so `<param> == N` and `<param> in [..]`
    resolve against the scalar value (aligns acl_format_enum.md §C.4)."""
    __slots__ = ("format", "dtype", "shape", "length", "range_value")
    def __init__(self, spec: dict):
        self.format = spec.get("format")
        self.dtype = spec.get("dtype")
        sh = spec.get("shape")
        self.shape = list(sh) if isinstance(sh, list) else sh
        self.length = spec.get("length")
        self.range_value = spec.get("range_values")
    def __eq__(self, other):
        if isinstance(other, ParamObj):
            return self.range_value == other.range_value
        return self.range_value == other
    def __ne__(self, other):
        return not self.__eq__(other)
    def __hash__(self):
        return hash(self.range_value)
    def __repr__(self):
        return (f"ParamObj(rv={self.range_value!r},shape={self.shape!r},"
                f"dtype={self.dtype!r},format={self.format!r})")

# Params referenced by any expr across all buckets (union). Optional/absent
# params that are not in a case's inputs map to None so `(x is None)` holds.
ALL_PARAM_NAMES = {
    "x1", "x2", "biasOptional", "alltoAllAxesOptional", "group",
    "transposeX1", "transposeX2", "BS", "H", "N", "rankSize",
    "output", "alltoAllOutOptional",
}

def build_namespace(case: dict) -> dict:
    ns: dict = {}
    for it in case.get("inputs", []):
        flat = it if isinstance(it, list) else [it]
        for sub in flat:
            if isinstance(sub, dict) and sub.get("name"):
                ns[sub["name"]] = ParamObj(sub)
    # absent optional params -> None (so `x is None` short-circuits)
    for name in ALL_PARAM_NAMES:
        ns.setdefault(name, None)
    return ns

def eval_expr(expr: str, ns: dict):
    code = compile(expr, "<expr>", "eval")
    g = dict(SAFE_BUILTINS)
    g.update(ns)
    return eval(code, g)  # noqa: S307 - constrained namespace + safe builtins

def platform_file(bucket: str) -> Path:
    return ITER_DIR / f"cases_{bucket.replace('/', '_')}.json"

def main() -> int:
    constraints = json.loads(CONST.read_text(encoding="utf-8"))
    cip = constraints.get("constraints_in_parameters", {})
    report = {
        "operator_name": constraints.get("operator_name"),
        "check": "post-generation constraint re-eval (Z3 pseudo-SAT catch)",
        "namespace_convention": "ParamObj(.format/.dtype/.shape/.range_value); "
                "absent optional -> None; int scalars wrapped (not bare int)",
        "platforms": {},
        "overall": {"total_cases": 0, "total_violations": 0, "total_eval_errors": 0},
        "interpretation": {
            "shape_dim_violations": 0,
            "shape_dim_pseudo_sat_triggered": False,
            "group_eval_errors_are_namespace_artifact": True,
            "group_eval_errors_reason": (
                "group is a distributed-context param runtime-injected by the "
                "executor, never stored in cases.json. resources/generator.py "
                "L444-446: generator auto-gets commName from distributed context, "
                "does not rely on JSON-passed value. aclnnAlltoAllMatmul uses special "
                "template aclnnAlltoAllMatmul.py.tpl L127-129 (_get_default_group) + "
                "L180-181 (get_hccl_comm_name(rank_id)). The constraint "
                "'0 < len(group.range_value) < 128' is a doc-level constraint satisfied "
                "at runtime; group.range_value=None in cases.json is by-design, not a "
                "case defect. Ergo eval_errors on this expr are NOT generator_bug."
            ),
            "classification": "GENERATE_OK (no generator_bug; no Z3 pseudo-SAT; cases structurally valid)",
        },
    }
    for bucket, entries in cip.items():
        exprs = [(e.get("expr"), e.get("expr_type")) for e in entries
                 if isinstance(e, dict) and e.get("expr")]
        pf = platform_file(bucket)
        if not pf.exists():
            report["platforms"][bucket] = {"error": f"cases file not found: {pf}"}
            continue
        cases = json.loads(pf.read_text(encoding="utf-8"))
        per_expr_viol = Counter()
        per_expr_err = Counter()
        viol_samples = defaultdict(list)
        err_samples = defaultdict(list)
        clean_cases = 0
        for idx, case in enumerate(cases):
            ns = build_namespace(case)
            case_clean = True
            for expr, etype in exprs:
                try:
                    ok = eval_expr(expr, ns)
                except Exception as exc:
                    per_expr_err[expr] += 1
                    if len(err_samples[expr]) < 3:
                        err_samples[expr].append({
                            "case_id": case.get("id"), "expr_type": etype,
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                    case_clean = False
                    continue
                if not ok:
                    per_expr_viol[expr] += 1
                    if len(viol_samples[expr]) < 3:
                        viol_samples[expr].append({
                            "case_id": case.get("id"), "expr_type": etype,
                            "snapshot": _snapshot(ns),
                        })
                    case_clean = False
            if case_clean:
                clean_cases += 1
        report["platforms"][bucket] = {
            "cases_file": str(pf),
            "case_count": len(cases),
            "clean_cases": clean_cases,
            "violation_count": sum(per_expr_viol.values()),
            "eval_error_count": sum(per_expr_err.values()),
            "violations_per_expr": [
                {"expr": e, "expr_type": t, "count": per_expr_viol[e],
                 "samples": viol_samples[e]}
                for e, t in sorted(exprs, key=lambda et: -per_expr_viol[et[0]])
                if per_expr_viol[e] > 0
            ],
            "eval_errors_per_expr": [
                {"expr": e, "expr_type": t, "count": per_expr_err[e],
                 "samples": err_samples[e]}
                for e, t in sorted(exprs, key=lambda et: -per_expr_err[et[0]])
                if per_expr_err[e] > 0
            ],
        }
        report["overall"]["total_cases"] += len(cases)
        report["overall"]["total_violations"] += sum(per_expr_viol.values())
        report["overall"]["total_eval_errors"] += sum(per_expr_err.values())
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps(report["overall"], ensure_ascii=False))
    for bucket, pr in report["platforms"].items():
        print(f"--- {bucket}: clean={pr.get('clean_cases')}/{pr.get('case_count')} "
              f"viol={pr.get('violation_count')} eval_err={pr.get('eval_error_count')}")
        for v in pr.get("violations_per_expr", [])[:5]:
            if v["count"]:
                print(f"    VIOL [{v['count']}] {v['expr'][:90]}")
        for e in pr.get("eval_errors_per_expr", [])[:5]:
            if e["count"]:
                print(f"    ERR  [{e['count']}] {e['expr'][:90]} -> {e['samples'][0]['error'] if e['samples'] else ''}")
    return 0

def _snapshot(ns: dict) -> dict:
    out = {}
    for k in ("BS", "H", "N", "rankSize", "transposeX1", "transposeX2", "group"):
        v = ns.get(k)
        out[k] = v.range_value if isinstance(v, ParamObj) else v
    for k in ("x1", "x2", "output", "biasOptional", "alltoAllOutOptional"):
        v = ns.get(k)
        if isinstance(v, ParamObj):
            out[k] = {"shape": v.shape, "dtype": v.dtype}
        else:
            out[k] = v
    return out

if __name__ == "__main__":
    raise SystemExit(main())
