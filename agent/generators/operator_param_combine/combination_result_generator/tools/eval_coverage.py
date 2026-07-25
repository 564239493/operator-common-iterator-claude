"""
Feature Combination V2.0 — Coverage Evaluator

Usage:
    python tools/eval_coverage.py config.json cases.json [-u uncovered.json]

Evaluates how well an existing set of test cases covers the pair space
defined by a configuration file. Reports coverage rate, illegal pairs
(constraint violation), and missing parameters.
"""

import argparse
import json
import os
import sys

from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue
from agent.generators.operator_param_combine.combination_result_generator.engine import load_config, build_constraint, \
    build_universe_and_tracker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_SAMPLE_TOP = 30


def load_cases(cases_path: str) -> list[dict]:
    with open(cases_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "cases" in data:
        return data["cases"]
    raise ValueError("cases file must be a JSON array or object with 'cases' key")


def _factor_values_from_case(case: dict) -> list[FactorValue]:
    values: list[FactorValue] = []
    for param_name, attrs in case.items():
        for attr_name, val in attrs.items():
            values.append(
                FactorValue(
                    factor=Factor(parameter=param_name, attribute=attr_name),
                    value=val,
                )
            )
    return values


def _format_pair(pair) -> str:
    left = f"{pair.left.factor.name}={pair.left.value}"
    right = f"{pair.right.factor.name}={pair.right.value}"
    return f"{left} | {right}"


def run(config_path: str, cases_path: str, uncovered_path: str | None = None) -> None:
    # ---- build universe ----
    config = load_config(config_path)
    print(f"[config]    loaded {len(config.parameters)} parameter(s)")
    if config.constraints:
        print(f"[config]    loaded {len(config.constraints)} constraint(s)")

    constraint = build_constraint(config.constraints)
    universe, tracker, builder = build_universe_and_tracker(config, constraint)
    universe_size = universe.size()
    print(f"[pair]      universe size = {universe_size} pairs")

    # ---- load cases ----
    cases = load_cases(cases_path)
    print(f"[cases]     loaded {len(cases)} test cases")

    # ---- evaluate ----
    illegal_pairs: list[dict] = []
    covered_param_names: set[str] = set()

    for case_index, case_dict in enumerate(cases, start=1):
        factor_values = _factor_values_from_case(case_dict)
        case_pairs = builder.build(factor_values)

        for param_name in case_dict:
            covered_param_names.add(param_name)

        for pair in case_pairs:
            if universe.contains(pair):
                tracker.update([pair])
            else:
                illegal_pairs.append({
                    "case_index": case_index,
                    "pair": _format_pair(pair),
                    "reason": "pair not in universe (constraint violation or invalid domain)",
                })

    # ---- compute stats ----
    all_param_names = set(config.parameters.keys())
    missing_parameters = sorted(all_param_names - covered_param_names)

    covered_count = tracker.covered_count()
    uncovered_count = universe_size - covered_count
    coverage_rate = tracker.coverage_rate()

    uncovered_pairs = []
    uncovered_indices = tracker.uncovered_indices()
    for idx in uncovered_indices:
        pair = universe.get_by_index(idx)
        if pair is not None:
            uncovered_pairs.append(_format_pair(pair))

    # ---- print report ----
    print()
    print("=" * 70)
    print("  Pairwise Coverage Report")
    print("=" * 70)
    print(f"  config:     {os.path.basename(config_path)}")
    print(f"  cases:      {len(cases)} test cases")
    print(f"  universe:   {universe_size} pairs")
    print()
    print("-" * 70)
    illegal_pct = len(illegal_pairs) / universe_size * 100 if universe_size else 0
    print(f"  Covered:     {covered_count:>6d}  ({coverage_rate * 100:5.1f}%)")
    print(f"  Uncovered:   {uncovered_count:>6d}  ({(1 - coverage_rate) * 100:5.1f}%)")
    print(f"  Illegal:     {len(illegal_pairs):>6d}  ({illegal_pct:5.1f}%)   <- constraint violation")
    print("-" * 70)

    if illegal_pairs:
        print()
        print(f"*** Illegal Pairs (constraint violation, {len(illegal_pairs)} total) ***")
        for entry in illegal_pairs:
            print(f"  case#{entry['case_index']}:  {entry['pair']}")

    if missing_parameters:
        print()
        print(f"*** Missing Parameters ({len(missing_parameters)} total, 0 cases) ***")
        for name in missing_parameters:
            print(f"  {name}  (never assigned in any test case)")

    if uncovered_pairs:
        print()
        sample = uncovered_pairs[:_SAMPLE_TOP]
        print(f"*** Uncovered Pairs (sample {len(sample)} / {len(uncovered_pairs)}) ***")
        for pair_str in sample:
            print(f"  {pair_str}")

    print("=" * 70)

    # ---- save uncovered ----
    if uncovered_path:
        with open(uncovered_path, "w", encoding="utf-8") as f:
            json.dump({
                "config": os.path.basename(config_path),
                "cases_file": os.path.basename(cases_path),
                "total_cases": len(cases),
                "universe_size": universe_size,
                "covered_count": covered_count,
                "uncovered_count": uncovered_count,
                "coverage_rate": coverage_rate,
                "illegal_pairs": illegal_pairs,
                "missing_parameters": missing_parameters,
                "uncovered_pairs": uncovered_pairs,
            }, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n[output] uncovered pairs saved to {uncovered_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pairwise Coverage Evaluator"
    )
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--cases", help="path to cases.json")
    parser.add_argument(
        "-u", "--uncovered", default=None,
        help="output uncovered pairs to JSON file (optional)",
    )
    args = parser.parse_args()
    run(args.config, args.cases, args.uncovered)
