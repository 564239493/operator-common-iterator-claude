if __name__ == '__main__':
    print(0.5 % 32 == 0)
    print(isinstance(False, int))
    dim_count = 0
    if dim_count:
        print(dim_count)
    print("no")

    def test_or_all_false_collapses_to_false_missing(self):
        # 两个子式都是"缺失参数的 is not None"-> False，Or 删空后必须坍缩为 False
        expr = "biasOptional is not None or deqScaleOptional is not None"
        existing = {"x"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "False"

    def test_or_all_false_collapses_to_false_present(self):
        # 两个子式都是"存在参数的 is None"-> False（组合表外携带形态）
        expr = "yOffsetOptional is None or x2OffsetOptional is None"
        existing = {"yOffsetOptional", "x2OffsetOptional"}
        result = remove_missing_param_exprs(expr, existing)
        assert result == "False"