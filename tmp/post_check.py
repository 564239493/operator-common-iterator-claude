"""Post-generation Python-side constraint re-check (catch Z3 pseudo-SAT).

Reads constraints.json + generation_summary.json (per-platform case files),
evaluates each constraints_in_parameters[platform].expr against every case using
a safe namespace. Wraps each case parameter as an object exposing
.range_value/.format/.dtype/.shape with __len__/__eq__ so exprs like
`2 <= len(tensorShape) <= 6` and `weightTensorSize.range_value > 0` evaluate.
Writes post_check_report.json.
"""
import json
import sys
from pathlib import Path


SAFE_BUILTINS = {
    "len": len, "max": max, "min": min, "abs": abs, "sum": sum,
    "any": any, "all": all,
    "True": True, "False": False, "None": None,
}


class Param:
    """Wraps a case input dict so expr referencing <name>.range_value /
    len(<name>) / <name> == N / <name> in [...] all evaluate."""

    __slots__ = ("_raw", "format", "dtype", "shape", "range_value", "length")

    def __init__(self, raw):
        self._raw = raw or {}
        self.format = self._raw.get("format")
        self.dtype = self._raw.get("dtype")
        self.shape = self._raw.get("shape")
        # case field is `range_values` (plural); expr uses `.range_value` (singular)
        self.range_value = self._raw.get("range_values")
        self.length = self._raw.get("length")

    def __len__(self):
        rv = self._raw.get("range_values")
        if isinstance(rv, list):
            return len(rv)
        ln = self._raw.get("length")
        return ln if isinstance(ln, int) else 0

    def __eq__(self, other):
        return self.range_value == other

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.range_value)

    def __iter__(self):
        rv = self._raw.get("range_values")
        return iter(rv) if isinstance(rv, list) else iter([rv])

    def __getitem__(self, i):
        rv = self._raw.get("range_values")
        return rv[i] if isinstance(rv, list) else rv

    def __contains__(self, item):
        rv = self._raw.get("range_values")
        return item in rv if isinstance(rv, list) else (item == rv)

    def __repr__(self):
        return f"Param(range_value={self.range_value!r}, length={self.length})"


def build_namespace(case):
    ns = {}
    for inp in case.get("inputs", []):
        name = inp.get("name")
        if name:
            ns[name] = Param(inp)
    # Some exprs may reference outputs; outputs appear as attr inputs in this
    # generator's compact form, so they are already in ns. Add outputs scalar
    # name mapping too if case has a dict outputs field (defensive).
    return ns


def eval_expr(expr, case):
    ns = build_namespace(case)
    try:
        return bool(eval(expr, {"__builtins__": SAFE_BUILTINS}, ns)), None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def main(iter_dir: Path):
    iter_dir = Path(iter_dir)
    constraints = json.loads((iter_dir / "constraints.json").read_text(encoding="utf-8"))
    summary = json.loads((iter_dir / "generation_summary.json").read_text(encoding="utf-8"))

    per_platform_files = summary["per_platform_files"]
    cip = constraints.get("constraints_in_parameters", {})

    report = {
        "operator_name": constraints.get("operator_name"),
        "total_cases": summary.get("total"),
        "platforms": {},
        "overall_violations": 0,
        "overall_errors": 0,
    }

    for platform, exprs in cip.items():
        rel = per_platform_files.get(platform)
        if rel is None:
            # try fuzzy match on sanitized filename
            continue
        cases_path = Path(rel)
        if not cases_path.is_absolute():
            cases_path = (Path.cwd() / rel) if not Path(rel).exists() else Path(rel)
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        plat_report = {
            "cases": len(cases),
            "exprs": [e["expr"] for e in exprs],
            "violations": [],
            "eval_errors": [],
        }
        for ci, case in enumerate(cases):
            for ei, e in enumerate(exprs):
                expr = e["expr"]
                ok, err = eval_expr(expr, case)
                if err is not None:
                    plat_report["eval_errors"].append({
                        "case_id": case.get("id"), "expr": expr, "error": err,
                    })
                    report["overall_errors"] += 1
                elif not ok:
                    plat_report["violations"].append({
                        "case_id": case.get("id"), "expr": expr,
                        "inputs": [inp.get("name") + "=" + str(inp.get("range_values")) for inp in case.get("inputs", [])],
                    })
                    report["overall_violations"] += 1
        plat_report["violations_count"] = len(plat_report["violations"])
        plat_report["eval_errors_count"] = len(plat_report["eval_errors"])
        report["platforms"][platform] = plat_report

    report["has_pseudo_sat_violations"] = report["overall_violations"] > 0
    out = iter_dir / "post_check_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
