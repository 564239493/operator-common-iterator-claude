"""``sum(生成器)``：逐元素计算后再聚合（``sum(f(i) for i in ...)``）。

覆盖三类真实文档写法：列表参数按 ``range(len(p))`` 求和、``tensor_list`` 按
``range(len(p))`` 求和、直接遍历序列；``sum([...])`` 列表推导与 ``if`` 过滤同样适用。

迭代窗口必须能静态确定（``range()`` 的界由 ``_int_bounds`` 推出，序列取静态长度表），
窗口内的下标用 guard 短路，窗口外不可达；推不出窗口时保持拒绝，约束在 builder 层
记为 ``conversion_failed``，求解前即 fail-closed 阻断该 case。
"""

import ast
from types import SimpleNamespace

import pytest
import z3

from agent.generators.common_utils.logger_util import init_logger
from agent.generators.param_constraint_solve.expression_preprocess_utils import (
    ASTtoZ3Converter,
)
from agent.generators.param_constraint_solve.param_constraint_utils import (
    ParamConstraintUtils,
    _len_upper_from_exprs,
)
from agent.generators.param_constraint_solve.length_bounds import (
    resolve_unroll_window,
)
from agent.generators.param_constraint_solve.z3_expression_solver_utils import (
    Z3ConstraintBuilder,
)


@pytest.fixture(scope="module", autouse=True)
def _logger(tmp_path_factory):
    init_logger(log_name="test_sum_generator", log_dir=str(tmp_path_factory.mktemp("logs")))
    yield


def _convert(builder, expr):
    return ASTtoZ3Converter(builder).visit(ast.parse(expr, mode="eval").body)


def _fix_list(builder, name, values):
    """把列表参数钉成给定元素值（长度同时确定）。"""
    seq = builder.var_map[name].get_z3_expr()
    builder.solver.add(z3.Length(seq) == len(values))
    for index, value in enumerate(values):
        builder.solver.add(seq[index] == value)
    return seq


# aclnnRepeat：repeats 里非 1 的个数不超过 4（首轴）或 3（非首轴）
REPEAT_EXPR = (
    "sum((1 if repeats.range_value[i] != 1 else 0) for i in range(len(repeats)))"
    " <= (4 if repeats.range_value[0] != 1 else 3)"
)

# aclnnIncreFlashAttentionV3：分页 KV cache 所需 block 数之和不超过 key 第一维
IFAV3_EXPR = (
    "sum(ceilDiv(actualSeqLengthsKv.range_value[i], blockSize.range_value) "
    "for i in range(len(actualSeqLengthsKv))) <= key.shape[0]"
)

# aclnnGroupedMatmulV3：每个 weight 的第一维之和不超过 x 第一维
GMMV3_EXPR = "sum(weight[i].shape[0] for i in range(len(weight))) <= x.shape[0]"


# aclnnMultiScaleDeformableAttnFunction：spatialShape 按 (Hℓ, Wℓ) 成对给出，
# 像素总数 = Σ HℓWℓ
MSDA_EXPR = (
    "sum(spatialShape.range_value[2 * i] * spatialShape.range_value[2 * i + 1]"
    " for i in range(len(spatialShape.range_value) // 2)) == value.shape[1]"
)


def _repeat_builder():
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("repeats", type_hint="list", dtype="int32", length=None)
    return builder


def _ifav3_builder():
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("actualSeqLengthsKv", type_hint="list", dtype="int32", length=None)
    builder.declare_var("blockSize", type_hint="scalar", dtype="int32")
    builder.declare_var("key", type_hint="tensor", dtype="fp32")
    return builder


def _gmmv3_builder():
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("weight", type_hint="tensor_list", dtype="fp32", length=None)
    builder.declare_var("x", type_hint="tensor", dtype="fp32")
    return builder


@pytest.mark.parametrize("builder_factory, expr, length_max, window", [
    (_repeat_builder, REPEAT_EXPR, {"repeats": 8}, 8),
    (_ifav3_builder, IFAV3_EXPR, {"actualSeqLengthsKv": 4}, 4),
    (_gmmv3_builder, GMMV3_EXPR, {"weight": 3}, 3),
])
def test_sum_generator_unrolls_to_arithmetic(builder_factory, expr, length_max, window):
    """``range(len(p))`` 有静态上界时展开成逐项相加，不再是量词。"""
    builder = builder_factory()
    builder.set_static_length_max(length_max)
    converted = _convert(builder, expr)
    assert z3.is_bool(converted)
    assert not z3.is_quantifier(converted)
    assert builder.unrolled_quantifiers == [{"param": "range", "kind": "sum", "length": window}]


def test_repeat_sum_generator_matches_python_semantics():
    """展开结果与 Python 逐元素计数一致（窗口宽于实际长度时靠 guard 短路）。"""
    builder = _repeat_builder()
    builder.set_static_length_max({"repeats": 8})
    expr = _convert(builder, REPEAT_EXPR)

    # 首元素非 1、共 4 个非 1 → 4 <= 4 成立
    builder.solver.push()
    _fix_list(builder, "repeats", [2, 2, 1, 1, 2, 1, 1, 1])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat
    builder.solver.pop()

    # 首元素非 1、共 5 个非 1 → 5 <= 4 不成立
    builder.solver.push()
    _fix_list(builder, "repeats", [2, 2, 1, 2, 2, 2, 1, 1])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()

    # 首元素为 1、4 个非 1 → 4 <= 3 不成立
    builder.solver.push()
    _fix_list(builder, "repeats", [1, 2, 2, 1, 2, 2, 1, 1])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()

    # 实际长度 2 < 静态上界 8：窗口后半段由 guard 短路成 0，不参与计数
    builder.solver.push()
    _fix_list(builder, "repeats", [2, 2])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat
    builder.solver.pop()


def test_gmmv3_sum_generator_matches_python_semantics():
    """``tensor_list`` 的个数是 Int 常量 ``p.length``，上界同样来自 ``len(p)`` 约束。

    ``validate_artifacts`` 拒绝把属性挂在索引结果上（``weight[i].shape``），所以这类
    求和实际按共享 shape 元数据写成 ``len(weight) * weight.shape[0]``；本用例只验证
    转换器对 ``tensor_list`` 长度上界一视同仁。
    """
    builder = _gmmv3_builder()
    builder.set_static_length_max({"weight": 3})
    expr = _convert(builder, GMMV3_EXPR)
    weight = builder.var_map["weight"]
    x = builder.var_map["x"]

    builder.solver.add(weight.length == 2)
    builder.solver.add(z3.Length(weight.elem_shape) == 1)
    builder.solver.add(weight.elem_shape[0] == 2)
    builder.solver.add(z3.Length(x.shape) == 1)

    # 两个 weight，第一维各 2 → 和 4 <= 5
    builder.solver.push()
    builder.solver.add(x.shape[0] == 5, expr)
    assert builder.solver.check() == z3.sat
    builder.solver.pop()

    # 和 4 <= 3 不成立
    builder.solver.push()
    builder.solver.add(x.shape[0] == 3, expr)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()


def test_sum_over_sequence_iteration_uses_static_length():
    """``sum(v for v in p.range_value)`` 直接遍历，按精确长度逐元素求和。"""
    builder = _repeat_builder()
    builder.set_static_lengths({"repeats": 3})
    expr = _convert(builder, "sum(v for v in repeats.range_value) == 6")
    assert not z3.is_quantifier(expr)
    assert builder.unrolled_quantifiers == [{"param": "repeats", "kind": "sum", "length": 3}]

    _fix_list(builder, "repeats", [1, 2, 3])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat

    builder.solver.push()
    builder.solver.add(builder.var_map["repeats"].get_z3_expr()[2] == 4)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()


def test_sum_generator_with_if_filter_counts_only_matching_terms():
    """生成器尾部的 ``if`` 过滤写成 If(C, elt, 0)：只累加满足条件的元素。"""
    builder = _repeat_builder()
    builder.set_static_lengths({"repeats": 4})
    expr = _convert(builder, "sum(v for v in repeats.range_value if v > 1) == 5")
    _fix_list(builder, "repeats", [2, 1, 3, 0])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat


def test_sum_list_comp_unrolls_like_generator():
    """``sum([...])`` 列表推导与生成器表达式同一处理路径。"""
    builder = _repeat_builder()
    builder.set_static_length_max({"repeats": 4})
    expr = _convert(builder, "sum([repeats.range_value[i] for i in range(len(repeats))]) >= 0")
    assert not z3.is_quantifier(expr)
    assert [item["kind"] for item in builder.unrolled_quantifiers] == ["sum"]


def test_sum_generator_without_static_bounds_still_rejected():
    """推不出窗口就拒绝，不用猜测的界改变语义。"""
    builder = _repeat_builder()
    with pytest.raises(NotImplementedError, match="statically known bounds"):
        _convert(builder, REPEAT_EXPR)


@pytest.mark.parametrize("expr, message", [
    ("sum(repeats.range_value[i] for i in range(0, len(repeats), 2))", "step"),
    ("sum(repeats.range_value[i] for i in range(len(repeats)) "
     "for j in range(2))", "multiple for clauses"),
    ("sum(v for v in repeats.range_value)", "statically known length"),
])
def test_sum_generator_unsupported_shapes_raise(expr, message):
    builder = _repeat_builder()
    builder.set_static_length_max({"repeats": 4})
    with pytest.raises(NotImplementedError, match=message):
        _convert(builder, expr)


def test_msda_sum_generator_uses_floor_div_bound():
    """``range(len(p) // 2)``：整除的界由被除数区间换算而来。"""
    builder = _repeat_builder()
    builder.declare_var("spatialShape", type_hint="list", dtype="int32", length=None)
    builder.declare_var("value", type_hint="tensor", dtype="fp32")
    builder.set_static_length_max({"spatialShape": 4})
    expr = _convert(builder, MSDA_EXPR)
    assert not z3.is_quantifier(expr)
    # 窗口 [0, 4 // 2)
    assert builder.unrolled_quantifiers == [{"param": "range", "kind": "sum", "length": 2}]

    value = builder.var_map["value"]
    builder.solver.add(z3.Length(value.shape) == 2)
    builder.solver.add(value.shape[1] == 1 * 2 + 3 * 4)

    # spatialShape = [1, 2, 3, 4] → 1*2 + 3*4 = 14
    builder.solver.push()
    _fix_list(builder, "spatialShape", [1, 2, 3, 4])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat
    builder.solver.pop()

    # 只有前两层参与（len=2 → 0 项，和 0）；钉成 14 应不成立
    builder.solver.push()
    _fix_list(builder, "spatialShape", [1, 2])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()


def test_sum_generator_drops_constraint_as_conversion_failed():
    """转换失败仍 fail-closed：builder 记 conversion_failed，由求解主流程阻断。"""
    builder = _repeat_builder()
    builder.add_constraints({"C-1": REPEAT_EXPR})
    drops = builder.dropped_constraints
    assert len(drops) == 1
    assert drops[0]["reason"] == "conversion_failed"
    assert "statically known bounds" in drops[0]["error"]


# ---------------------------------------------------------------------------
# 预扫描：从约束里收集列表长度上界（range(len(p)) / sum() 展开的窗口来源）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("len(a) <= 4", 4),
        ("len(a) < 5", 4),
        ("len(a) == 3", 3),
        ("3 >= len(a)", 3),
        ("a.length <= 6", 6),
        ("len(a) in [2, 4]", 4),
        ("len(a) >= 2", None),
        ("len(b) <= 4", None),
    ],
)
def test_len_upper_from_exprs(expr, expected):
    assert _len_upper_from_exprs([expr], "a") == expected


def test_len_upper_ignores_disjunctive_branch():
    assert _len_upper_from_exprs(["len(a) <= 3 or len(a) == 9"], "a") is None


def _len_utils(types=None, doc_lengths=None):
    """构造只带长度上界解析所需字段的 ParamConstraintUtils（type 用文档类型名）。"""
    from agent.generators.common_model_definition import ValueWithSrcText

    types = types or {}
    doc_lengths = doc_lengths or {}
    names = {"repeats", "x"} | set(types) | set(doc_lengths)
    utils = object.__new__(ParamConstraintUtils)
    utils.case_input_map = {
        name: SimpleNamespace(type=types.get(name, "scalars")) for name in names
    }
    utils.param_combinations = None
    utils._len_hard_exprs = []
    utils.operator_rule_data = SimpleNamespace(
        inputs={
            name: SimpleNamespace(
                array_length=(ValueWithSrcText(value=doc_lengths[name])
                              if name in doc_lengths else "N/A"))
            for name in names
        },
        outputs={},
    )
    return utils


def test_length_max_records_hard_expr_for_unroll():
    utils = _len_utils(types={"x": "tensor"})
    builder = _repeat_builder()
    constraints = [
        SimpleNamespace(expr="len(repeats) <= 8 and len(x) <= 4"),
    ]
    assert utils._static_length_max_for_unroll(constraints, builder) == {"repeats": 8}
    # 推出的上界须并入 domain_static，否则被偏好消解丢弃后窗口会悄悄变大
    assert utils._len_hard_exprs == ["len(repeats) <= 8"]


def test_length_max_skips_undeclared_params():
    utils = _len_utils()
    builder = Z3ConstraintBuilder(timeout_ms=0)
    constraints = [SimpleNamespace(expr="len(repeats) <= 8")]
    assert utils._static_length_max_for_unroll(constraints, builder) == {}
    assert utils._len_hard_exprs == []


def test_sum_constant_summand_counts_elements():
    """`sum(1 for ...)` 纯常量被求和项：计数语义（visit_Constant 返回裸 int，包回 z3）。"""
    builder = _repeat_builder()
    builder.set_static_length_max({"repeats": 4})
    seq = builder.var_map["repeats"].get_z3_expr()

    expr = _convert(builder, "sum(1 for i in range(len(repeats))) == len(repeats)")
    builder.solver.push()
    builder.solver.add(z3.Length(seq) == 3, expr)
    assert builder.solver.check() == z3.sat
    builder.solver.pop()

    # 常量 2：3 项 → 和 6；4 项 → 8 ≠ 6
    expr2 = _convert(builder, "sum(2 for i in range(len(repeats))) == 6")
    builder.solver.push()
    builder.solver.add(z3.Length(seq) == 3, expr2)
    assert builder.solver.check() == z3.sat
    builder.solver.pop()
    builder.solver.push()
    builder.solver.add(z3.Length(seq) == 4, expr2)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()


def test_length_max_uses_doc_array_length_first():
    """文档 array_length 是第一档：[[2, 6]] 取 6，约束档不再覆盖；文档档同样要硬断言。"""
    utils = _len_utils(doc_lengths={"repeats": [[2, 6]]})
    builder = _repeat_builder()
    constraints = [SimpleNamespace(expr="len(repeats) <= 9")]
    assert utils._static_length_max_for_unroll(constraints, builder) == {"repeats": 6}
    assert utils._len_hard_exprs == ["len(repeats) <= 6"]


def test_length_max_doc_enum_takes_max():
    """array_length 枚举 [4, 5] 取最大 5。"""
    utils = _len_utils(doc_lengths={"repeats": [4, 5]})
    builder = _repeat_builder()
    constraints = [SimpleNamespace(expr="len(repeats) >= 1")]
    assert utils._static_length_max_for_unroll(constraints, builder) == {"repeats": 5}
    assert utils._len_hard_exprs == ["len(repeats) <= 5"]


def test_length_max_falls_back_to_constraints_without_doc():
    """文档没写 array_length 时仍走约束档（原行为不变）。"""
    utils = _len_utils()
    builder = _repeat_builder()
    constraints = [SimpleNamespace(expr="len(repeats) <= 8 and len(x) <= 4")]
    assert utils._static_length_max_for_unroll(constraints, builder) == {"repeats": 8}
    assert utils._len_hard_exprs == ["len(repeats) <= 8"]


def test_length_max_clamps_sum_window_bound_above_unroll_limit():
    """文档上界超阈值时，只有 sum 窗口参数截断到阈值并照常硬断言。

    多档声明 ``[[1,128],[1,1024]]`` 塌成 1024（A2 平台其实最多 128），
    截断后窗口是 128；``len(p) <= 128`` 进入硬断言，窗口内的展开精确。
    """
    utils = _len_utils(doc_lengths={"repeats": [[1, 128], [1, 1024]]})
    builder = _repeat_builder()
    constraints = [SimpleNamespace(expr=REPEAT_EXPR)]
    assert utils._static_length_max_for_unroll(constraints, builder) == {"repeats": 128}
    assert utils._len_hard_exprs == ["len(repeats) <= 128"]

    builder.set_static_length_max({"repeats": 128})
    converted = _convert(builder, REPEAT_EXPR)
    assert not z3.is_quantifier(converted)
    assert builder.unrolled_quantifiers == [{"param": "range", "kind": "sum", "length": 128}]


def test_length_max_clamps_text_bound_only_inside_sum_window():
    """文档没写时，约束文本推出来的超大上界同样只对 sum 窗口截断。"""
    utils = _len_utils()
    builder = _repeat_builder()
    constraints = [
        SimpleNamespace(expr="len(repeats) <= 200"),
        SimpleNamespace(expr=REPEAT_EXPR),
    ]
    assert utils._static_length_max_for_unroll(constraints, builder) == {"repeats": 128}
    assert utils._len_hard_exprs == ["len(repeats) <= 128"]


def test_length_max_does_not_clamp_non_sum_bound():
    """非 sum 参数超阈值时保持不登记（量词回退），不产生截断与硬断言。"""
    utils = _len_utils(doc_lengths={"repeats": 1024})
    builder = _repeat_builder()
    constraints = [SimpleNamespace(expr="len(repeats) >= 1")]
    assert utils._static_length_max_for_unroll(constraints, builder) == {}
    assert utils._len_hard_exprs == []

    utils = _len_utils()
    constraints = [SimpleNamespace(expr="len(repeats) <= 200")]
    assert utils._static_length_max_for_unroll(constraints, builder) == {}
    assert utils._len_hard_exprs == []


def test_length_max_clamps_only_the_sum_parameter():
    """同一批约束里两个参数都超阈值：只有出现在 sum 窗口的那个被截断。"""
    utils = _len_utils(doc_lengths={"repeats": 1024, "x": 500})
    builder = _repeat_builder()
    builder.declare_var("x", type_hint="list", dtype="int32", length=None)
    constraints = [
        SimpleNamespace(expr=REPEAT_EXPR),
        SimpleNamespace(expr="len(x) >= 400"),
    ]
    assert utils._static_length_max_for_unroll(constraints, builder) == {"repeats": 128}
    assert utils._len_hard_exprs == ["len(repeats) <= 128"]


def test_length_max_clamp_prefers_tighter_text_bound():
    """文档多档声明塌成 1024 时，窗口取约束文本里更小的上界（按 128 展开会超时）。"""
    utils = _len_utils(doc_lengths={"repeats": [[1, 128], [1, 1024]]})
    builder = _repeat_builder()
    constraints = [
        SimpleNamespace(expr="len(repeats) <= 8"),
        SimpleNamespace(expr=REPEAT_EXPR),
    ]
    assert utils._static_length_max_for_unroll(constraints, builder) == {"repeats": 8}
    assert utils._len_hard_exprs == ["len(repeats) <= 8"]


def test_resolve_unroll_window_prefers_tighter_text_bound():
    """allow_clamp 时文本界更小就取文本界；不更小仍落阈值。"""
    assert resolve_unroll_window(
        "repeats", 1024, ["len(repeats) <= 8"], allow_clamp=True) == (8, 1024)
    assert resolve_unroll_window(
        "repeats", 1024, ["len(repeats) <= 300"], allow_clamp=True) == (128, 1024)


def test_resolve_unroll_window_scopes_clamp_to_sum():
    """``allow_clamp`` 只由调用方按 sum 窗口传入；未开启时超阈值返回 None。"""
    assert resolve_unroll_window("repeats", 1024, [], allow_clamp=True) == (128, 1024)
    assert resolve_unroll_window("repeats", 1024, [], allow_clamp=False) == (None, None)
    assert resolve_unroll_window("repeats", 8, []) == (8, None)
    assert resolve_unroll_window(
        "repeats", None, ["len(repeats) <= 300"], allow_clamp=True) == (128, 300)
    assert resolve_unroll_window("repeats", None, []) == (None, None)


def test_int_bounds_prefers_exact_length_over_clamped_window():
    """精确长度小于截断后的上界时：窗口取小者，避免按满窗口展开。"""
    builder = _repeat_builder()
    builder.set_static_lengths({"repeats": 4})
    builder.set_static_length_max({"repeats": 128})
    converter = ASTtoZ3Converter(builder)
    seq = builder.var_map["repeats"].get_z3_expr()
    assert converter._int_bounds(z3.Length(seq)) == (0, 4)
