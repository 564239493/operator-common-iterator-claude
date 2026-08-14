import pytest

from agent.generators.operator_param_combine.combination_result_generator.constraint.cache import ConstraintCache
from agent.generators.operator_param_combine.combination_result_generator.constraint.compiler import ConstraintCompiler
from agent.generators.operator_param_combine.combination_result_generator.constraint.exceptions import ConstraintSyntaxError, UnsupportedASTNodeError


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

    def test_param_names_attribute_deps(self):
        compiler = ConstraintCompiler()

        result = compiler.compile(
            "x.dtype == weight.dtype"
        )

        assert result.param_names == frozenset({"x", "weight"})

    def test_param_names_with_bare_name(self):
        compiler = ConstraintCompiler()

        result = compiler.compile(
            "batch_size > 0"
        )

        assert result.param_names == frozenset({"batch_size"})

    def test_param_names_mixed_bare_and_attribute(self):
        compiler = ConstraintCompiler()

        result = compiler.compile(
            "x.dtype == 'fp16' and batch_size > 0"
        )

        assert result.param_names == frozenset({"x", "batch_size"})

    def test_param_names_with_function_call(self):
        compiler = ConstraintCompiler()

        result = compiler.compile(
            "len(x.shape) == 2"
        )

        assert result.param_names == frozenset({"x"})
        assert "len" not in result.param_names