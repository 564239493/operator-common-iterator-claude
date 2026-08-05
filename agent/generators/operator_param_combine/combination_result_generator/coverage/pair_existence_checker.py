"""
Pair Existence Checker.

M3-Step5.

Responsibility:

    Validate whether a Pair satisfies
    constraint rules.

Architecture:

    M3 Coverage

          |
          v

    ConstraintEvaluatorProtocol

          |
          v

    M2 Constraint Engine


Important:

    This module MUST NOT depend on
    M2 concrete implementation.

"""

import itertools
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
)

from agent.generators.operator_param_combine.combination_result_generator.constraint.interfaces import \
    ConstraintProtocol
from agent.generators.common_utils.timing import track

from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue

from agent.generators.operator_param_combine.combination_result_generator.coverage import Pair

from agent.generators.operator_param_combine.combination_result_generator.constraint import ConstraintEvaluator


class PairExistenceChecker:
    """
    Check whether Pair exists
    in valid coverage space.


    A Pair is valid when:

        constraint.evaluate(context)
        == True

    """

    def __init__(
            self,
            constraint: ConstraintProtocol,
            compiled_list: Optional[list] = None,
            config: Optional[Any] = None,
    ) -> None:
        """
        Initialize PairExistenceChecker.
        Args:
            evaluator:
                Constraint evaluator implementation.
            constraint:
                Executable constraint object.
        """
        self._constraint: ConstraintProtocol = constraint
        self._compiled = compiled_list or []
        self._config = config

    @track("PairExistenceChecker.is_valid_combination")
    def is_valid_combination(
            self, left: FactorValue, right: FactorValue
    ) -> bool:
        if self._constraint is None:
            return True
        context: Dict[str, Any] = {}
        for fv in (left, right):
            param = fv.factor.parameter
            attr = fv.factor.attribute
            if param not in context:
                context[param] = {}
            context[param][attr] = fv.value
        try:
            return self._constraint.evaluate(context)
        except Exception:
            return True

    @track("PairExistenceChecker.filter_pairs")
    def filter_pairs(self, pairs: Iterable[Pair]) -> List[Pair]:
        valid: List[Pair] = []
        for pair in pairs:
            if self._is_pair_deep_valid(pair):
                valid.append(pair)
        return valid

    @track("PairExistenceChecker._is_pair_deep_valid")
    def _is_pair_deep_valid(self, pair: Pair) -> bool:
        base_context: Dict[str, Any] = {}
        for fv in (pair.left, pair.right):
            p = fv.factor.parameter
            a = fv.factor.attribute
            base_context.setdefault(p, {})[a] = fv.value

        # fallback: 没有 compiled 信息时走简单 evaluate
        if not self._compiled:
            try:
                return self._constraint.evaluate(base_context)
            except Exception:
                return True

        evaluator = ConstraintEvaluator()
        for cc in self._compiled:
            deps = cc.dependencies

            # partition: 已有 vs 缺失的依赖属性
            missing_by_param: Dict[str, list] = {}
            for dep in deps:
                parts = dep.split(".")
                if len(parts) < 2:
                    continue  # 函数名、独立变量等
                p, a = parts[0], parts[1]  # 只取前两层，忽略深层（如 shape.rank）
                if p not in base_context or a not in base_context[p]:
                    missing_by_param.setdefault(p, []).append(a)

            # Case 1: 所有依赖均已就位 → 直接求值
            if not missing_by_param:
                try:
                    if not evaluator.evaluate(cc.tree, base_context):
                        return False
                except Exception:
                    continue
                continue

            # Case 2: 缺失 ≥2 个不同参数 → 无法证明，放过
            if len(missing_by_param) >= 2:
                continue

            # Case 3: 缺失恰好 1 个参数的部分属性 → 穷举域
            param, attrs = next(iter(missing_by_param.items()))
            param_model = self._config.parameters.get(param) if self._config else None
            if param_model is None:
                continue

            domains = []
            skip = False
            for attr in attrs:
                domain = param_model.attributes().get(attr)
                if not domain:
                    skip = True
                    break
                domains.append(list(domain))
            if skip:
                continue

            # 穷举组合上限 100，避免爆炸
            total = 1
            for d in domains:
                total *= len(d)
            if total > 100:
                continue

            ok = False
            for combo in itertools.product(*domains):
                ctx = {p: dict(a) for p, a in base_context.items()}
                ctx.setdefault(param, {}).update(dict(zip(attrs, combo)))
                try:
                    if evaluator.evaluate(cc.tree, ctx):
                        ok = True
                        break
                except Exception:
                    continue

            if not ok:
                return False

        return True
