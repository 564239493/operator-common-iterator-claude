import pytest

from agent.generators.operator_param_combine.combination_result_generator.generator.exceptions import (
    GeneratorError,
    InvalidGeneratorConfigError,
    GenerationFailedError,
    CoverageNotReachedError,
)

class TestException:

    def test_generator_error(self):

        error = GeneratorError(
            "generator error"
        )

        assert error.error_code == "G4000"

        assert (
            str(error)
            ==
            "[G4000] generator error"
        )


    def test_invalid_generator_config_error(self):

        error = InvalidGeneratorConfigError(
            "invalid config"
        )

        assert error.error_code == "G4001"

        assert (
            str(error)
            ==
            "[G4001] invalid config"
        )


    def test_generation_failed_error(self):

        error = GenerationFailedError(
            "generation failed"
        )

        assert error.error_code == "G4002"

        assert (
            str(error)
            ==
            "[G4002] generation failed"
        )


    def test_coverage_not_reached_error(self):

        error = CoverageNotReachedError(
            "coverage not reached"
        )

        assert error.error_code == "G4003"

        assert (
            str(error)
            ==
            "[G4003] coverage not reached"
        )


    def test_raise_invalid_generator_config_error(self):

        with pytest.raises(
            InvalidGeneratorConfigError
        ):
            raise InvalidGeneratorConfigError(
                "invalid config"
            )


    def test_raise_generation_failed_error(self):

        with pytest.raises(
            GenerationFailedError
        ):
            raise GenerationFailedError(
                "generation failed"
            )


    def test_raise_coverage_not_reached_error(self):

        with pytest.raises(
            CoverageNotReachedError
        ):
            raise CoverageNotReachedError(
                "coverage not reached"
            )

    def test_exception_inheritance(self):

        error = InvalidGeneratorConfigError(
            "config"
        )

        assert isinstance(
            error,
            GeneratorError,
        )