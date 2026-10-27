"""`remove_missing_param_exprs` 的 IfExp 存在性分支回归测试。

背景：aclnnQuantSparseFlashMla 生成 0 用例。组合行里常见双向存在性约束
``(X is not None) if (A is not None) else (X is None)``，旧实现只要测试式
引用了缺失参数就直接返回 body，于是 X 缺失时整条约束折成字面量 False，
组合行必然 unsat，500 次求解全部失败。

本文件锁定修复后的语义：
- 测试式只含存在性判断 → 按存在性化简后取正确分支；
- 测试式在化简后仍保留取值比较（如 ``X.range_value == 3``）→ 保留 IfExp，
  由后续求解器按实际取值判定；
- 测试式还引用了缺失参数的取值（无法判定）→ 整条 True（空泛满足）。
"""

import pytest

from agent.generators.operator_param_combine.combination_result_generator.constraint.remover import (
    remove_missing_param_exprs,
)

# 真实运行（aclnnQuantSparseFlashMla iter_001）中的约束形状
_BICONDITIONAL = "(K is not None) if (M is not None) else (K is None)"
_GUARDED_OPTIONAL = (
    "(K is not None) if (X.range_value == 3 and Y.range_value != 1) else (K is None)"
)


@pytest.mark.parametrize(
    "existing, expected",
    [
        # M 存在、K 缺失 → 条件 True，取 then 分支：K is not None（K 缺失）→ False
        # 这是真实违反（M 要求 K 必须存在），必须保持 False
        ({"M"}, "False"),
        # M 缺失、K 缺失 → 条件 False，取 else 分支：K is None（K 缺失）→ True
        (set(), "True"),
        # 都存在 → 条件 True，then 分支 → True
        ({"K", "M"}, "True"),
        # K 存在、M 缺失 → 条件 False，else 分支 K is None（K 存在）→ False
        ({"K"}, "False"),
    ],
    ids=["K_missing_M_present", "both_missing", "both_present", "K_present_M_missing"],
)
def test_biconditional_presence_selects_right_branch(existing, expected):
    assert remove_missing_param_exprs(_BICONDITIONAL, existing) == expected


@pytest.mark.parametrize(
    "existing, expected",
    [
        # 测试式还引用了缺失的取值参数 → 条件不可判定 → 空泛满足
        # （旧实现会走 body，K 缺失时折成 False）
        ({"K"}, "True"),
        (set(), "True"),
        # 只剩 Y 缺失 → 仍不可判定
        ({"K", "X"}, "True"),
        # 取值参数都在 → 保留 IfExp，分支已按存在性化简，交给求解器判定
        (
            {"K", "X", "Y"},
            "True if X.range_value == 3 and Y.range_value != 1 else False",
        ),
    ],
    ids=["K_present_XY_missing", "all_missing", "Y_missing", "all_present_symbolic"],
)
def test_guarded_optional_undecidable_is_vacuous(existing, expected):
    assert remove_missing_param_exprs(_GUARDED_OPTIONAL, existing) == expected


def test_symbolic_test_preserved_with_branches_simplified():
    expr = "(K is not None) if (X.range_value == 3) else (K is None)"
    # K 存在：then True / else False，条件留给求解器
    assert remove_missing_param_exprs(expr, {"K", "X"}) == "True if X.range_value == 3 else False"
    # K 缺失：then False / else True（真实违反只能在 X!=3 时满足）
    assert remove_missing_param_exprs(expr, {"X"}) == "False if X.range_value == 3 else True"


def test_ifexp_with_trivial_else():
    # else 分支恒 True，then 分支的 K 缺失 → 条件 True 时仍应得到 False
    expr = "(K is not None) if (M is not None) else True"
    assert remove_missing_param_exprs(expr, {"M"}) == "False"
    assert remove_missing_param_exprs(expr, set()) == "True"


def test_nested_ifexp_undecidable_outer():
    # 真实运行中的嵌套形状：外层条件依赖缺失参数 → 整条 True
    expr = (
        "(softmaxLseOutOptional.shape == [1]) "
        "if (returnSoftmaxLse.range_value == False) "
        "else ((softmaxLseOutOptional.shape == [q.shape[0], 1]) "
        "if (layoutQOptional.range_value == 'BSND') else True)"
    )
    assert remove_missing_param_exprs(expr, {"q"}) == "True"


def test_nested_ifexp_decidable_outer_keeps_inner():
    # 外层可判 True → 取 then；外层可判 False → 取 else（内层不可判定 → True）
    expr = (
        "(K is not None) "
        "if (M is not None) "
        "else ((K is not None) if (X.range_value == 1) else (K is None))"
    )
    assert remove_missing_param_exprs(expr, {"M"}) == "False"
    assert remove_missing_param_exprs(expr, set()) == "True"


def test_ifexp_all_params_present_keeps_expression():
    expr = "(a > 1) if (m > 0) else (a < 0)"
    assert remove_missing_param_exprs(expr, {"a", "m"}) == "a > 1 if m > 0 else a < 0"


def test_plain_presence_checks_unchanged():
    assert remove_missing_param_exprs("K is not None", set()) == "False"
    assert remove_missing_param_exprs("K is None", set()) == "True"
    assert remove_missing_param_exprs("K is not None", {"K"}) == "True"
    assert remove_missing_param_exprs("K is None", {"K"}) == "False"


def test_real_run_shapes_never_fold_to_false_when_optional_missing():
    """真实运行的约束形状：Optional 参数缺失时不得折成字面量 False。"""
    exprs = [
        "(sequsedOriKvOptional is not None) "
        "if (oriBlockTableOptional is not None) else True",
        "(cmpKvDescaleOptional is None) "
        "if (cmpKvOptional is None) else (cmpKvDescaleOptional is not None)",
        "(cuSeqlensCmpKvOptional is not None) "
        "if (layoutKvOptional.range_value == 'TND' and cmpKvOptional is not None) "
        "else (cuSeqlensCmpKvOptional is None)",
        "(oriTopkLengthOptional is not None) "
        "if (oriMaskMode.range_value == 0 and oriSparseIndicesOptional is not None) "
        "else (oriTopkLengthOptional is None)",
    ]
    for expr in exprs:
        assert remove_missing_param_exprs(expr, set()) != "False"
    # 双向约束在“条件侧存在、被约束侧缺失”时仍是真实违反，必须保留 False
    assert remove_missing_param_exprs(exprs[1], {"cmpKvOptional"}) == "False"
