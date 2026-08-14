import pytest

from agent.generators.operator_param_combine.combination_result_generator.coverage.coverage import CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair import Pair
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.universe import PairUniverse
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue

from agent.generators.operator_param_combine.combination_result_generator.generator.candidate_generator import CandidateGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.exceptions import CandidateGenerationError
from agent.generators.operator_param_combine.combination_result_generator.generator.model import TestCase
from agent.generators.operator_param_combine.combination_result_generator.generator.model import TestValue

from agent.generators.operator_param_combine.combination_result_generator.model.generator_config import GeneratorConfig
from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import ParameterModel


def build_small_universe():
    config = GeneratorConfig(
        parameters={
            "x": ParameterModel(name="x", dtype=("fp16", "fp32"), dimension=(2, 4)),
            "y": ParameterModel(name="y", dtype=("int8", "int16")),
        }
    )
    factor_values = []
    for param in config.parameters.values():
        for attr_name, domain in param.attributes().items():
            if domain:
                for val in domain:
                    factor_values.append(
                        FactorValue(Factor(param.name, attr_name), val)
                    )
    builder = PairBuilder()
    all_pairs = builder.build(factor_values)
    universe = PairUniverse(all_pairs)
    tracker = CoverageTracker(universe)
    return config, universe, tracker, builder


def create_greedy_generator(
    universe=None,
    coverage_tracker=None,
    config=None,
    random_seed=42,
):
    if config is None:
        config, _, _, _ = build_small_universe()
    return CandidateGenerator(
        config=config,
        universe=universe,
        coverage_tracker=coverage_tracker,
        random_seed=random_seed,
    )


class TestCandidateGreedy:

    def test_greedy_returns_tuple(self):
        config, universe, tracker, _ = build_small_universe()
        gen = create_greedy_generator(universe, tracker, config)
        result = gen.generate_candidate()
        assert isinstance(result, tuple)
        assert len(result) == 2
        case, gain = result
        assert isinstance(case, TestCase)
        assert isinstance(gain, int)

    def test_greedy_gain_non_negative(self):
        config, universe, tracker, _ = build_small_universe()
        gen = create_greedy_generator(universe, tracker, config)
        _, gain = gen.generate_candidate()
        assert gain >= 0

    def test_greedy_gain_matches_compute_gain_fast(self):
        config, universe, tracker, builder = build_small_universe()
        gen = create_greedy_generator(universe, tracker, config)
        case, gain = gen.generate_candidate()
        factor_values = []
        for param_name, attrs in case.values.items():
            for attr_name, val in attrs.items():
                factor_values.append(
                    FactorValue(Factor(param_name, attr_name), val)
                )
        pairs = builder.build(factor_values)
        mask = 0
        for p in pairs:
            idx = universe.get_index(p)
            if idx is not None:
                mask |= (1 << idx)
        uncovered_mask = tracker.uncovered_pairs_mask()
        expected_gain = (mask & uncovered_mask).bit_count()
        assert gain == expected_gain

    def test_greedy_deterministic(self):
        config, universe, tracker, _ = build_small_universe()
        gen1 = create_greedy_generator(universe, tracker, config, random_seed=42)
        gen2 = create_greedy_generator(universe, tracker, config, random_seed=42)
        case1, gain1 = gen1.generate_candidate()
        case2, gain2 = gen2.generate_candidate()
        assert case1.values == case2.values
        assert gain1 == gain2

    def test_greedy_with_fixed_values(self):
        config, universe, tracker, _ = build_small_universe()
        gen = create_greedy_generator(universe, tracker, config)
        case, _ = gen.generate_candidate(fixed_values={"x": {"dtype": "fp16"}})
        assert case.get_value("x", "dtype") == "fp16"

    def test_non_greedy_returns_gain_zero(self):
        gen = CandidateGenerator(
            config=GeneratorConfig(
                parameters={
                    "x": ParameterModel(name="x", dtype=("fp16", "fp32")),
                }
            ),
            random_seed=42,
        )
        assert not gen.is_greedy
        case, gain = gen.generate_candidate()
        assert gain == 0
        assert isinstance(case, TestCase)

    def test_is_greedy_property(self):
        config, universe, tracker, _ = build_small_universe()
        gen_greedy = create_greedy_generator(universe, tracker, config)
        assert gen_greedy.is_greedy is True
        gen_random = CandidateGenerator(config=config, random_seed=42)
        assert gen_random.is_greedy is False

    def test_greedy_positive_gain_with_uncovered_pairs(self):
        config, universe, tracker, _ = build_small_universe()
        gen = create_greedy_generator(universe, tracker, config)
        _, gain = gen.generate_candidate()
        assert gain > 0

    def test_greedy_zero_gain_when_all_covered(self):
        config, universe, tracker, builder = build_small_universe()
        factor_values = []
        for param in config.parameters.values():
            for attr_name, domain in param.attributes().items():
                if domain:
                    for val in domain:
                        factor_values.append(
                            FactorValue(Factor(param.name, attr_name), val)
                        )
        all_pairs = builder.build(factor_values)
        for p in all_pairs:
            tracker.mark_covered(p)
        gen = create_greedy_generator(universe, tracker, config)
        _, gain = gen.generate_candidate()
        assert gain == 0

    def test_greedy_reuses_provided_seed(self):
        config, universe, tracker, _ = build_small_universe()
        gen = create_greedy_generator(universe, tracker, config, random_seed=99)
        case, _ = gen.generate_candidate()
        assert len(case.values) > 0

    def test_greedy_with_constraint_fallback(self):
        config, universe, tracker, _ = build_small_universe()

        class AlwaysFalseConstraint:
            def evaluate(self, context):
                return False

        gen = CandidateGenerator(
            config=config,
            constraint=AlwaysFalseConstraint(),
            universe=universe,
            coverage_tracker=tracker,
            max_iterations=10,
        )
        assert gen.is_greedy
        with pytest.raises(CandidateGenerationError):
            gen.generate_candidate()