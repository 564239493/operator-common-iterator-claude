from agent.generators.operator_param_combine.combination_result_generator.constraint import ConstraintEvaluator
from agent.generators.operator_param_combine.combination_result_generator.constraint.ast_parser import ConstraintParser


class TestEvaluator:
    def test_attribute_compare(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse(
            "x.dtype == weight.dtype"
        )

        context = {

            "x": {
                "dtype": "fp16"
            },

            "weight": {
                "dtype": "fp16"
            }

        }

        assert evaluator.evaluate(
            tree,
            context
        )

    def test_bool_expression(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse(
            "x.dtype=='fp16' and x.dimension==2"
        )

        context = {

            "x": {
                "dtype": "fp16",
                "dimension": 2
            }

        }

        assert evaluator.evaluate(
            tree,
            context
        )

    def test_function_call(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()          # 默认 FunctionRegistry 已含 len
        tree = parser.parse("len(x.shape_property)==2")
        assert evaluator.evaluate(tree, {"x": {"shape_property": ["A", "B"]}})

    def test_abs_function_call(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("abs(x.range_value) > 0")
        assert evaluator.evaluate(tree, {"x": {"range_value": -5}})

    def test_min_function_call(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("min(x.shape, y.shape) > 0")
        assert evaluator.evaluate(tree, {"x": {"shape": 3}, "y": {"shape": 5}})
