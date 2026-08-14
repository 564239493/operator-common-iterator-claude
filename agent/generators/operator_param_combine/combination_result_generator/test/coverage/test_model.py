import pytest

from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Parameter, Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue


class TestParameterAndFactor:
    def test_parameter_create(self):
        p = Parameter(
            name="x",
            attributes={
                "dtype": [
                    "fp16"
                ]
            }
        )

        assert p.name == "x"

    def test_parameter_invalid_name(self):
        with pytest.raises(
                ValueError
        ):
            Parameter(
                "",
                {}
            )

    def test_factor_name(self):
        factor = Factor(
            "x",
            "dtype"
        )

        assert (
                factor.name
                ==
                "x.dtype"
        )

    def test_factor_value(self):
        factor = Factor(
            "x",
            "dtype"
        )

        value = FactorValue(
            factor,
            "fp16"
        )

        assert (
                value.value
                ==
                "fp16"
        )
