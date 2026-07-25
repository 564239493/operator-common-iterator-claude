from constraint.ast_parser import ConstraintParser
from constraint.dependency import DependencyAnalyzer


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