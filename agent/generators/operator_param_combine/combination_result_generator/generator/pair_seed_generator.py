from __future__ import annotations

from typing import Any

from agent.generators.operator_param_combine.combination_result_generator.coverage import PairUniverse, CoverageTracker, Pair


class PairSeedGenerator:
    """
    Convert uncovered pairs into
    seed assignments.
    Example:
        Pair
            x.dtype=fp16
            weight.dtype=int8
        ->
        {
            "x": {
                "dtype": "fp16"
            },
            "weight": {
                "dtype": "int8"
            }
        }
    """

    def __init__(self, universe: PairUniverse, coverage_tracker: CoverageTracker) -> None:

        if universe is None:
            raise ValueError("universe cannot be None")
        if coverage_tracker is None:
            raise ValueError("coverage_tracker cannot be None")
        self._universe = universe
        self._coverage_tracker = coverage_tracker

    def next_pair(self) -> Pair | None:
        indices = self._coverage_tracker.uncovered_indices()
        for idx in indices:
            pair = self._universe.get_by_index(idx)
            if pair is not None:
                return pair
        return None

    def pair_to_seed(self, pair: Pair) -> dict[str, dict[str, Any]]:
        seed: dict[str, dict[str, Any]] = {}
        self._add_factor_value(seed, pair.left)

        self._add_factor_value(seed, pair.right)
        return seed

    def next_seed(self) -> dict[str, dict[str, Any]] | None:
        pair = self.next_pair()
        if pair is None:
            return None

        return self.pair_to_seed(pair)

    @staticmethod
    def _add_factor_value(seed: dict[str, dict[str, Any]], factor_value) -> None:

        parameter = factor_value.factor.parameter

        attribute = factor_value.factor.attribute

        value = factor_value.value

        if parameter not in seed:
            seed[parameter] = {}

        seed[parameter][attribute] = value
