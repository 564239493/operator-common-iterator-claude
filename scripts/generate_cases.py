#!/usr/bin/env python3
"""Deterministically call the retained business case generator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def serializable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--constraints", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.count < 1:
        raise SystemExit("count must be positive")

    constraints_path = Path(args.constraints)
    output_path = Path(args.output)
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))

    from generators.facade import TestCaseGenerator

    generator = TestCaseGenerator(constraints, seed=args.seed)
    by_platform = generator.generate_by_platform(args.count)
    cases = [serializable(case) for platform_cases in by_platform.values() for case in platform_cases]
    if not cases:
        raise SystemExit("generator produced no cases")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "operator_name": generator.operator_name,
        "requested_per_platform": args.count,
        "platforms": {name: len(items) for name, items in by_platform.items()},
        "total": len(cases),
        "seed": args.seed,
    }
    (output_path.parent / "generation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
