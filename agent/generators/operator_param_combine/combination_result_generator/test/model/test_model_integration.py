from agent.generators.operator_param_combine.combination_result_generator.model.parameter_model import (
    ParameterModel
)

from agent.generators.operator_param_combine.combination_result_generator.model.variable_registry import (
    VariableRegistry
)

from agent.generators.operator_param_combine.combination_result_generator.model.value_registry import (
    ValueRegistry
)

from agent.generators.operator_param_combine.combination_result_generator.model.constraint_context import (
    ConstraintContext
)

class TestModelIntegration:

    def test_full_model_flow(self):


        parameter = ParameterModel(

            name="x",

            dtype=(

                "fp16",

                "fp32",

            )

        )


        variable_registry = VariableRegistry()


        variable_registry.register_parameter(
            parameter
        )


        variable = variable_registry.get_by_name(
            "x.dtype"
        )


        value_registry = ValueRegistry()


        value_id = value_registry.register(
            "fp16"
        )


        ctx = ConstraintContext()


        ctx.set_value(

            variable.full_name,

            value_registry.get_value(
                value_id
            )

        )


        assert (

            ctx.get_value(
                "x.dtype"
            )

            ==
            "fp16"

        )