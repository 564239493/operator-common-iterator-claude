from agent.generators.operator_param_combine.combination_result_generator.model.exceptions import (
    ModelError,
    EmptyParameterNameError,
    VariableNotFoundError,
    ValueIdNotFoundError,
    ContextVariableMissingError,
)


class TestExceptionCleanup:

    def test_exception_hierarchy(self):

        exceptions = [

            EmptyParameterNameError(
                "test"
            ),

            VariableNotFoundError(
                "test"
            ),

            ValueIdNotFoundError(
                "test"
            ),

            ContextVariableMissingError(
                "test"
            ),

        ]


        for e in exceptions:

            assert isinstance(
                e,
                ModelError
            )