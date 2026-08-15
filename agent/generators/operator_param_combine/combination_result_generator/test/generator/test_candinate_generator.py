import pytest

from agent.generators.operator_param_combine.combination_result_generator.constraint.interfaces import (
    ConstraintProtocol,
)

from agent.generators.operator_param_combine.combination_result_generator.generator.candidate_generator import (
    CandidateGenerator,
)

from agent.generators.operator_param_combine.combination_result_generator.generator.exceptions import (
    CandidateGenerationError,
)

from agent.generators.operator_param_combine.combination_result_generator.model.generator_config import (
    GeneratorConfig,
)

from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import (
    ParameterModel,
)


class AlwaysTrueConstraint:

    def evaluate(
            self,
            context,
    ) -> bool:
        return True


class AlwaysFalseConstraint:

    def evaluate(
            self,
            context,
    ) -> bool:
        return False


def create_config() -> GeneratorConfig:
    x = ParameterModel(
        name="x",
        dtype=(
            "fp16",
            "fp32",
        ),
        dimension=(
            2,
            4,
        ),
    )

    weight = ParameterModel(
        name="weight",
        dtype=(
            "int8",
            "int16",
        ),
    )

    return GeneratorConfig(
        parameters={
            "x": x,
            "weight": weight,
        }
    )


def create_generator(
        constraint=None,
) -> CandidateGenerator:
    return CandidateGenerator(
        config=create_config(),
        constraint=constraint,
        random_seed=1234,
    )


# =====================================================
# Happy Path
# =====================================================
class TestCandidateGenerator:

    def test_generate_candidate(self):
        generator = CandidateGenerator(
            config=create_config(),
        )

        case, _ = generator.generate_candidate()

        assert case is not None

    def test_return_type(self):
        generator = CandidateGenerator(
            config=create_config(),
        )

        case, _ = generator.generate_candidate()

        from agent.generators.operator_param_combine.combination_result_generator.generator.model import TestCase

        assert isinstance(
            case,
            TestCase,
        )

    def test_contains_all_parameters(self):
        generator = CandidateGenerator(
            config=create_config(),
        )

        case, _ = generator.generate_candidate()

        assert "x" in case.values

        assert "weight" in case.values

    def test_contains_attributes(self):
        generator = CandidateGenerator(
            config=create_config(),
        )

        case, _ = generator.generate_candidate()

        assert "dtype" in case.values["x"]

        assert "dimension" in case.values["x"]

    # =====================================================
    # Domain Validation
    # =====================================================

    def test_dtype_from_domain(self):
        generator = CandidateGenerator(
            config=create_config(),
        )

        case, _ = generator.generate_candidate()

        assert (
                case.values["x"]["dtype"]
                in (
                    "fp16",
                    "fp32",
                )
        )

    def test_dimension_from_domain(self):
        generator = CandidateGenerator(
            config=create_config(),
        )

        case, _ = generator.generate_candidate()

        assert (
                case.values["x"]["dimension"]
                in (
                    2,
                    4,
                )
        )

    def test_weight_dtype_from_domain(self):
        generator = CandidateGenerator(
            config=create_config(),
        )

        case, _ = generator.generate_candidate()

        assert (
                case.values["weight"]["dtype"]
                in (
                    "int8",
                    "int16",
                )
        )

    # =====================================================
    # Constraint
    # =====================================================

    def test_constraint_true(self):
        generator = CandidateGenerator(
            config=create_config(),
            constraint=AlwaysTrueConstraint(),
        )

        case, _ = generator.generate_candidate()

        assert case is not None

    def test_constraint_false_raise(self):
        generator = CandidateGenerator(
            config=create_config(),
            constraint=AlwaysFalseConstraint(),
            max_iterations=5,
        )

        with pytest.raises(
                CandidateGenerationError
        ):
            generator.generate_candidate()

    # =====================================================
    # Boundary
    # =====================================================

    def test_single_value_domain(self):
        config = GeneratorConfig(
            parameters={
                "x": ParameterModel(
                    name="x",
                    dtype=("fp16",),
                )
            }
        )

        generator = CandidateGenerator(
            config=config,
        )

        case, _ = generator.generate_candidate()

        assert (
                case.values["x"]["dtype"]
                ==
                "fp16"
        )

    def test_empty_domain_skip(self):
        config = GeneratorConfig(
            parameters={
                "x": ParameterModel(
                    name="x",
                    dtype=(),
                    dimension=(2,),
                )
            }
        )

        generator = CandidateGenerator(
            config=config,
        )

        case, _ = generator.generate_candidate()

        assert (
                "dtype"
                not in case.values["x"]
        )

        assert (
                case.values["x"]["dimension"]
                ==
                2
        )

    def test_only_one_parameter(self):
        config = GeneratorConfig(
            parameters={
                "x": ParameterModel(
                    name="x",
                    dtype=("fp16",),
                )
            }
        )

        generator = CandidateGenerator(
            config=config,
        )

        case, _ = generator.generate_candidate()

        assert tuple(case.values.keys()) == ("x",)

    # =====================================================
    # Random Seed Consistency
    # =====================================================

    def test_same_seed_same_result(self):
        config = create_config()

        generator1 = CandidateGenerator(
            config=config,
            random_seed=123,
        )

        generator2 = CandidateGenerator(
            config=config,
            random_seed=123,
        )

        case1, _ = generator1.generate_candidate()

        case2, _ = generator2.generate_candidate()

        assert (
                case1.values
                ==
                case2.values
        )

    def test_different_seed(self):
        config = create_config()

        generator1 = CandidateGenerator(
            config=config,
            random_seed=1,
        )

        generator2 = CandidateGenerator(
            config=config,
            random_seed=2,
        )

        case1, _ = generator1.generate_candidate()

        case2, _ = generator2.generate_candidate()

        assert isinstance(
            case1.values,
            dict,
        )

        assert isinstance(
            case2.values,
            dict,
        )

    # =====================================================
    # Internal Validation
    # =====================================================

    def test_is_valid_without_constraint(self):
        generator = CandidateGenerator(
            config=create_config(),
        )

        case, _ = generator.generate_candidate()

        assert (
                generator._is_valid(
                    case
                )
                is True
        )

    def test_is_valid_with_true_constraint(self):
        generator = CandidateGenerator(
            config=create_config(),
            constraint=AlwaysTrueConstraint(),
        )

        case, _ = generator.generate_candidate()

        assert (
                generator._is_valid(
                    case
                )
                is True
        )

    # =====================================================
    # Exception Contract
    # =====================================================

    def test_candidate_generation_error_code(self):
        error = CandidateGenerationError(
            "failed"
        )

        assert (
                error.error_code
                ==
                "G4004"
        )

    def test_candidate_generation_error_str(self):
        error = CandidateGenerationError(
            "failed"
        )

        assert (
                str(error)
                ==
                "[G4004] failed"
        )

    def test_fixed_value(self):
        generator = create_generator()

        testcase, _ = generator.generate_candidate(
            {
                "x": {
                    "dtype": "fp16"
                }
            }
        )

        assert (
                testcase.get_value(
                    "x",
                    "dtype"
                )
                == "fp16"
        )

    def test_fixed_value_and_random_fill(self):
        generator = create_generator()

        testcase, _ = generator.generate_candidate(
            {
                "x": {
                    "dtype": "fp16"
                }
            }
        )

        assert (
                testcase.get_value(
                    "x",
                    "dtype"
                )
                == "fp16"
        )

        assert len(
            testcase.values["x"]
        ) > 1
