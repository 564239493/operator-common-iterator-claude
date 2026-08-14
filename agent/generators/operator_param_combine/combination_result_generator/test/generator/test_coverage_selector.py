import pytest

from agent.generators.operator_param_combine.combination_result_generator.coverage.coverage import CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.universe import PairUniverse
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair import Pair

from agent.generators.operator_param_combine.combination_result_generator.generator.coverage_selector import (
    CoverageSelector,
    FirstUncoveredPairSelector,
    RandomUncoveredPairSelector,
    MostConstrainedSelector,
)


def make_universe_and_tracker(num_pairs: int = 5):
    factor_values = []
    for i in range(num_pairs + 1):
        factor_values.append(
            FactorValue(Factor(f"p{i}", "dtype"), f"v{i % 3}")
        )
        factor_values.append(
            FactorValue(Factor(f"p{i}", "dimension"), i)
        )

    from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
    builder = PairBuilder()
    pairs = builder.build(factor_values)
    universe = PairUniverse(pairs)
    tracker = CoverageTracker(universe)
    return universe, tracker, pairs


class TestFirstUncoveredPairSelector:

    def test_init(self):
        selector = FirstUncoveredPairSelector()
        assert selector is not None

    def test_select_next_returns_pair(self):
        universe, tracker, all_pairs = make_universe_and_tracker(3)
        selector = FirstUncoveredPairSelector()

        pair = selector.select_next(universe, tracker)
        assert pair is not None
        assert isinstance(pair, Pair)

    def test_select_next_returns_none_when_all_covered(self):
        universe, tracker, all_pairs = make_universe_and_tracker(3)
        selector = FirstUncoveredPairSelector()

        for p in all_pairs:
            tracker.mark_covered(p)

        pair = selector.select_next(universe, tracker)
        assert pair is None

    def test_select_next_empty_universe(self):
        universe = PairUniverse()
        tracker = CoverageTracker(universe)
        selector = FirstUncoveredPairSelector()

        pair = selector.select_next(universe, tracker)
        assert pair is None

    def test_select_next_after_partial_coverage(self):
        universe, tracker, all_pairs = make_universe_and_tracker(3)

        tracker.mark_covered(all_pairs[0])

        selector = FirstUncoveredPairSelector()
        pair = selector.select_next(universe, tracker)
        assert pair is not None
        assert pair.pair_id not in tracker.covered_pairs()


class TestRandomUncoveredPairSelector:

    def test_init(self):
        selector = RandomUncoveredPairSelector()
        assert selector is not None

    def test_init_with_seed(self):
        selector = RandomUncoveredPairSelector(seed=42)
        assert selector is not None

    def test_select_next_returns_pair(self):
        universe, tracker, all_pairs = make_universe_and_tracker(3)
        selector = RandomUncoveredPairSelector(seed=42)

        pair = selector.select_next(universe, tracker)
        assert pair is not None

    def test_select_next_returns_none_when_all_covered(self):
        universe, tracker, all_pairs = make_universe_and_tracker(3)
        selector = RandomUncoveredPairSelector()

        for p in all_pairs:
            tracker.mark_covered(p)

        pair = selector.select_next(universe, tracker)
        assert pair is None

    def test_select_next_empty_universe(self):
        universe = PairUniverse()
        tracker = CoverageTracker(universe)
        selector = RandomUncoveredPairSelector()

        pair = selector.select_next(universe, tracker)
        assert pair is None

    def test_seed_determinism(self):
        universe, tracker, all_pairs = make_universe_and_tracker(5)

        s1 = RandomUncoveredPairSelector(seed=42)
        s2 = RandomUncoveredPairSelector(seed=42)

        p1 = s1.select_next(universe, tracker)
        p2 = s2.select_next(universe, tracker)
        assert p1.pair_id == p2.pair_id


class TestMostConstrainedSelector:

    def test_init(self):
        selector = MostConstrainedSelector()
        assert selector is not None

    def test_select_next_returns_pair(self):
        universe, tracker, all_pairs = make_universe_and_tracker(3)
        selector = MostConstrainedSelector()

        pair = selector.select_next(universe, tracker)
        assert pair is not None

    def test_select_next_returns_none_when_all_covered(self):
        universe, tracker, all_pairs = make_universe_and_tracker(3)
        selector = MostConstrainedSelector()

        for p in all_pairs:
            tracker.mark_covered(p)

        pair = selector.select_next(universe, tracker)
        assert pair is None

    def test_select_next_empty_universe(self):
        universe = PairUniverse()
        tracker = CoverageTracker(universe)
        selector = MostConstrainedSelector()

        pair = selector.select_next(universe, tracker)
        assert pair is None


class TestCoverageSelectorAbstract:

    def test_abstract_cannot_instantiate(self):
        with pytest.raises(TypeError):
            CoverageSelector()
