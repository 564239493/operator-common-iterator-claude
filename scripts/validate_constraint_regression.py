#!/usr/bin/env python3
"""Validate that constraint modifications don't break previously successful cases.

This is a deterministic regression check. It reads:
- Previous iteration's execution_result.json to find passed cases
- Previous iteration's cases.json to get concrete parameter values
- Current iteration's constraints.json (the modified constraints)

For each previously passed case, it evaluates whether the case still satisfies
the new constraints. If any case fails, it's reported as a regression.

Exit codes:
- 0: No regressions (all passed cases still valid)
- 1: Regressions detected, but under max attempts (updater can self-correct)
- 2: Input error (missing files, invalid JSON, etc.)
- 3: Regressions detected AND reached max attempts (user intervention required)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.hs.constraint_evaluator import evaluate_case_relations


def check_regression(
    prev_cases_path: Path,
    prev_execution_result_path: Path,
    new_constraints_path: Path,
) -> dict[str, Any]:
    """Check if modified constraints break previously successful cases.

    Returns a report dict with:
    - ok: bool (True if no regressions)
    - total_passed_cases: int (number of previously passed cases)
    - checked_cases: int (number of cases actually checked)
    - regressions: list of dicts (each with case_id, issues)
    - error: str (only if there was an error loading files)
    """
    # Load previous execution result
    try:
        execution_result = json.loads(prev_execution_result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"Failed to load execution_result.json: {exc}"}

    # Find passed cases and extract numeric IDs
    records = execution_result.get("records", [])
    passed_case_ids = set()
    for record in records:
        if record.get("execution_status") == "PASS" or record.get("status") == "passed":
            testcase_name = record.get("testcase_name")
            case_id = record.get("case_id")
            if testcase_name:
                # Extract numeric ID from testcase_name (e.g., "aclnnGroupedMatmulV5_055" -> 55)
                import re
                match = re.search(r'_(\d+)$', testcase_name)
                if match:
                    passed_case_ids.add(int(match.group(1)))
            elif case_id is not None:
                passed_case_ids.add(case_id)

    if not passed_case_ids:
        return {
            "ok": True,
            "total_passed_cases": 0,
            "checked_cases": 0,
            "regressions": [],
            "note": "No previously passed cases to check",
        }

    # Load previous cases
    try:
        prev_cases = json.loads(prev_cases_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"Failed to load cases.json: {exc}"}

    # Build case_id -> case map (use 'id' field from cases.json)
    case_map = {case.get("id"): case for case in prev_cases if "id" in case}

    # Load new constraints
    try:
        new_constraints = json.loads(new_constraints_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"Failed to load constraints.json: {exc}"}

    # Check each passed case against new constraints
    regressions = []
    for case_id in passed_case_ids:
        if case_id not in case_map:
            continue

        case = case_map[case_id]
        issues = evaluate_case_relations(case, new_constraints, platform=None)

        if issues:
            regressions.append({
                "case_id": case_id,
                "issues": issues,
            })

    return {
        "ok": len(regressions) == 0,
        "total_passed_cases": len(passed_case_ids),
        "checked_cases": len([cid for cid in passed_case_ids if cid in case_map]),
        "regressions": regressions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate that constraint modifications don't break previously successful cases."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        required=True,
        help="Path to previous iteration's cases.json",
    )
    parser.add_argument(
        "--execution-result",
        type=Path,
        required=True,
        help="Path to previous iteration's execution_result.json",
    )
    parser.add_argument(
        "--constraints",
        type=Path,
        required=True,
        help="Path to current iteration's constraints.json (modified constraints)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write regression_check.json",
    )
    parser.add_argument(
        "--attempt",
        type=int,
        default=1,
        help="Current attempt number (1-based), default 1",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum allowed attempts before requiring user intervention, default 3",
    )

    args = parser.parse_args()

    # Validate inputs exist
    if not args.cases.is_file():
        print(f"Error: cases file not found: {args.cases}", file=sys.stderr)
        return 2
    if not args.execution_result.is_file():
        print(f"Error: execution_result file not found: {args.execution_result}", file=sys.stderr)
        return 2
    if not args.constraints.is_file():
        print(f"Error: constraints file not found: {args.constraints}", file=sys.stderr)
        return 2
    if args.attempt < 1:
        print(f"Error: --attempt must be >= 1, got {args.attempt}", file=sys.stderr)
        return 2
    if args.max_attempts < 1:
        print(f"Error: --max-attempts must be >= 1, got {args.max_attempts}", file=sys.stderr)
        return 2

    # Run regression check
    report = check_regression(args.cases, args.execution_result, args.constraints)

    # Add attempt metadata to report
    report["attempt"] = args.attempt
    report["max_attempts"] = args.max_attempts
    report["limit_reached"] = (
        not report["ok"] and args.attempt >= args.max_attempts
    )

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Print summary
    if report.get("error"):
        print(f"Error: {report['error']}", file=sys.stderr)
        return 2

    total = report["total_passed_cases"]
    checked = report.get("checked_cases", total)
    regressions = len(report["regressions"])

    if report["ok"]:
        print(f"No regressions: {checked}/{total} passed cases checked, all still valid")
        return 0

    print(f"Regressions detected: {regressions}/{checked} cases now fail new constraints")
    for reg in report["regressions"]:
        print(f"  - {reg['case_id']}: {len(reg['issues'])} constraint(s) violated")

    if report["limit_reached"]:
        print(f"Reached max attempts ({args.max_attempts}), user intervention required")
        return 3

    print(f"Attempt {args.attempt}/{args.max_attempts}, updater can self-correct")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())