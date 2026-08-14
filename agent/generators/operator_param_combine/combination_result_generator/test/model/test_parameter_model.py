from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import (
    ParameterModel
)

from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import (
    ParameterAttribute
)


class TestParameterModel:
    """
    测试：
    属性模型
    expand_variables
    """

    def test_parameter_expand(self):
        parameter = ParameterModel(

            name="x",

            dtype=("fp16", "fp32"),

            dimension=(2,)

        )

        variables = parameter.expand_variables()

        names = [

            v.full_name

            for v in variables

        ]

        assert "x.dtype" in names

        assert "x.dimension" in names

    def test_empty_attribute(self):
        parameter = ParameterModel(name="x")
        assert (parameter.dtype == ())
