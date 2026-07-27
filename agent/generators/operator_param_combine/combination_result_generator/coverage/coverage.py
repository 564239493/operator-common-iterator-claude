"""
Coverage Tracker.

M3-Step4

Responsibility:

    Maintain coverage state using bit vector for O(1) operations.

    PairUniverse:
        What should be covered

    CoverageTracker:
        What has been covered
"""

from typing import Iterable, List, Set

from agent.generators.common_utils.timing import track
from agent.generators.operator_param_combine.combination_result_generator.coverage import PairUniverse, Pair
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_id import PairId


class CoverageTracker:
    """
    Maintain pair coverage status using integer bit vector.
    """

    def __init__(self, universe: PairUniverse):
        if universe is None:
            raise ValueError("universe cannot be None")
        self._universe = universe
        self._total = universe.size()
        self._covered_mask: int = 0
        self._covered_count: int = 0

    def mark_covered(self, pair: Pair):
        idx = self._universe.get_index(pair)
        if idx is None:
            return
        bit = 1 << idx
        if not (self._covered_mask & bit):
            self._covered_mask |= bit
            self._covered_count += 1

    @track("CoverageTracker.update")
    def update(self, pairs: Iterable[Pair]):
        for pair in pairs:
            self.mark_covered(pair)

    def covered_pairs(self) -> Set[PairId]:
        result: Set[PairId] = set()
        mask = self._covered_mask
        i = 0
        while mask:
            if mask & 1:
                pair = self._universe.get_by_index(i)
                if pair is not None:
                    result.add(pair.pair_id)
            mask >>= 1
            i += 1
        return result

    def uncovered_pairs_mask(self) -> int:
        all_bits = (1 << self._total) - 1 if self._total > 0 else 0
        return all_bits & ~self._covered_mask

    def uncovered_pairs(self) -> Set[PairId]:
        result: Set[PairId] = set()
        uncovered = self.uncovered_pairs_mask()
        i = 0
        while uncovered:
            if uncovered & 1:
                pair = self._universe.get_by_index(i)
                if pair is not None:
                    result.add(pair.pair_id)
            uncovered >>= 1
            i += 1
        return result

    def uncovered_indices(self) -> List[int]:
        indices: List[int] = []
        uncovered = self.uncovered_pairs_mask()
        i = 0
        while uncovered:
            if uncovered & 1:
                indices.append(i)
            uncovered >>= 1
            i += 1
        return indices

    def total_pairs(self) -> int:
        return self._total

    def covered_count(self) -> int:
        return self._covered_count

    def uncovered_count(self) -> int:
        return self._total - self._covered_count

    def coverage_rate(self) -> float:
        if self._total == 0:
            return 1.0
        return self._covered_count / self._total

    def is_complete(self) -> bool:
        return self._covered_count == self._total

    def is_uncovered(self, pair: Pair) -> bool:
        idx = self._universe.get_index(pair)
        if idx is None:
            return False
        return not (self._covered_mask & (1 << idx))

    def is_covered(self, pair: Pair) -> bool:
        idx = self._universe.get_index(pair)
        if idx is None:
            return False
        return bool(self._covered_mask & (1 << idx))
