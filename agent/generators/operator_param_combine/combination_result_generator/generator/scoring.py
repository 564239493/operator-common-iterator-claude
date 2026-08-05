from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from agent.generators.operator_param_combine.combination_result_generator.coverage import PairUniverse, CoverageTracker, Pair
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue
from agent.generators.operator_param_combine.combination_result_generator.generator.model import TestCase


class ScoringStrategy(ABC):

    @abstractmethod
    def score(
        self,
        testcase: TestCase,
        universe: PairUniverse,
        coverage_tracker: CoverageTracker,
    ) -> int:
        ...


class UncoveredPairScoring(ScoringStrategy):

    def __init__(self, pair_builder: PairBuilder) -> None:
        self._pair_builder = pair_builder

    def score(
        self,
        testcase: TestCase,
        universe: PairUniverse,
        coverage_tracker: CoverageTracker,
    ) -> int:
        factor_values = self._extract_factor_values(testcase)
        pairs = self._pair_builder.build(factor_values)

        testcase_mask = 0
        for p in pairs:
            idx = universe.get_index(p)
            if idx is not None:
                testcase_mask |= (1 << idx)

        uncovered = coverage_tracker.uncovered_pairs_mask()
        return (testcase_mask & uncovered).bit_count()

    @staticmethod
    def _extract_factor_values(testcase: TestCase) -> List[FactorValue]:

        values: List[FactorValue] = []
        for param_name, attrs in testcase.values.items():
            for attr_name, val in attrs.items():
                values.append(
                    FactorValue(
                        factor=Factor(parameter=param_name, attribute=attr_name),
                        value=val,
                    )
                )
        return values


class WeightedUncoveredPairScoring(ScoringStrategy):

    def __init__(self, pair_builder: PairBuilder) -> None:
        self._pair_builder = pair_builder

    def score(
        self,
        testcase: TestCase,
        universe: PairUniverse,
        coverage_tracker: CoverageTracker,
    ) -> int:
        factor_values = self._extract_factor_values(testcase)
        pairs = self._pair_builder.build(factor_values)
        uncovered_mask = coverage_tracker.uncovered_pairs_mask()

        total_weight = 0
        for pair in pairs:
            idx = universe.get_index(pair)
            if idx is not None and (uncovered_mask & (1 << idx)):
                weight = 1 + self._rarity_weight(pair, universe)
                total_weight += weight
        return total_weight

    @staticmethod
    def _rarity_weight(pair: Pair, universe: PairUniverse) -> int:
        left_count = 0
        right_count = 0
        for p in universe.get_pairs():
            if p.left == pair.left or p.right == pair.left:
                left_count += 1
            if p.left == pair.right or p.right == pair.right:
                right_count += 1
        return max(0, 10 - left_count) + max(0, 10 - right_count)

    @staticmethod
    def _extract_factor_values(testcase: TestCase) -> List[FactorValue]:
        values: List[FactorValue] = []
        for param_name, attrs in testcase.values.items():
            for attr_name, val in attrs.items():
                values.append(
                    FactorValue(
                        factor=Factor(parameter=param_name, attribute=attr_name),
                        value=val,
                    )
                )
        return values
