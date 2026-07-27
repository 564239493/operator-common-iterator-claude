"""
Feature Combination V2.0 — Shared Pipeline Engine

Provides reusable functions for config loading, constraint compilation,
and pair universe construction. Used by both the generation entry point
and coverage evaluation tool.
"""

import json

from agent.generators.operator_param_combine.combination_result_generator.constraint.interfaces import \
    ConstraintProtocol
from agent.generators.operator_param_combine.combination_result_generator.coverage import Pair, PairExistenceChecker, \
    PairUniverse, CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue
from agent.generators.operator_param_combine.combination_result_generator.generator import GeneratorOptions
from agent.generators.operator_param_combine.combination_result_generator.model.generator_config import GeneratorConfig
from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import ParameterModel
from agent.generators.operator_param_combine.combination_result_generator.constraint.compiler import ConstraintCompiler
from agent.generators.operator_param_combine.combination_result_generator.constraint.evaluator import ConstraintEvaluator

_PARAMETER_ATTRIBUTE_KEYS = frozenset({
    "dtype", "range_value", "is_present", "length",
    "dimension", "shape_property", "format",
})


def load_config(json_path: str) -> GeneratorConfig:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    parameters = {}
    for param_name, attrs in data["parameters"].items():
        kwargs = {"name": param_name}
        for key, values in attrs.items():
            if key in _PARAMETER_ATTRIBUTE_KEYS:
                kwargs[key] = tuple(values)
        parameters[param_name] = ParameterModel(**kwargs)

    constraints = tuple(data.get("constraints", []))
    return GeneratorConfig(parameters=parameters, constraints=constraints)


def load_options(data: dict) -> GeneratorOptions:
    opts = data.get("options", {})
    return GeneratorOptions(
        strength=opts.get("strength", 2),
        target_coverage=opts.get("target_coverage", 1.0),
        max_iterations=opts.get("max_iterations", 10000),
        random_seed=opts.get("random_seed", None),
    )


def build_constraint(constraints: tuple[str, ...]) -> ConstraintProtocol | None:
    if not constraints:
        return None

    compiler = ConstraintCompiler()
    evaluator = ConstraintEvaluator()
    compiled = [compiler.compile(expr) for expr in constraints]

    class CombinedConstraint:
        def __init__(self):
            self.compiled = compiled
        def evaluate(self, context) -> bool:
            for constraint_compile in compiled:
                try:
                    if not evaluator.evaluate(constraint_compile.tree, context):
                        return False
                except Exception as e:
                    continue
            return True

    return CombinedConstraint()


def build_universe_and_tracker(
        config: GeneratorConfig,
        constraint: ConstraintProtocol | None,
):
    factor_values: list[FactorValue] = []
    for param in config.parameters.values():
        for attr_name, domain in param.attributes().items():
            if domain:
                for val in domain:
                    factor_values.append(
                        FactorValue(Factor(param.name, attr_name), val)
                    )

    builder = PairBuilder()
    pairs: list[Pair] = builder.build(factor_values)

    if constraint is not None and hasattr(constraint, "compiled"):
        checker = PairExistenceChecker(
            constraint,
            compiled_list=constraint.compiled,
            config=config,
        )
        pairs = checker.filter_pairs(pairs)

    universe = PairUniverse(pairs)
    tracker = CoverageTracker(universe)
    return universe, tracker, builder
