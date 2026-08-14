from agent.generators.operator_param_combine.combination_result_generator.constraint.ast_parser import ConstraintParser
from agent.generators.operator_param_combine.combination_result_generator.constraint.dependency import DependencyAnalyzer


class TestDependency:

    def test_attribute_dependency(self):
        parser = ConstraintParser()

        analyzer = DependencyAnalyzer()

        tree = parser.parse(
            "x.dtype == weight.dtype"
        )

        result = analyzer.analyze(
            tree
        )

        assert result == {
            "x.dtype",
            "weight.dtype",
        }

    def test_multiple_attributes(self):
        parser = ConstraintParser()

        analyzer = DependencyAnalyzer()
        tree = parser.parse(
            "x.dtype=='fp16' and x.dimension==2"
        )

        result = analyzer.analyze(tree)

        assert result == {
            "x.dtype",
            "x.dimension",
        }

    def test_standalone_name(self):
        parser = ConstraintParser()

        analyzer = DependencyAnalyzer()

        tree = parser.parse(
            "batch_size > 0"
        )

        result = analyzer.analyze(tree)

        assert result == {
            "batch_size"
        }

    def test_multiple_expr(self):
        parser = ConstraintParser()
        analyzer = DependencyAnalyzer()

        tree = parser.parse(
            "not(activation.range_value in [\"geglu\",\"swiglu\",\"reglu\"]) or (x.dtype == \"FLOAT16\" and weight1.dtype == \"FLOAT16\" and weight2.dtype == \"FLOAT16\" and y.dtype == \"FLOAT16\")"
        )

        result = analyzer.analyze(tree)

        assert result == {
            "activation.range_value", "x.dtype", "weight1.dtype", "weight2.dtype", "y.dtype"
        }

    def test_function_call_excludes_function_name(self):
        parser = ConstraintParser()
        analyzer = DependencyAnalyzer()

        tree = parser.parse("len(x.shape) == 2")

        result = analyzer.analyze(tree)

        assert result == {"x.shape"}
        assert "len" not in result

    def test_function_call_with_multiple_args(self):
        parser = ConstraintParser()
        analyzer = DependencyAnalyzer()

        tree = parser.parse("min(x.range_value, y.range_value) > 0")

        result = analyzer.analyze(tree)

        assert result == {"x.range_value", "y.range_value"}
        assert "min" not in result

    def test_nested_function_call(self):
        parser = ConstraintParser()
        analyzer = DependencyAnalyzer()

        tree = parser.parse("abs(len(x.shape_property)) > 0")

        result = analyzer.analyze(tree)

        assert result == {"x.shape_property"}
        assert "abs" not in result
        assert "len" not in result

    def test_generator_exp_excludes_loop_var(self):
        parser = ConstraintParser()
        analyzer = DependencyAnalyzer()

        tree = parser.parse("all(d > 0 for d in x2.shape)")

        result = analyzer.analyze(tree)

        assert result == {"x2.shape"}
        assert "d" not in result
        assert "all" not in result