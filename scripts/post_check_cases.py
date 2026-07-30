#!/usr/bin/env python3
"""Post-generation Python-side constraint re-check to catch Z3 pseudo-SAT.

Reads ``constraints_in_parameters[].expr`` per platform and the corresponding
``cases_<platform>.json``, wraps each case's params into an object exposing
``.format``/``.dtype``/``.shape``/``.range_value`` (with ``__eq__`` comparing
``range_value`` so ``<param> in [..]`` and ``<param> == N`` both evaluate),
and ``eval``s each expr with safe builtins.

6.3b eval 保真边界（防误杀通过用例）：
- 每 expr 返回 ``(ok, err)``。**仅 ``err is None 且 ok=False`` 才算真违例**
  （计入 ``violated_count``、可被 generate_cases 过滤）。
- ``err`` 非空（如 ``range_values`` 为 ``[min,max]`` 列表致 ``0 <= list`` 抛
  TypeError）标 ``eval_unreliable``：**绝不**据此过滤或计入违例——防误杀通过用例。
- 既无 clean-False 又无 eval-error 的用例为 fully-verified pass；仅有 eval-error
  的用例为 ``unreliable``（不可信，保留不过滤）。

``run_post_check(cases_dir, constraints_path) -> report`` 为可复用入口（供
``generate_cases.py`` 主流程接入，见 6.3a），CLI ``main()`` 薄封装之。写
``<cases_dir>/post_check_report.json``。

Usage:
    python scripts/post_check_cases.py --iter-dir runs/<run>/iter_001
    python scripts/post_check_cases.py --iter-dir runs/<run>/iter_001 \
        --constraints runs/<run>/iter_001/constraints.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]

try:
    from math import prod as _prod
except ImportError:  # Python < 3.8
    def _prod(it, start=1):
        r = start
        for x in it:
            r *= x
        return r

SAFE_BUILTINS = {
    "len": len,
    "max": max,
    "min": min,
    "abs": abs,
    "sum": sum,
    "prod": _prod,
    "any": any,
    "all": all,
    "range": range,
    "True": True,
    "False": False,
    "None": None,
}


class ParamObj:
    """Wraps a realized case param so attribute-based exprs evaluate.

    ``__eq__`` compares ``range_value`` so ``<param> == N`` and
    ``<param> in [..]`` resolve against the int-scalar value, per the
    skill namespace convention.
    """

    __slots__ = ("dtype", "format", "shape", "range_value")

    def __init__(self, param: dict):
        self.dtype = param.get("dtype")
        self.format = param.get("format")
        self.shape = param.get("shape")
        self.range_value = param.get("range_values")

    def __eq__(self, other):  # noqa: D401
        return self.range_value == other

    def __hash__(self):  # noqa: D401
        return hash(self.range_value)

    def __len__(self):  # noqa: D401
        # len(<param>) → 取 range_value 的长度（list 型参数元素数）。
        # 标量 range_value（int）无长度，len(int) 抛 TypeError → eval_error
        # （诚实：该 expr 对该用例不可信，不过滤，见 6.3b）。
        return len(self.range_value)

    def __repr__(self):  # noqa: D401
        return (
            f"ParamObj(dtype={self.dtype!r}, format={self.format!r}, "
            f"shape={self.shape!r}, range_value={self.range_value!r})"
        )


def collect_params(case: dict) -> dict[str, dict]:
    """Flatten inputs (which may nest lists for tensor_list params) by name."""
    by_name: dict[str, dict] = {}
    for inp in case.get("inputs", []):
        if isinstance(inp, list):
            for it in inp:
                if isinstance(it, dict) and "name" in it:
                    by_name[it["name"]] = it
        elif isinstance(inp, dict) and "name" in inp:
            by_name[inp["name"]] = inp
    return by_name


def eval_expr(expr: str, namespace: dict) -> tuple[bool, str | None]:
    """Return (ok, err). err is None ⇔ eval succeeded (ok then trusted)."""
    try:
        result = eval(expr, {"__builtins__": SAFE_BUILTINS}, namespace)
        return bool(result), None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def run_post_check(cases_dir: Path, constraints_path: Path) -> dict:
    """Core re-check. Reads cases_<platform>.json from cases_dir, evals each
    expr per platform, returns the report dict (also written to
    ``<cases_dir>/post_check_report.json`` by the caller/main).

    Per-platform fields: total_cases / passed / violations / violating_case_ids
    (full list, for filtering) / unreliable_cases / eval_errors /
    expr_violation_counts / sample_violations.
    """
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    cip = constraints.get("constraints_in_parameters", {})
    operator = constraints.get("operator_name")

    report = {
        "operator_name": operator,
        "cases_dir": str(cases_dir),
        "constraints_path": str(constraints_path),
        "platforms": {},
        "overall_violation": False,
        "overall_eval_unreliable": False,
    }

    for platform, entries in cip.items():
        sanitized = platform.replace("/", "_")
        cases_path = cases_dir / f"cases_{sanitized}.json"
        if not cases_path.exists():
            report["platforms"][platform] = {
                "error": f"cases file not found: {cases_path}",
                "violations": 0,
                "violating_case_ids": [],
            }
            report["overall_violation"] = True
            continue

        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        exprs = [(e.get("expr"), e.get("expr_type")) for e in entries if e.get("expr")]

        violations: list[dict] = []
        violating_case_ids: list = []
        pass_count = 0
        unreliable_count = 0
        eval_error_count = 0
        expr_violation_counts: Counter = Counter()
        for case in cases:
            params = collect_params(case)
            namespace = {name: ParamObj(p) for name, p in params.items()}
            case_clean_violation = False   # 至少一个 expr clean-False（真违例）
            case_eval_error = False         # 至少一个 expr 抛错（不可信）
            failed: list[dict] = []
            for expr, etype in exprs:
                ok, err = eval_expr(expr, namespace)
                if err is not None:
                    # 6.3b: eval 不可信——不判违例、不过滤，仅告警
                    case_eval_error = True
                    eval_error_count += 1
                    failed.append(
                        {"expr": expr, "expr_type": etype,
                         "eval_unreliable": True, "eval_error": err}
                    )
                elif not ok:
                    # clean-False：真违例
                    case_clean_violation = True
                    expr_violation_counts[expr] += 1
                    failed.append(
                        {"expr": expr, "expr_type": etype, "eval_unreliable": False}
                    )
            cid = case.get("id")
            if case_clean_violation:
                violating_case_ids.append(cid)
                violations.append(
                    {
                        "case_id": cid,
                        "save_name": case.get("save_name"),
                        "params": {
                            n: {
                                "dtype": p.get("dtype"),
                                "format": p.get("format"),
                                "shape": p.get("shape"),
                                "range_values": p.get("range_values"),
                            }
                            for n, p in params.items()
                        },
                        "failed_exprs": failed,
                    }
                )
            elif case_eval_error:
                unreliable_count += 1
            else:
                pass_count += 1

        report["platforms"][platform] = {
            "cases_path": str(cases_path),
            "total_cases": len(cases),
            "passed": pass_count,
            "violations": len(violations),
            "violating_case_ids": violating_case_ids,
            "unreliable_cases": unreliable_count,
            "eval_errors": eval_error_count,
            "expr_violation_counts": dict(expr_violation_counts),
            "sample_violations": violations[:5],
        }
        if violations:
            report["overall_violation"] = True
        if unreliable_count:
            report["overall_eval_unreliable"] = True

    return report


def write_report(report: dict, cases_dir: Path) -> Path:
    out_path = cases_dir / "post_check_report.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--iter-dir",
        required=True,
        help="迭代/产物目录 (如 runs/<run>/iter_001)，须含 cases_<platform>.json",
    )
    parser.add_argument(
        "--constraints",
        default=None,
        help="constraints.json 路径; 默认 <iter-dir>/constraints.json",
    )
    args = parser.parse_args()

    cases_dir = Path(args.iter_dir)
    constraints_path = (
        Path(args.constraints) if args.constraints else cases_dir / "constraints.json"
    )

    report = run_post_check(cases_dir, constraints_path)
    out_path = write_report(report, cases_dir)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\npost_check_report written to: {out_path}")
    print(f"overall_violation: {report['overall_violation']}")
    print(f"overall_eval_unreliable: {report['overall_eval_unreliable']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
