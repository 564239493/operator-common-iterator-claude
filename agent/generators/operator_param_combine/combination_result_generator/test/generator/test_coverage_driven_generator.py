import pytest

from agent.generators.operator_param_combine.combination_result_generator.coverage.coverage import CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.universe import PairUniverse
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue
from agent.generators.operator_param_combine.combination_result_generator.coverage.pair import Pair

from agent.generators.operator_param_combine.combination_result_generator.generator.coverage_driven_generator import CoverageDrivenGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.candidate_generator import CandidateGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.generator_options import GeneratorOptions
from agent.generators.operator_param_combine.combination_result_generator.generator.model import (
    GenerationResult,
    TestCase,
    TestSuite,
)
from agent.generators.operator_param_combine.combination_result_generator.generator.pair_seed_generator import PairSeedGenerator
from agent.generators.operator_param_combine.combination_result_generator.generator.cache import TestCaseCache
from agent.generators.operator_param_combine.combination_result_generator.generator.scoring import UncoveredPairScoring
from agent.generators.operator_param_combine.combination_result_generator.generator.coverage_selector import RandomUncoveredPairSelector, FirstUncoveredPairSelector

from agent.generators.operator_param_combine.combination_result_generator.model.generator_config import GeneratorConfig
from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import ParameterModel


def create_simple_config():
    x = ParameterModel(name="x", dtype=("fp16", "fp32"), dimension=(2, 4))
    weight = ParameterModel(name="weight", dtype=("int8", "int16"))
    return GeneratorConfig(parameters={"x": x, "weight": weight})


def build_universe_and_tracker(config: GeneratorConfig):
    factor_values = []
    for param in config.parameters.values():
        for attr_name, domain in param.attributes().items():
            if domain:
                for val in domain:
                    factor_values.append(
                        FactorValue(Factor(param.name, attr_name), val)
                    )
    from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_existence_checker import PairExistenceChecker
    builder = PairBuilder()
    all_pairs = builder.build(factor_values)
    constraint = None
    if config.constraints:
        from agent.generators.operator_param_combine.combination_result_generator.constraint.compiler import ConstraintCompiler
        compiler = ConstraintCompiler()
        compiled_constraints = [compiler.compile(expr) for expr in config.constraints]
        from agent.generators.operator_param_combine.combination_result_generator.constraint.evaluator import ConstraintEvaluator

        def evaluate(context):
            for cc in compiled_constraints:
                if not cc.evaluate(context):
                    return False
            return True

        class CombinedConstraint:
            def evaluate(self, context) -> bool:
                return evaluate(context)

        checker = PairExistenceChecker(CombinedConstraint())
        all_pairs = checker.filter_pairs(all_pairs)

    universe = PairUniverse(all_pairs)
    tracker = CoverageTracker(universe)
    return universe, tracker, builder


class TestCoverageDrivenGenerator:

    def test_init(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        assert gen is not None

    def test_generate_returns_result(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100, target_coverage=1.0)

        gen = CoverageDrivenGenerator(
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

    def test_generate_result_type(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert isinstance(result.suite, TestSuite)
        assert isinstance(result.coverage_rate, float)
        assert isinstance(result.iterations, int)
        assert isinstance(result.elapsed_time, float)

    def test_generate_coverage_increases(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100, target_coverage=1.0)

        initial_rate = tracker.coverage_rate()

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result.coverage_rate >= initial_rate

    def test_generate_covers_reasonable(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=500, target_coverage=0.9)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result.coverage_rate > 0

    def test_generate_respects_max_iterations(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=10)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result.iterations <= options.max_iterations

    def test_generate_with_custom_selector(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
            selector=FirstUncoveredPairSelector(),
        )
        result = gen.generate()
        assert result is not None

    def test_generate_with_custom_scoring(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
            scoring=UncoveredPairScoring(builder),
        )
        result = gen.generate()
        assert result is not None

    def test_generate_with_custom_cache(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        cache = TestCaseCache()
        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
            cache=cache,
        )
        result = gen.generate()
        assert result is not None
        assert cache.size() > 0

    def test_generate_with_custom_pool_size(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
            candidate_pool_size=10,
        )
        result = gen.generate()
        assert result is not None

    def test_generate_empty_universe(self):
        config = create_simple_config()
        universe = PairUniverse()
        tracker = CoverageTracker(universe)
        builder = PairBuilder()
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=10)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result is not None
        assert result.coverage_rate == 1.0

    def test_generate_prefilled_coverage(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)

        all_pairs = universe.get_pairs()
        for i in range(min(5, len(all_pairs))):
            tracker.mark_covered(all_pairs[i])

        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100, target_coverage=1.0)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result.coverage_rate > 0

    def test_generate_no_duplicate_cases(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=100)

        cache = TestCaseCache()
        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
            cache=cache,
        )
        result = gen.generate()

        unique_cases = set()
        for case in result.suite:
            key = str(sorted(str(k) + str(v) for k, v in case.values.items()))
            assert key not in unique_cases
            unique_cases.add(key)

    def test_generate_target_not_1(self):
        config = create_simple_config()
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=200, target_coverage=0.5)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result.coverage_rate >= 0.5 or result.iterations >= options.max_iterations

    def test_generate_large_universe(self):
        params = {
            f"p{i}": ParameterModel(name=f"p{i}", dtype=(f"a{i}", f"b{i}"))
            for i in range(5)
        }
        config = GeneratorConfig(parameters=params)
        universe, tracker, builder = build_universe_and_tracker(config)
        candidate_gen = CandidateGenerator(config, random_seed=42)
        options = GeneratorOptions(max_iterations=200)

        gen = CoverageDrivenGenerator(
            universe=universe,
            coverage_tracker=tracker,
            constraint=None,
            config=options,
            candidate_generator=candidate_gen,
            pair_builder=builder,
        )
        result = gen.generate()
        assert result is not None
        assert result.coverage_rate > 0
