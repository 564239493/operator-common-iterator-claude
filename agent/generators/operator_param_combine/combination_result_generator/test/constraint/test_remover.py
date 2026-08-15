from agent.generators.operator_param_combine.combination_result_generator.constraint.remover import \
    remove_missing_param_exprs


class TestRemoveMissingParamExprs:

    def test_remove_missing_param_in_or_chain(self):
        expr = (
            "(x.dtype == 'fp32' and weight.dtype == 'fp32' and biasOptional.dtype == 'fp32' "
            "and groupListOptional.dtype == 'int64' and out.dtype == 'fp32') "
            "or (x.dtype == 'fp16' and weight.dtype == 'fp16' and biasOptional.dtype == 'fp16' "
            "and groupListOptional.dtype == 'int64' and out.dtype == 'fp16')"
        )
        existing = {"x", "weight", "groupListOptional", "out"}
        result = remove_missing_param_exprs(expr, existing)
        assert "biasOptional" not in result

    def test_presence_check_is_none_removed_to_true(self):
        expr = "biasOptional is None"
        existing = {"x"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "True"

    def test_presence_check_is_not_none_removed_to_false(self):
        expr = "biasOptional is not None"
        existing = {"x"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "False"

    def test_presence_check_is_none_existing_param_returns_false(self):
        expr = "biasOptional is None"
        existing = {"x", "biasOptional"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "False"

    def test_presence_check_is_not_none_existing_param_returns_true(self):
        expr = "biasOptional is not None"
        existing = {"x", "biasOptional"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "True"

    def test_and_chain_removes_missing_branch(self):
        expr = "x.dtype == 'fp16' and biasOptional.dtype == 'fp32' and weight.dtype == 'fp16'"
        existing = {"x", "weight"}
        result = remove_missing_param_exprs(expr, existing)
        assert "biasOptional" not in result
        assert "x.dtype" in result
        assert "weight.dtype" in result

    def test_or_chain_with_missing_short_circuits_true(self):
        expr = "x.dtype == 'fp16' or biasOptional.dtype == 'fp32'"
        existing = {"x"}
        result = remove_missing_param_exprs(expr, existing)
        # 缺失子式 -> True, or 短路 -> True
        assert result == "True"

    def test_all_missing_returns_true(self):
        expr = "biasOptional.dtype == 'fp32' and deqScale.dtype == 'fp32'"
        existing = {"x"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "True"

    def test_no_missing_params_unchanged(self):
        expr = "x.dtype == 'fp16' and weight.dtype == 'fp16'"
        existing = {"x", "weight"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "x.dtype == 'fp16' and weight.dtype == 'fp16'"

    def test_generator_exp_missing_iter(self):
        expr = "all(d > 0 for d in x2.shape)"
        existing = {"x1"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "True"

    def test_generator_exp_existing_iter_preserved(self):
        expr = "all(d > 0 for d in x2.shape)"
        existing = {"x2"}
        result = remove_missing_param_exprs(expr, existing)
        # 循环变量 d 不应被误判为缺失参数，迭代源 x2.shape 应保留
        assert "x2.shape" in result
        assert "d > 0" in result

    def test_subscript_missing_param(self):
        expr = "x1.shape[1] == x2.shape[0]"
        existing = {"x1"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "True"

    def test_ifexp_missing_test_returns_body(self):
        expr = "('A') if (transposeX2.range_value == False) else ('B')"
        existing = {}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "'A'"

    def test_function_name_not_treated_as_param(self):
        expr = "len(x.shape) == 2"
        existing = {"x"}
        result = remove_missing_param_exprs(expr, existing)
        # len 是函数名不是参数，x 存在，应原样保留
        assert result == "len(x.shape) == 2"

    def test_or_line_expr(self):
        expr = "(x.dtype == 'fp32' and weight.dtype == 'fp32' and biasOptional.dtype == 'fp32' and groupListOptional.dtype == 'int64' and out.dtype == 'fp32') or (x.dtype == 'fp16' and weight.dtype == 'fp16' and biasOptional.dtype == 'fp16' and groupListOptional.dtype == 'int64' and out.dtype == 'fp16') or (x.dtype == 'bf16' and weight.dtype == 'bf16' and biasOptional.dtype == 'fp32' and groupListOptional.dtype == 'int64' and out.dtype == 'bf16')"
        existing = {"x", "weight", "out"}
        result = remove_missing_param_exprs(expr, existing)
        assert "biasOptional" not in result and "groupListOptional" not in result

    def test_exist_contain_name_not_in_expr(self):
        expr = "(x.dtype == 'fp32' and weight.dtype == 'fp32' and biasOptional.dtype == 'fp32' and groupListOptional.dtype == 'int64' and out.dtype == 'fp32') or (x.dtype == 'fp16' and weight.dtype == 'fp16' and biasOptional.dtype == 'fp16' and groupListOptional.dtype == 'int64' and out.dtype == 'fp16') or (x.dtype == 'bf16' and weight.dtype == 'bf16' and biasOptional.dtype == 'fp32' and groupListOptional.dtype == 'int64' and out.dtype == 'bf16')"
        existing = {"x", "weight", "out", "activate"}
        example = "x.dtype == 'FLOAT32' and weight.dtype == 'FLOAT32' and (out.dtype == 'FLOAT32') or (x.dtype == 'FLOAT16' and weight.dtype == 'FLOAT16' and (out.dtype == 'FLOAT16')) or (x.dtype == 'BFLOAT16' and weight.dtype == 'BFLOAT16' and (out.dtype == 'BFLOAT16'))"
        result = remove_missing_param_exprs(expr, existing)
        assert "biasOptional" not in result and "groupListOptional" not in result and "activate" not in result and "x" in result and "weight" in result and "out" in result

    def test_presence_check_is_not_none_not_remove(self):
        expr = "biasOptional is not None"
        existing = {"x", "biasOptional"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "True"


    def test_presence_check_present_and_is_none(self):
        expr = "biasOptional is None or x.dtype == biasOptional.dtype"
        existing = {"x", "biasOptional"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "x.dtype == biasOptional.dtype"

