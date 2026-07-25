
import ast


import pytest


from constraint.ast_parser import (
    ConstraintParser,
)


from constraint.exceptions import (
    ConstraintSyntaxError,
)


class TestAstPaser:

    def test_parse_valid_expression(self):


        parser = ConstraintParser()


        tree = parser.parse(
            "x.dtype == weight.dtype"
        )


        assert isinstance(
            tree,
            ast.Expression
        )



    def test_parse_constant_expression(self):


        parser = ConstraintParser()


        tree = parser.parse(
            "x.dimension == 2"
        )


        assert isinstance(
            tree,
            ast.Expression
        )



    def test_empty_expression(self):


        parser = ConstraintParser()


        with pytest.raises(
            ConstraintSyntaxError
        ):

            parser.parse(
                ""
            )



    def test_invalid_syntax(self):


        parser = ConstraintParser()


        with pytest.raises(
            ConstraintSyntaxError
        ):

            parser.parse(
                "x.dtype =="
            )



    def test_non_string_expression(self):


        parser = ConstraintParser()


        with pytest.raises(
            ConstraintSyntaxError
        ):

            parser.parse(
                123
            )