from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import Optional

from agent.generators.operator_param_combine.combination_result_generator.coverage import PairUniverse, CoverageTracker, Pair


class CoverageSelector(ABC):

    @abstractmethod
    def select_next(
        self,
        universe: PairUniverse,
        coverage_tracker: CoverageTracker,
    ) -> Optional[Pair]:
        ...


class FirstUncoveredPairSelector(CoverageSelector):

    def select_next(
        self,
        universe: PairUniverse,
        coverage_tracker: CoverageTracker,
    ) -> Optional[Pair]:
        indices = coverage_tracker.uncovered_indices()
        for idx in indices:
            pair = universe.get_by_index(idx)
            if pair is not None:
                return pair
        return None


class RandomUncoveredPairSelector(CoverageSelector):

    def __init__(self, seed: Optional[int] = None) -> None:
        self._random = random.Random(seed)

    def select_next(
        self,
        universe: PairUniverse,
        coverage_tracker: CoverageTracker,
    ) -> Optional[Pair]:
        indices = coverage_tracker.uncovered_indices()
        if not indices:
            return None
        idx = self._random.choice(indices)
        return universe.get_by_index(idx)


class MostConstrainedSelector(CoverageSelector):

    def select_next(
        self,
        universe: PairUniverse,
        coverage_tracker: CoverageTracker,
    ) -> Optional[Pair]:
        indices = coverage_tracker.uncovered_indices()
        if not indices:
            return None

        all_pairs = universe.get_pairs()

        factor_frequency: dict[str, int] = {}
        for idx in indices:
            pair = universe.get_by_index(idx)
            if pair is None:
                continue
            for fv in (pair.left, pair.right):
                key = f"{fv.factor.name}={fv.value}"
                factor_frequency[key] = factor_frequency.get(key, 0) + 1

        best_pair = None
        best_score = -1

        for idx in indices:
            pair = universe.get_by_index(idx)
            if pair is None:
                continue
            left_key = f"{pair.left.factor.name}={pair.left.value}"
            right_key = f"{pair.right.factor.name}={pair.right.value}"
            score = factor_frequency.get(left_key, 0) + factor_frequency.get(right_key, 0)
            if score > best_score:
                best_score = score
                best_pair = pair

        return best_pair
