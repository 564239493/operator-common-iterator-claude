from model.exceptions import EmptyParameterNameError, InvalidVariableError, ValueIdNotFoundError, \
    ContextVariableMissingError, ModelError


class TestException:

    def test_all_exception_inherit(self):
        exceptions = [

            EmptyParameterNameError("x"),

            InvalidVariableError("x"),

            ValueIdNotFoundError("x"),

            ContextVariableMissingError("x"),

        ]

        for e in exceptions:
            assert isinstance(
                e,
                ModelError
            )

    def test_error_code(self):
        assert (
                EmptyParameterNameError("x")
                .error_code
                ==
                "M1202"
        )

        assert (
                ContextVariableMissingError("x")
                .error_code
                ==
                "M1401"
        )