import pytest


from agent.generators.operator_param_combine.combination_result_generator.model.constraint_context import (
    ConstraintContext
)


from agent.generators.operator_param_combine.combination_result_generator.model.exceptions import (
    ContextVariableMissingError,
    ContextVariableConflictError
)

class TestConstraintContext:
    """
    验证：
    设置
    查询
    覆盖
    异常
    """

    def test_context_set_get(self):


        ctx = ConstraintContext()


        ctx.set_value(
            "x.dtype",
            "fp16"
        )


        assert (

            ctx.get_value(
                "x.dtype"
            )

            ==
            "fp16"

        )



    def test_context_missing(self):


        ctx=ConstraintContext()


        with pytest.raises(
            ContextVariableMissingError
        ):

            ctx.get_value(
                "x.dtype"
            )



    def test_context_override(self):


        ctx=ConstraintContext()


        ctx.set_value(
            "x.dtype",
            "fp16"
        )


        ctx.set_value(
            "x.dtype",
            "int8"
        )


        assert (

            ctx.get_value(
                "x.dtype"
            )

            ==
            "int8"

        )