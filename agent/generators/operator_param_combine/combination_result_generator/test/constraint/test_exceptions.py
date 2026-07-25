from constraint.exceptions import (
    ConstraintError,
    ConstraintSyntaxError,
    UnsupportedASTNodeError,
    VariableBindingError,
    FunctionNotRegisteredError,
)


class TestConstraintException:

    def test_exception_hierarchy(self):

        exceptions = [

            ConstraintSyntaxError(
                "syntax"
            ),

            UnsupportedASTNodeError(
                "ast"
            ),

            VariableBindingError(
                "variable"
            ),

            FunctionNotRegisteredError(
                "function"
            ),
        ]


        for exc in exceptions:

            assert isinstance(
                exc,
                ConstraintError
            )



    def test_error_code(self):

        assert (
            ConstraintSyntaxError(
                "x"
            ).error_code
            ==
            "C2001"
        )


        assert (
            UnsupportedASTNodeError(
                "x"
            ).error_code
            ==
            "C2002"
        )



    def test_error_format(self):

        exc = ConstraintSyntaxError(
            "invalid"
        )


        assert str(exc)==(
            "[C2001] invalid"
        )