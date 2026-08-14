import pytest

from agent.generators.operator_param_combine.combination_result_generator.coverage.coverage import CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.universe import PairUniverse
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue

from agent.generators.operator_param_combine.combination_result_generator.generator.acts_generator import ACTSGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.candidate_generator import CandidateGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.generator_options import GeneratorOptions
from agent.generators.operator_param_combine.combination_result_generator.generator.model import GenerationResult

from agent.generators.operator_param_combine.combination_result_generator.model.generator_config import GeneratorConfig
from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import ParameterModel


def create_config():
    x = ParameterModel(name="x", dtype=("fp16", "fp32"), dimension=(2, 4))
    weight = ParameterModel(name="weight", dtype=("int8", "int16"))
    return GeneratorConfig(parameters={"x": x, "weight": weight})


def build_components(config: GeneratorConfig):
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
    return universe, tracker, builder


class TestACTSGenerator:

    def test_init(self):
        config = create_config()
        universe, tracker, builder = build_components(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = ACTSGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        assert gen is not None

    def test_algorithm_name(self):
        config = create_config()
        universe, tracker, builder = build_components(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = ACTSGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        assert gen.algorithm_name == "ACTS-compatible"

    def test_generate_returns_result(self):
        config = create_config()
        universe, tracker, builder = build_components(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = ACTSGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result is not None
        assert isinstance(result, GenerationResult)

    def test_generate_with_custom_pool_size(self):
        config = create_config()
        universe, tracker, builder = build_components(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = ACTSGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
            candidate_pool_size=20,
        )
        result = gen.generate()
        assert result is not None

    def test_generate_coverage_increases(self):
        config = create_config()
        universe, tracker, builder = build_components(config)
        initial_rate = tracker.coverage_rate()
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100, target_coverage=1.0)

        gen = ACTSGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result.coverage_rate >= initial_rate
