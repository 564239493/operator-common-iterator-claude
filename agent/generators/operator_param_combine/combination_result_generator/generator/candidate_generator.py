from __future__ import annotations

import random

from typing import Any
from typing import List
from typing import Optional
from typing import Tuple

from agent.generators.common_utils.timing import track
from agent.generators.operator_param_combine.combination_result_generator.constraint.interfaces import ConstraintProtocol
from agent.generators.operator_param_combine.combination_result_generator.coverage import PairUniverse, CoverageTracker, Pair
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue
from agent.generators.operator_param_combine.combination_result_generator.generator.model import TestCase, TestValue
from agent.generators.operator_param_combine.combination_result_generator.generator.exceptions import CandidateGenerationError
from agent.generators.operator_param_combine.combination_result_generator.model.generator_config import GeneratorConfig


class CandidateGenerator:

    def __init__(
            self,
            config: GeneratorConfig,
            constraint: Optional[ConstraintProtocol] = None,
            *,
            random_seed: Optional[int] = None,
            max_iterations: int = 10000,
            universe: Optional[PairUniverse] = None,
            coverage_tracker: Optional[CoverageTracker] = None,
    ) -> None:
        self._config = config
        self._constraint = constraint
        self._max_iterations = max_iterations
        self._random = random.Random(random_seed)
        self._universe = universe
        self._coverage_tracker = coverage_tracker
        self._use_greedy = universe is not None and coverage_tracker is not None

    @property
    def is_greedy(self) -> bool:
        return self._use_greedy

    @track("CandidateGenerator.generate_candidate")
    def generate_candidate(self, fixed_values: dict[str, dict[str, Any]] | None = None) -> Tuple[TestCase, int]:
        fixed_values = fixed_values or {}
        greedy_max_retries = 1
        random_max_retries = 3

        uncovered_mask = (self._coverage_tracker.uncovered_pairs_mask() if self._use_greedy else 0)

        # 基于seed与构建用例，只包含seed中的属性
        seed_testcase = TestCase()
        for param_name, param_fixed in fixed_values.items():
            parameter = self._config.parameters.get(param_name)
            if parameter is None:
                raise CandidateGenerationError(f"unknown parameter '{param_name}'")
            for attr_name, value in param_fixed.items():
                domain = parameter.attributes().get(attr_name)
                if not domain:
                    raise CandidateGenerationError(f"{param_name}.{attr_name} has empty domain")
                if value not in domain:
                    raise CandidateGenerationError(f"{param_name}.{attr_name}={value} not in domain")
                seed_testcase.add_value(TestValue(param_name, attr_name, value))

        # 获取待填充的属性，除seed中的属性，即需要填充的
        attrs_to_fill: list[tuple[str, str, tuple[Any, ...]]] = []
        for parameter in self._config.parameters.values():
            param_fixed = fixed_values.get(parameter.name, {})
            for attr_name, domain in parameter.attributes().items():
                if domain and attr_name not in param_fixed:
                    attrs_to_fill.append((parameter.name, attr_name, domain))

        seed_params = set(fixed_values.keys())
        attrs_to_fill.sort(key=lambda item: (
            0 if item[0] in seed_params else 1,
            len(item[2]),
        ))

        for attempt in range(self._max_iterations):
            use_greedy = self._use_greedy and attempt < greedy_max_retries
            if attempt >= greedy_max_retries + random_max_retries:
                break

            testcase = TestCase()
            testcase.values = {
                p: dict(attrs) for p, attrs in seed_testcase.values.items()
            }

            nodes = [200]
            success = self._backtrack_fill(
                testcase, attrs_to_fill, 0,
                uncovered_mask, sort_by_gain=use_greedy, nodes_remaining=nodes,
            )
            if not success:
                continue

            total_gain = (
                self._compute_total_gain(testcase, uncovered_mask)
                if self._use_greedy else 0
            )
            if self._is_valid(testcase):
                return testcase, total_gain

        raise CandidateGenerationError("failed to generate legal testcase")

    @track("CandidateGenerator._get_candidates_ranked")
    def _get_candidates_ranked(
            self, *, testcase, param_name, attr_name, domain, uncovered_mask, sort_by_gain: bool,
    ) -> list[tuple[Any, int]]:
        existing_factors = self._extract_existing_factors(testcase)
        candidates = []
        for value in domain:
            if not self._check_value_partial(testcase, param_name, attr_name, value):
                continue
            gain = 0
            if sort_by_gain:
                gain = self._compute_incremental_gain(
                    new_value=FactorValue(Factor(param_name, attr_name), value),
                    existing_factors=existing_factors, uncovered_mask=uncovered_mask,
                )
            candidates.append((value, gain))
        if sort_by_gain:
            candidates.sort(key=lambda x: x[1], reverse=True)
        else:
            self._random.shuffle(candidates)
        return candidates

    @track("CandidateGenerator._backtrack_fill")
    def _backtrack_fill(
            self, testcase, attributes, index, uncovered_mask, nodes_remaining, sort_by_gain: bool) -> bool:

        if index == len(attributes):
            return True  # 全部属性赋值完成
        if nodes_remaining[0] <= 0:
            return False  # 搜索预算耗尽

        param_name, attr_name, domain = attributes[index]
        candidates = self._get_candidates_ranked(
            testcase=testcase,
            param_name=param_name, attr_name=attr_name,
            domain=domain, uncovered_mask=uncovered_mask, sort_by_gain=sort_by_gain)

        for value, _ in candidates:
            nodes_remaining[0] -= 1
            testcase.add_value(TestValue(param_name, attr_name, value))
            if self._backtrack_fill(testcase, attributes, index + 1, uncovered_mask, nodes_remaining, sort_by_gain):
                return True
            # 回退：移除当前值
            del testcase.values[param_name][attr_name]
            if not testcase.values[param_name]:
                del testcase.values[param_name]

        return False  # 所有候选值均失败

    @track("CandidateGenerator._compute_total_gain")
    def _compute_total_gain(
            self, testcase: TestCase, uncovered_mask: int) -> int:
        existing = self._extract_existing_factors(testcase)
        mask = 0
        exist_num = len(existing)
        for i in range(exist_num):
            for j in range(i + 1, exist_num):
                factor_value1, factor_value2 = existing[i], existing[j]
                if factor_value1.factor == factor_value2.factor:
                    continue
                pair = Pair(factor_value1, factor_value2)
                idx = self._universe.get_index(pair)
                if idx is not None:
                    mask |= (1 << idx)
        return (mask & uncovered_mask).bit_count()

    @track("CandidateGenerator._compute_incremental_gain")
    def _compute_incremental_gain(self, *, new_value: FactorValue, existing_factors: List[FactorValue],
                                  uncovered_mask: int) -> int:
        mask = 0
        for existing in existing_factors:
            if new_value.factor == existing.factor:
                continue
            pair = Pair(existing, new_value)
            index = self._universe.get_index(pair)
            if index is not None:
                mask |= (1 << index)
        return (mask & uncovered_mask).bit_count()

    @staticmethod
    def _extract_existing_factors(testcase: TestCase) -> List[FactorValue]:
        factors = []
        for param_name, attrs in testcase.values.items():
            for attr_name, val in attrs.items():
                factors.append(FactorValue(factor=Factor(parameter=param_name, attribute=attr_name), value=val))
        return factors

    @track("CandidateGenerator._is_valid")
    def _is_valid(self, testcase: TestCase) -> bool:
        if self._constraint is None:
            return True
        return self._constraint.evaluate(testcase.values)

    @track("CandidateGenerator._check_value_partial")
    def _check_value_partial(self, testcase, param_name, attr_name, value):
        if self._constraint is None:
            return True
        context = {p: dict(attrs) for p, attrs in testcase.values.items()}
        if param_name not in context:
            context[param_name] = {}
        context[param_name][attr_name] = value
        return self._constraint.evaluate(context)
