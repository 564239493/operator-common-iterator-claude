import pytest

from agent.generators.operator_param_combine.combination_result_generator.coverage.coverage import CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.universe import PairUniverse
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue

from agent.generators.operator_param_combine.combination_result_generator.generator.scoring import (
    ScoringStrategy,
    UncoveredPairScoring,
    WeightedUncoveredPairScoring,
)
from agent.generators.operator_param_combine.combination_result_generator.generator.model import TestCase, TestValue


def make_universe_and_tracker():
    builder = PairBuilder()
    factor_values = [
        FactorValue(Factor("x", "dtype"), "fp16"),
        FactorValue(Factor("x", "dtype"), "fp32"),
        FactorValue(Factor("x", "dimension"), 2),
        FactorValue(Factor("weight", "dtype"), "int8"),
    ]
    pairs = builder.build(factor_values)
    universe = PairUniverse(pairs)
    tracker = CoverageTracker(universe)
    return builder, universe, tracker


def make_testcase(x_dtype: str) -> TestCase:
    case = TestCase()
    case.add_value(TestValue(parameter="x", attribute="dtype", value=x_dtype))
    case.add_value(TestValue(parameter="x", attribute="dimension", value=2))
    case.add_value(TestValue(parameter="weight", attribute="dtype", value="int8"))
    return case


class TestUncoveredPairScoring:

    def test_init(self):
        builder = PairBuilder()
        scoring = UncoveredPairScoring(builder)
        assert scoring is not None

    def test_score_zero_coverage(self):
        builder, universe, tracker = make_universe_and_tracker()
        scoring = UncoveredPairScoring(builder)
        case = make_testcase("fp16")
        score = scoring.score(case, universe, tracker)
        assert score > 0

    def test_score_full_coverage(self):
        builder, universe, tracker = make_universe_and_tracker()
        scoring = UncoveredPairScoring(builder)

        all_pairs = universe.get_pairs()
        for pair in all_pairs:
            tracker.mark_covered(pair)

        case = make_testcase("fp16")
        score = scoring.score(case, universe, tracker)
        assert score == 0

    def test_score_partial_coverage(self):
        builder, universe, tracker = make_universe_and_tracker()
        scoring = UncoveredPairScoring(builder)

        all_pairs = universe.get_pairs()
        for i in range(min(2, len(all_pairs))):
            tracker.mark_covered(all_pairs[i])

        case = make_testcase("fp16")
        score = scoring.score(case, universe, tracker)
        assert score >= 0

    def test_score_different_testcases(self):
        builder, universe, tracker = make_universe_and_tracker()
        scoring = UncoveredPairScoring(builder)

        case1 = make_testcase("fp16")
        case2 = make_testcase("fp32")

        score1 = scoring.score(case1, universe, tracker)
        score2 = scoring.score(case2, universe, tracker)
        assert isinstance(score1, int)
        assert isinstance(score2, int)

    def test_score_is_int(self):
        builder, universe, tracker = make_universe_and_tracker()
        scoring = UncoveredPairScoring(builder)
        case = make_testcase("fp16")
        score = scoring.score(case, universe, tracker)
        assert isinstance(score, int)

    def test_score_empty_universe(self):
        builder = PairBuilder()
        universe = PairUniverse()
        tracker = CoverageTracker(universe)
        scoring = UncoveredPairScoring(builder)
        case = make_testcase("fp16")
        score = scoring.score(case, universe, tracker)
        assert score == 0

    def test_score_single_parameter(self):
        builder = PairBuilder()
        case = TestCase()
        case.add_value(TestValue(parameter="x", attribute="dtype", value="fp16"))

        fv = FactorValue(Factor("x", "dtype"), "fp16")
        pairs = builder.build([fv])
        universe = PairUniverse(pairs)
        tracker = CoverageTracker(universe)
        scoring = UncoveredPairScoring(builder)

        score = scoring.score(case, universe, tracker)
        assert score >= 0


class TestWeightedUncoveredPairScoring:

    def test_init(self):
        builder = PairBuilder()
        scoring = WeightedUncoveredPairScoring(builder)
        assert scoring is not None

    def test_score_zero_coverage(self):
        builder, universe, tracker = make_universe_and_tracker()
        scoring = WeightedUncoveredPairScoring(builder)
        case = make_testcase("fp16")
        score = scoring.score(case, universe, tracker)
        assert score > 0

    def test_score_full_coverage(self):
        builder, universe, tracker = make_universe_and_tracker()
        scoring = WeightedUncoveredPairScoring(builder)

        all_pairs = universe.get_pairs()
        for pair in all_pairs:
            tracker.mark_covered(pair)

        case = make_testcase("fp16")
        score = scoring.score(case, universe, tracker)
        assert score == 0

    def test_weighted_score_larger_than_unweighted(self):
        builder, universe, tracker = make_universe_and_tracker()
        uncovered = UncoveredPairScoring(builder)
        weighted = WeightedUncoveredPairScoring(builder)

        case = make_testcase("fp16")

        score1 = uncovered.score(case, universe, tracker)
        score2 = weighted.score(case, universe, tracker)
        assert score2 >= score1

    def test_score_is_int(self):
        builder, universe, tracker = make_universe_and_tracker()
        scoring = WeightedUncoveredPairScoring(builder)
        case = make_testcase("fp16")
        score = scoring.score(case, universe, tracker)
        assert isinstance(score, int)

    def test_scoring_strategy_is_abstract(self):
        with pytest.raises(TypeError):
            ScoringStrategy()
