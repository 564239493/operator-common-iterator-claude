#!/usr/bin/env python3
"""Cross-check diagnostic findings against a proposed supplement patch.

This is a deterministic pre-apply gate.  Semantic case evaluation remains the
constraint-checker's job, but missing finding coverage and all-noop patches are
blocked before another generation round can start.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from apply_supplement_constraints import apply_patch
from validate_artifacts import validate_analysis, validate_constraints_patch


def validate_effect(analysis: dict, constraints: dict, patch: list[dict]) -> list[str]:
    errors = validate_analysis(analysis) + validate_constraints_patch(patch)
    if errors:
        return errors

    findings = analysis.get("constraint_findings", [])
    decision = analysis.get("supplement_decision", {})
    if decision.get("has_explicit_additions") is not True:
        return errors
    expected_ids = {item["id"] for item in findings}
    covered_ids: set[str] = set()
    for item in patch:
        covered_ids.update(item.get("finding_ids", []))
    missing = sorted(expected_ids - covered_ids)
    unknown = sorted(covered_ids - expected_ids)
    if missing:
        errors.append("constraint findings not covered by patch: " + ", ".join(missing))
    if unknown:
        errors.append("patch references unknown finding ids: " + ", ".join(unknown))
    if errors:
        return errors

    try:
        _, operations = apply_patch(copy.deepcopy(constraints), patch)
    except ValueError as exc:
        return [f"patch dry-run failed: {exc}"]
    if operations and all(operation.startswith("noop-") for operation in operations):
        errors.append(
            "diagnostic patch is entirely noop; it cannot count as a repaired constraint"
        )
    if not operations and expected_ids:
        errors.append("explicit constraint findings require a non-empty patch")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="校验 analysis findings 被 patch 覆盖且 patch 不是全量 noop。"
    )
    parser.add_argument("analysis")
    parser.add_argument("constraints")
    parser.add_argument("patch")
    args = parser.parse_args()
    paths = [Path(value).resolve() for value in (args.analysis, args.constraints, args.patch)]
    try:
        analysis, constraints, patch = [
            json.loads(path.read_text(encoding="utf-8")) for path in paths
        ]
        if not isinstance(analysis, dict) or not isinstance(constraints, dict):
            raise ValueError("analysis and constraints must be JSON objects")
        if not isinstance(patch, list):
            raise ValueError("patch must be a JSON array")
        errors = validate_effect(analysis, constraints, patch)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    payload = {"ok": not errors, "errors": errors}
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
