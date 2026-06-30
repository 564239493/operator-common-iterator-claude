#!/usr/bin/env python3
"""Validate stage artifact structure without invoking an LLM."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_constraints(value) -> list[str]:
    if not isinstance(value, dict):
        return ["constraints must be an object"]
    try:
        from generators.common_model_definition import OperatorRule

        OperatorRule(**value)
        return []
    except Exception as exc:
        return [f"OperatorRule validation failed: {exc}"]


def validate_cases(value) -> list[str]:
    if not isinstance(value, list):
        return ["cases must be an array"]
    if not value:
        return ["cases must not be empty"]
    return [f"cases[{index}] must be an object" for index, item in enumerate(value) if not isinstance(item, dict)]


def validate_execution(value) -> list[str]:
    if not isinstance(value, dict):
        return ["execution result must be an object"]
    errors = []
    required = ("status", "mode", "passed", "failed", "total", "records", "engine_error")
    errors.extend(f"missing field: {key}" for key in required if key not in value)
    passed, failed, total = value.get("passed", 0), value.get("failed", 0), value.get("total", 0)
    if all(isinstance(item, int) for item in (passed, failed, total)) and passed + failed != total:
        errors.append("passed + failed must equal total")
    if not isinstance(value.get("records", []), list):
        errors.append("records must be an array")
    return errors


def validate_analysis(value) -> list[str]:
    if not isinstance(value, dict):
        return ["analysis must be an object"]
    allowed = {"constraint_extraction", "generator_bug", "executor_bug"}
    return [] if value.get("root_cause") in allowed else ["invalid root_cause"]


VALIDATORS = {
    "constraints": validate_constraints,
    "cases": validate_cases,
    "execution": validate_execution,
    "analysis": validate_analysis,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=VALIDATORS)
    parser.add_argument("path")
    args = parser.parse_args()
    try:
        errors = VALIDATORS[args.kind](load(args.path))
    except Exception as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
