import pytest

from constraint.cache import ConstraintCache
from constraint.compiler import ConstraintCompiler
from constraint.exceptions import ConstraintSyntaxError, UnsupportedASTNodeError


class TestCompiledConstraint:
    def test_compile_constraint(self):
        compiler = ConstraintCompiler()

        result = compiler.compile(
            "x.dtype == weight.dtype"
        )

        assert (
                result.expression
                ==
                "x.dtype == weight.dtype"
        )

        assert (
                "x.dtype"
                in result.dependencies
        )

        assert (
                "weight.dtype"
                in result.dependencies
        )

    def test_compile_cache(self):
        cache = ConstraintCache()

        compiler = ConstraintCompiler(
            cache=cache
        )

        first = compiler.compile(
            "x.dtype=='fp16'"
        )

        second = compiler.compile(
            "x.dtype=='fp16'"
        )

        assert (
                first.tree
                is
                second.tree
        )

    def test_compile_invalid_expression(self):
        compiler = ConstraintCompiler()

        with pytest.raises(
                ConstraintSyntaxError
        ):
            compiler.compile(
                "import os"
            )

    def test_compile_unsupported_ast(self):
        compiler = ConstraintCompiler()

        with pytest.raises(
                UnsupportedASTNodeError
        ):
            compiler.compile(
                "lambda x:x"
            )