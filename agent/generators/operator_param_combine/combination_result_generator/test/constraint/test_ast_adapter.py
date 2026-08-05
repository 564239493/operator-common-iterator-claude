import ast

import pytest


from constraint.ast_parser import (
    ConstraintParser,
)


from constraint.ast_adapter import (
    ASTAdapter,
)


from constraint.exceptions import (
    UnsupportedASTNodeError,
)


class TestASTAdapter:

    def test_validate_valid_expression(self):

        parser = ConstraintParser()

        adapter = ASTAdapter()

        tree = parser.parse(
            "x.dtype == weight.dtype"
        )

        adapter.validate(
            tree
        )

    def test_validate_compare_operator(self):
        parser = ConstraintParser()

        adapter = ASTAdapter()

        tree = parser.parse(
            "x.dimension >= 2"
        )

        adapter.validate(tree)

    def test_validate_bool_operator(self):
        parser = ConstraintParser()

        adapter = ASTAdapter()

        tree = parser.parse(
            "x.dtype == 'fp16' and x.dimension == 2"
        )

        adapter.validate(tree)


    def test_attribute_path(self):


        parser = ConstraintParser()

        adapter = ASTAdapter()


        tree = parser.parse(
            "x.dtype == 1"
        )

        compare = tree.body


        attribute = compare.left


        result = (
            adapter
            .get_attribute_path(
                attribute
            )
        )


        assert result == "x.dtype"


    def test_unsupported_import(self):


        tree = ast.parse(
            "import os"
        )

        adapter = ASTAdapter()

        with pytest.raises(
            UnsupportedASTNodeError
        ):

            adapter.validate(
                tree
            )


    def test_unsupported_lambda(self):

        tree = ast.parse(
            "lambda x:x"
        )

        adapter = ASTAdapter()


        with pytest.raises(
            UnsupportedASTNodeError
        ):

            adapter.validate(
                tree
            )