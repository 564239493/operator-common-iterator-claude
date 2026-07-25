"""
Pair Universe.

M3-Step3

Responsibility:

    Maintain complete Pair coverage space.
    Assign sequential pair indices for efficient bit-vector coverage tracking.

Important:

    PairUniverse DOES NOT maintain coverage state.
    Covered / uncovered state belongs to CoverageTracker.

"""

from typing import Dict, Iterable, List

from agent.generators.operator_param_combine.combination_result_generator.coverage import Pair

from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_id import PairId


class PairUniverse:
    """
    Pair coverage universe.

    Stores all possible pairs with sequential indices.
    """

    def __init__(self, pairs: Iterable[Pair] = None):

        self._pairs: Dict[PairId, Pair] = {}
        self._indices: Dict[PairId, int] = {}
        self._pairs_by_index: Dict[int, Pair] = {}
        self._next_index: int = 0

        if pairs:
            for pair in pairs:
                self.add(pair)

    def add(self, pair: Pair):
        pair_id = pair.pair_id

        if pair_id not in self._pairs:
            self._pairs[pair_id] = pair
            idx = self._next_index
            self._indices[pair_id] = idx
            self._pairs_by_index[idx] = pair
            self._next_index += 1

    def contains(self, pair: Pair) -> bool:
        return pair.pair_id in self._pairs

    def get(self, pair_id: PairId) -> Pair:
        return self._pairs.get(pair_id)

    def get_by_index(self, idx: int) -> Pair:
        return self._pairs_by_index.get(idx)

    def get_index(self, pair: Pair) -> int | None:
        return self._indices.get(pair.pair_id)

    def all_pairs(self) -> Dict[PairId, Pair]:
        return dict(self._pairs)

    def pair_ids(self) -> set[PairId]:
        return set(self._pairs.keys())

    def pair_indices(self) -> List[int]:
        return list(self._pairs_by_index.keys())

    def size(self) -> int:
        return len(self._pairs)

    def get_pairs(self) -> List[Pair]:
        return list(self._pairs.values())
