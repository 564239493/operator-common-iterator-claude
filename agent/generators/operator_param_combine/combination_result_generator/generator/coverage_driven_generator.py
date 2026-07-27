from __future__ import annotations

import time
from typing import Optional
from typing import Tuple

from agent.generators.common_utils.timing import track
from agent.generators.operator_param_combine.combination_result_generator.constraint.interfaces import \
    ConstraintProtocol
from agent.generators.operator_param_combine.combination_result_generator.coverage import PairUniverse, CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue
from agent.generators.operator_param_combine.combination_result_generator.generator.base import BaseGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.generator_options import \
    GeneratorOptions
from agent.generators.operator_param_combine.combination_result_generator.generator.candidate_generator import \
    CandidateGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.coverage_selector import \
    CoverageSelector, RandomUncoveredPairSelector
from agent.generators.operator_param_combine.combination_result_generator.generator.scoring import ScoringStrategy, \
    UncoveredPairScoring
from agent.generators.operator_param_combine.combination_result_generator.generator.cache import TestCaseCache
from agent.generators.operator_param_combine.combination_result_generator.generator.pair_seed_generator import \
    PairSeedGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.model import TestSuite, \
    GenerationResult, TestCase


class CoverageDrivenGenerator(BaseGenerator):

    def __init__(
            self,
            universe: PairUniverse,
            coverage_tracker: CoverageTracker,
            constraint: Optional[ConstraintProtocol],
            config: GeneratorOptions,
            candidate_generator: CandidateGenerator,
            pair_builder: PairBuilder,
            *,
            selector: Optional[CoverageSelector] = None,
            scoring: Optional[ScoringStrategy] = None,
            cache: Optional[TestCaseCache] = None,
            candidate_pool_size: int = 50,
    ) -> None:
        super().__init__(universe, coverage_tracker, constraint, config)

        self._candidate_generator = candidate_generator
        self._pair_builder = pair_builder
        self._pair_seed_generator = PairSeedGenerator(universe, coverage_tracker)
        self._selector = selector or RandomUncoveredPairSelector(seed=config.random_seed)
        self._scoring = scoring or UncoveredPairScoring(pair_builder)
        self._cache = cache or TestCaseCache()
        self._candidate_pool_size = candidate_pool_size
        self._suite: Optional[TestSuite] = None
        self._universe = universe

    def initialize(self) -> None:
        self._suite = TestSuite()
        self._cache.clear()

    @track("CoverageDrivenGenerator.build")
    def build(self) -> GenerationResult:
        start_time = time.time()
        iterations = 0
        # 记录连续未产生实际收益的迭代次数
        no_improvement_streak = 0
        # 未产生实际收益的迭代次数上限，即若连续max_no_improvement未产生实际的收益，就停止继续尝试，结束迭代，不产生新的用例
        max_no_improvement = max(300, self._candidate_pool_size * 4)

        while (self._coverage_tracker.coverage_rate() < self._config.target_coverage
               and iterations < self._config.max_iterations):
            iterations += 1

            result = self._select_best_candidate()
            if result is None:
                no_improvement_streak += 1
                if no_improvement_streak >= max_no_improvement:
                    break
                continue

            best_candidate, gain = result

            if not self._cache.add(best_candidate):
                no_improvement_streak += 1
                if no_improvement_streak >= max_no_improvement:
                    break
                continue

            if gain == 0:
                no_improvement_streak += 1
                if no_improvement_streak >= max_no_improvement:
                    break
                continue

            no_improvement_streak = 0

            factor_values = self._extract_factor_values(best_candidate)
            pairs = self._pair_builder.build(factor_values)
            self._coverage_tracker.update(pairs)
            self._suite.add(best_candidate)

        return GenerationResult(
            suite=self._suite,
            coverage_rate=self._coverage_tracker.coverage_rate(),
            iterations=iterations,
            elapsed_time=time.time() - start_time,
        )

    def finalize(self) -> None:
        pass

    @track("CoverageDrivenGenerator._select_best_candidate")
    def _select_best_candidate(self) -> Optional[Tuple[TestCase, int]]:
        seed = self._pair_seed_generator.next_seed()
        is_greedy = self._candidate_generator.is_greedy

        if is_greedy:
            try:
                candidate, gain = self._candidate_generator.generate_candidate(fixed_values=seed)
            except Exception as e:
                return None
            if self._cache.contains(candidate):
                return None
            return candidate, gain

        best_candidate = None
        best_gain = -1

        uncovered_mask = self._coverage_tracker.uncovered_pairs_mask()

        for _ in range(self._candidate_pool_size):
            try:
                candidate, _ = self._candidate_generator.generate_candidate(
                    fixed_values=seed
                )
            except Exception:
                continue

            if self._cache.contains(candidate):
                continue

            gain = self._compute_gain_fast(candidate, uncovered_mask)
            if gain > best_gain:
                best_gain = gain
                best_candidate = candidate

        if best_candidate is None:
            return None
        return best_candidate, best_gain

    @track("CoverageDrivenGenerator._compute_gain_fast")
    def _compute_gain_fast(self, testcase: TestCase, uncovered_mask: int) -> int:
        factor_values = self._extract_factor_values(testcase)
        pairs = self._pair_builder.build(factor_values)

        testcase_mask = 0
        for p in pairs:
            idx = self._universe.get_index(p)
            if idx is not None:
                testcase_mask |= (1 << idx)

        return (testcase_mask & uncovered_mask).bit_count()

    def _compute_gain(self, testcase: TestCase) -> int:
        uncovered_mask = self._coverage_tracker.uncovered_pairs_mask()
        return self._compute_gain_fast(testcase, uncovered_mask)

    @staticmethod
    def _extract_factor_values(testcase: TestCase):
        values = []
        for param_name, attrs in testcase.values.items():
            for attr_name, val in attrs.items():
                values.append(
                    FactorValue(
                        factor=Factor(parameter=param_name, attribute=attr_name),
                        value=val,
                    )
                )
        return values
