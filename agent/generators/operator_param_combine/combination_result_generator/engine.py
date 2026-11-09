"""
Feature Combination V2.0 — Shared Pipeline Engine

Provides reusable functions for config loading, constraint compilation,
and pair universe construction. Used by both the generation entry point
and coverage evaluation tool.
"""

import json

from agent.generators.common_utils.logger_util import LazyLogger
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
from agent.generators.operator_param_combine.combination_result_generator.constraint.evaluator import \
    ConstraintEvaluator
from agent.generators.operator_param_combine.combination_result_generator.constraint.exceptions import \
    describe_binding_cause

_PARAMETER_ATTRIBUTE_KEYS = frozenset({
    "dtype", "range_value", "is_present", "length",
    "dimension", "shape_property", "format",
})

logger = LazyLogger()


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
    compiled = []
    for expr in constraints:
        try:
            cc = compiler.compile(expr)
            compiled.append(cc)
        except Exception as e:
            logger.error(f"Failed to compile constraint, expr : '{expr}', error : {e}")

    class CombinedConstraint:
        """多约束合取求值。

        语义边界（两种情形后果相反，日志必须能区分，否则极易误导定位）：
          - 不满足：evaluate 返回 False → 该行被拒绝，记入 failed_exprs；
          - 求值异常：如引用了缺席参数的属性 → 该约束本次未生效，
            行被放行，记入 skipped_exprs。
        异常分支保留“宽松放行”的历史行为（与 Z3 侧空泛满足的意图一致），
        但必须留下可定位痕迹：只打 debug 时日志里只剩“通过”，会被误读成
        “约束已校验且满足”，实际是这条约束从头到尾没起作用。
        """

        def __init__(self):
            self.compiled = compiled
            self.failed_exprs = set()
            # {expression: {"reason": str, "count": int, "code": str,
            #               "param": str | None, "attr": str | None, "cause": str}}
            self.skipped_exprs: dict[str, dict] = {}

        def evaluate(self, context) -> bool:
            for constraint_compile in compiled:
                try:
                    if not evaluator.evaluate(constraint_compile.tree, context):
                        self.failed_exprs.add(constraint_compile.expression)
                        return False
                except Exception as e:
                    self._record_skip(constraint_compile.expression, e)
            return True

        def _record_skip(self, expression: str, error: Exception) -> None:
            """记录一次“求值异常导致该约束本次未生效”。

            同一表达式只打印一次 WARNING，后续命中仅累计次数，避免单批上千行时刷屏；
            计数可据 skipped_exprs 判断哪条约束实际从未参与筛选。
            除完整消息外还留存 code/param/attr/cause，供上层按“哪个参数的哪个属性”
            分组汇总，避免调用方再从消息文本里正则提取。

            :param expression: 约束原始表达式，作为统计键
            :param error: 求值抛出的异常，其消息即日志中的根因
            """
            record = self.skipped_exprs.get(expression)
            if record is not None:
                # 同一表达式在同一批次会命中成百上千次，只累计不再刷屏
                record["count"] += 1
                return
            info = {
                "reason": f"[{type(error).__name__}] {error}",
                "count": 1,
                "code": getattr(error, "error_code", "") or type(error).__name__,
                "param": getattr(error, "param", None),
                "attr": getattr(error, "attr", None),
                "cause": getattr(error, "cause", "") or "",
            }
            self.skipped_exprs[expression] = info
            logger.warning(
                f"Constraint SKIPPED (row KEPT, this constraint took no effect): "
                f"[{info['code']}] "
                f"{describe_binding_cause(info['param'], info['attr'], info['cause'])} "
                f"| expr: {expression} | later hits are counted, not logged"
            )

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
