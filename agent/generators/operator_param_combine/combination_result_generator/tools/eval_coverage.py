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
from datetime import datetime

from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue
from agent.generators.operator_param_combine.combination_result_generator.engine import load_config, build_constraint, \
    build_universe_and_tracker
from agent.generators.operator_param_combine.combination_result_generator.engine import \
        _PARAMETER_ATTRIBUTE_KEYS
from agent.generators.data_definition.constants import DataMatchMap
from operator_param_combine.combination_result_generator.model.generator_config import GeneratorConfig
from operator_param_combine.combination_result_generator.model.parameter_model import ParameterModel
from agent.generators.common_utils.logger_util import init_logger, LazyLogger

logger = LazyLogger()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_SAMPLE_TOP = 30

def _load_config_with_dtype_mapping(config_path: str) -> GeneratorConfig:
    """加载 domain config 并将 dtype 从 ACL 全格式映射为 ATK 短格式。"""


    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data.get("parameters"):
        logger.warning(f"Domain data has no parameters: {config_path}")

    dtype_map = DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP
    # 构建 ACL → ATK 反向映射（全大写优先，非 ACL_ 前缀优先）
    _acl_to_atk = {}
    for k, v in dtype_map.items():
        if k == k.upper():
            if v not in _acl_to_atk:
                _acl_to_atk[v] = k
            elif not k.startswith("ACL_"):
                _acl_to_atk[v] = k

    import re
    # 约束中 dtype 引用映射：将 'FLOAT16'/'ACL_FLOAT16' 替换为 'fp16'
    def _replace_dtype_in_expr(expr):
        for atk, acl in _acl_to_atk.items():
            expr = re.sub(rf"'{acl}'", f"'{atk}'", expr)
            expr = re.sub(rf'"{acl}"', f'"{atk}"', expr)
        return expr

    parameters = {}
    for param_name, attrs in data["parameters"].items():
        kwargs = {"name": param_name}
        for key, values in attrs.items():
            if key in _PARAMETER_ATTRIBUTE_KEYS:
                if key == "dtype":
                    # FLOAT16 → fp16, BFLOAT16 → bf16, ...
                    values = tuple(dtype_map.get(v, v) for v in values)
                kwargs[key] = tuple(values)
        parameters[param_name] = ParameterModel(**kwargs)

    constraints = tuple(_replace_dtype_in_expr(e) for e in data.get("constraints", []))
    return GeneratorConfig(parameters=parameters, constraints=constraints)


def load_cases(cases_path: str) -> list[dict]:
    with open(cases_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        if len(data) == 0:
            logger.warning(f"Case file has empty case list: {cases_path}")
        return data
    if isinstance(data, dict) and "cases" in data:
        if len(data["cases"]) == 0:
            logger.warning(f"Case file has empty cases array: {cases_path}")
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
    config = _load_config_with_dtype_mapping(config_path)
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


def batch_run(config_dir: str, cases_dir: str, output_dir: str | None = None,
              report_path: str | None = None) -> dict[str, dict]:
    """对目录下的每个算子执行覆盖率评估。

    Args:
        config_dir: domain_data JSON 文件所在目录
        cases_dir: case_abstract_data JSON 文件所在目录
        output_dir: 可选的详细报告输出目录
        report_path: 可选的汇总报告保存路径

    Returns:
        {operator_name: {total_cases, universe_size, covered, uncovered, coverage_rate, illegal, missing_params}}
    """
    init_logger(log_name="batch_eval", log_dir=os.path.join(config_dir, "logs"))

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    if output_dir and not report_path:
        report_path = os.path.join(output_dir, "report", "coverage_summary.txt")

    domain_files = sorted(
        f for f in os.listdir(config_dir)
        if f.endswith("_domain_data.json")
    )

    results = {}
    total_operators = 0
    total_covered = 0
    total_universe = 0

    lines: list[str] = []
    def emit(*args, **kwargs):
        kwargs.setdefault("file", None)
        s = " ".join(str(a) for a in args) if args else ""
        print(s)
        lines.append(s)

    emit()
    emit("=" * 120)
    emit(f"  Batch Coverage Evaluation")
    emit(f"  config dir: {config_dir}")
    emit(f"  cases  dir: {cases_dir}")
    emit(f"  generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    emit("=" * 120)
    emit(f"  {'Operator':<40} {'Cases':>6} {'Universe':>10} {'2-Pair':>8} {'2-Pair rate':>7} {'Illegal':>8} {'Missing':>8} {'1-Pair':>8} {'1-Pair rate':>7}")
    emit(f"  {'-'*40} {'-'*6} {'-'*10} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*7}")

    for domain_file in domain_files:
        operator_name = domain_file.replace("_domain_data.json", "")
        config_path = os.path.join(config_dir, domain_file)
        case_file = f"{operator_name}_case_abstract_data.json"
        cases_path = os.path.join(cases_dir, case_file)

        if not os.path.exists(cases_path):
            emit(f"  {operator_name:<40} {'N/A':>6} {'N/A':>10} {'N/A':>8} {'N/A':>7} {'N/A':>8} {'N/A':>8}  [no cases file]")
            continue

        try:
            config = _load_config_with_dtype_mapping(config_path)
        except Exception as e:
            emit(f"  {operator_name:<40} {'ERR':>6} {'ERR':>10} {'ERR':>8} {'ERR':>7} {'ERR':>8} {'ERR':>8}  [{e}]")
            continue
        if not config.parameters:
            logger.warning(f"Config for '{operator_name}' has 0 parameters, universe will be empty")

        constraint = build_constraint(config.constraints)
        universe, tracker, builder = build_universe_and_tracker(config, constraint)
        universe_size = universe.size()

        # 1-pair universe: 每个参数的单个属性取值
        pair1_factors: list[FactorValue] = []
        for param in config.parameters.values():
            for attr_name, domain in param.attributes().items():
                if domain:
                    for val in domain:
                        pair1_factors.append(FactorValue(Factor(param.name, attr_name), val))
        pair1_universe_size = len(pair1_factors)
        valid_pair1_keys = {f"{fv.factor.name}={fv.factor.attribute}={fv.value}" for fv in pair1_factors}
        covered_pair1: set[str] = set()

        cases = load_cases(cases_path)
        if len(cases) == 0:
            logger.warning(f"Cases for '{operator_name}' is empty, no pairs to evaluate")
        illegal_count = 0
        covered_param_names: set[str] = set()

        for case_dict in cases:
            factor_values = _factor_values_from_case(case_dict)
            case_pairs = builder.build(factor_values)
            for param_name in case_dict:
                covered_param_names.add(param_name)
            for pair in case_pairs:
                if universe.contains(pair):
                    tracker.update([pair])
                else:
                    illegal_count += 1
            for fv in factor_values:
                fv_key = f"{fv.factor.name}={fv.factor.attribute}={fv.value}"
                if fv_key in valid_pair1_keys:
                    covered_pair1.add(fv_key)

        covered_count = tracker.covered_count()
        uncovered_count = universe_size - covered_count
        coverage_rate = tracker.coverage_rate()
        missing_params = sorted(set(config.parameters.keys()) - covered_param_names)

        total_operators += 1
        total_covered += covered_count
        total_universe += universe_size

        pair1_covered = len(covered_pair1)
        pair1_coverage_rate = pair1_covered / pair1_universe_size if pair1_universe_size else 0

        results[operator_name] = {
            "total_cases": len(cases),
            "universe_size": universe_size,
            "covered_count": covered_count,
            "uncovered_count": uncovered_count,
            "coverage_rate": coverage_rate,
            "illegal_pairs": illegal_count,
            "missing_parameters": missing_params,
            "pair1_universe_size": pair1_universe_size,
            "pair1_covered": pair1_covered,
            "pair1_coverage_rate": pair1_coverage_rate,
        }

        coverage_pct = coverage_rate * 100
        pair1_pct = pair1_coverage_rate * 100
        emit(f"  {operator_name:<40} {len(cases):>6} {universe_size:>10} {covered_count:>8} {coverage_pct:>6.1f}% {illegal_count:>8} {len(missing_params):>8} {pair1_covered:>8} {pair1_pct:>6.1f}%")

        if output_dir:
            os.makedirs(os.path.join(output_dir, "report"), exist_ok=True)
            out_path = os.path.join(output_dir, "report", f"{operator_name}_coverage_report.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(results[operator_name] | {
                    "operator_name": operator_name,
                    "missing_parameters": missing_params,
                    "pair1_universe_size": pair1_universe_size,
                    "pair1_covered": pair1_covered,
                    "pair1_coverage_rate": pair1_coverage_rate,
                }, f, indent=2, ensure_ascii=False, default=str)

    avg_rate = total_covered / total_universe * 100 if total_universe else 0
    total_pair1_uni = sum(r.get("pair1_universe_size", 0) for r in results.values())
    total_pair1_cov = sum(r.get("pair1_covered", 0) for r in results.values())
    avg_pair1_rate = total_pair1_cov / total_pair1_uni * 100 if total_pair1_uni else 0
    emit(f"  {'-'*40} {'-'*6} {'-'*10} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*7}")
    emit(f"  {'TOTAL':<40} {sum(r['total_cases'] for r in results.values()):>6} {total_universe:>10} {total_covered:>8} {avg_rate:>6.1f}% {'-':>8} {total_pair1_cov:>8} {avg_pair1_rate:>6.1f}%")
    emit("=" * 120)
    emit()

    if report_path:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pairwise Coverage Evaluator"
    )
    sub = parser.add_subparsers(dest="command")

    single = sub.add_parser("single", help="evaluate single operator")
    single.add_argument("--config", help="path to config.json")
    single.add_argument("--cases", help="path to cases.json")
    single.add_argument("-u", "--uncovered", default=None, help="output uncovered pairs to JSON file")

    batch = sub.add_parser("batch", help="evaluate all operators in directory")
    batch.add_argument("--config-dir", required=True, help="directory with domain_data JSON files")
    batch.add_argument("--cases-dir", required=True, help="directory with case_abstract_data JSON files")
    batch.add_argument("-o", "--output-dir", default=None, help="save per-operator reports")
    batch.add_argument("-r", "--report", default=None, help="save summary report to file")

    args = parser.parse_args()
    if args.command == "batch":
        batch_run(args.config_dir, args.cases_dir, args.output_dir, args.report)
    elif args.command == "single" or args.config:
        run(args.config or args.cases, args.cases or args.config, args.uncovered)
    else:
        parser.print_help()
