from agent.generators.operator_param_combine.combination_result_generator.constraint import ConstraintEvaluator, EvalState
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

    def test_missing_variable_returns_unknown(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("x.dtype == weight.dtype")
        result = evaluator.evaluate(tree, {"x": {"dtype": "fp16"}})
        assert result is EvalState.UNKNOWN

    def test_violated_constraint_returns_false(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("x.dtype == weight.dtype")
        result = evaluator.evaluate(
            tree,
            {"x": {"dtype": "fp16"}, "weight": {"dtype": "fp32"}}
        )
        assert result is EvalState.FALSE

    def test_satisfied_constraint_returns_true(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("x.dtype == weight.dtype")
        result = evaluator.evaluate(
            tree,
            {"x": {"dtype": "fp16"}, "weight": {"dtype": "fp16"}}
        )
        assert result is EvalState.TRUE

    def test_unknown_in_and_propagates(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("x.dtype == 'fp16' and y.dtype == 'fp32'")
        result = evaluator.evaluate(tree, {"x": {"dtype": "fp16"}})
        assert result is EvalState.UNKNOWN

    def test_false_in_and_short_circuits(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("x.dtype == 'fp16' and y.dtype == 'fp32'")
        result = evaluator.evaluate(
            tree,
            {"x": {"dtype": "fp32"}}
        )
        assert result is EvalState.FALSE

    def test_unknown_in_or_propagates(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("x.dtype == 'fp16' or y.dtype == 'fp32'")
        result = evaluator.evaluate(tree, {"x": {"dtype": "fp32"}})
        assert result is EvalState.UNKNOWN

    def test_true_in_or_short_circuits(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("x.dtype == 'fp16' or y.dtype == 'fp32'")
        result = evaluator.evaluate(
            tree,
            {"x": {"dtype": "fp16"}}
        )
        assert result is EvalState.TRUE

    def test_unknown_in_compare_propagates(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("x.dtype == 'fp16'")
        result = evaluator.evaluate(tree, {})
        assert result is EvalState.UNKNOWN

    def test_unknown_in_not_propagates(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("not(x.is_present == True)")
        result = evaluator.evaluate(tree, {})
        assert result is EvalState.UNKNOWN

    def test_unknown_in_ifexp_propagates(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("x.dtype if x.is_present else 'fp16'")
        result = evaluator.evaluate(tree, {})
        assert result is EvalState.UNKNOWN

    def test_truthiness_compat(self):
        parser = ConstraintParser()
        evaluator = ConstraintEvaluator()
        tree = parser.parse("x.dtype == 'fp16'")
        assert evaluator.evaluate(tree, {"x": {"dtype": "fp16"}})
        assert not evaluator.evaluate(tree, {"x": {"dtype": "fp32"}})
