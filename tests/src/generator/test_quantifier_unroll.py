"""all()/any() 定长展开（生成期）测试。

覆盖两部分：
1. `resolve_static_lengths`：从约束文本与组合期硬钉长度解析唯一长度；
2. `ASTtoZ3Converter`：长度静态确定时把量词展开成逐元素 And/Or，语义等价。
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
    resolve_static_lengths,
)
from agent.generators.param_constraint_solve.z3_expression_solver_utils import (
    MAX_STATIC_UNROLL,
    Z3ConstraintBuilder,
)


@pytest.fixture(scope="module", autouse=True)
def _logger(tmp_path_factory):
    # 生成器模块的 LazyLogger 需要显式初始化（与真实运行一致）。
    init_logger(log_name="test_quantifier_unroll", log_dir=str(tmp_path_factory.mktemp("logs")))
    yield


def _convert(builder, expr):
    return ASTtoZ3Converter(builder).visit(ast.parse(expr, mode="eval").body)


def test_probe_unroll_limit_matches_generator():
    # probe_param_pins 是纯标准库脚本，不 import 生成器；两处上限须人工保持一致。
    from scripts import probe_param_pins as pp

    assert pp.UNROLL_LIMIT == MAX_STATIC_UNROLL


# ---------------------------------------------------------------------------
# resolve_static_lengths
# ---------------------------------------------------------------------------


def test_exact_equality_gives_length():
    assert resolve_static_lengths(["len(a) == 2"]) == {"a": 2}
    assert resolve_static_lengths(["2 == len(a)"]) == {"a": 2}


def test_equality_chain_propagates_length():
    exprs = ["len(a) == len(b) and len(b) == 3"]
    assert resolve_static_lengths(exprs) == {"a": 3, "b": 3}


def test_chained_comparison_is_supported():
    assert resolve_static_lengths(["len(a) == len(b) == 2"]) == {"a": 2, "b": 2}


def test_length_attribute_form_is_supported():
    assert resolve_static_lengths(["a.length == 4"]) == {"a": 4}


def test_disjunctive_constraint_is_ignored():
    # or/not/if-else 只保证某分支成立，不能据此断言长度。
    assert resolve_static_lengths(["len(a) == 2 or len(a) == 3"]) == {}
    assert resolve_static_lengths(["not (len(a) == 2)"]) == {}
    assert resolve_static_lengths(["len(a) == (2 if flag else 3)"]) == {}


def test_interval_and_set_bounds_are_not_static():
    assert resolve_static_lengths(["2 <= len(a) <= 4"]) == {}
    assert resolve_static_lengths(["len(a) in [2, 3]"]) == {}
    assert resolve_static_lengths(["len(a) >= 2"]) == {}


def test_presence_guard_does_not_block_conjunction():
    assert resolve_static_lengths(["(x is not None) and len(a) == 4"]) == {"a": 4}


def test_hard_lengths_are_used_and_conflicts_drop():
    assert resolve_static_lengths([], {"a": 5}) == {"a": 5}
    # 组合期硬钉 5 与约束 len(a) == 2 冲突：不展开，交给 Z3 判 UNSAT。
    assert resolve_static_lengths(["len(a) == 2 and len(a) == len(b)"], {"a": 5}) == {}


def test_hard_length_propagates_to_equality_group():
    assert resolve_static_lengths(["len(a) == len(b)"], {"a": 3}) == {"a": 3, "b": 3}


def test_over_limit_length_is_not_unrolled():
    assert resolve_static_lengths([f"len(a) == {MAX_STATIC_UNROLL + 1}"]) == {}


def test_invalid_expression_is_ignored():
    assert resolve_static_lengths(["len(a) ==", "# TODO: len(a) == 2"]) == {}


# ---------------------------------------------------------------------------
# 转换器展开 + 语义等价
# ---------------------------------------------------------------------------


def _list_builder(static_lengths=None):
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("a", type_hint="list", dtype="int32", length=None)
    if static_lengths:
        builder.set_static_lengths(static_lengths)
    return builder


def test_all_is_unrolled_to_and():
    builder = _list_builder({"a": 3})
    expr = _convert(builder, "all(v >= 0 for v in a.range_value)")
    assert not z3.is_quantifier(expr)
    assert z3.is_and(expr)
    assert len(expr.children()) == 3
    assert builder.unrolled_quantifiers == [{"param": "a", "kind": "all", "length": 3}]


def test_any_is_unrolled_to_or():
    builder = _list_builder({"a": 3})
    expr = _convert(builder, "any(v == 7 for v in a.range_value)")
    assert z3.is_or(expr)
    assert len(expr.children()) == 3


def test_without_static_length_keeps_quantifier():
    builder = _list_builder()
    expr = _convert(builder, "all(v >= 0 for v in a.range_value)")
    assert z3.is_quantifier(expr)
    assert builder.unrolled_quantifiers == []


def test_length_over_limit_keeps_quantifier():
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("a", type_hint="list", dtype="int32", length=None)
    # set_static_lengths 会过滤超上限项，转换器仍走量词。
    builder.set_static_lengths({"a": MAX_STATIC_UNROLL + 1})
    expr = _convert(builder, "all(v >= 0 for v in a.range_value)")
    assert z3.is_quantifier(expr)


def test_unrolled_all_matches_quantifier_semantics():
    builder = _list_builder({"a": 3})
    var = builder.var_map["a"].z3_var
    builder.solver.add(z3.Length(var) == 3)
    builder.solver.add(_convert(builder, "all(v >= 0 for v in a.range_value)"))
    # 违反第 2 个元素 → UNSAT；全部非负 → SAT
    builder.solver.push()
    builder.solver.add(var[2] < 0)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()
    assert builder.solver.check() == z3.sat


def test_unrolled_any_matches_quantifier_semantics():
    builder = _list_builder({"a": 2})
    var = builder.var_map["a"].z3_var
    builder.solver.add(z3.Length(var) == 2)
    builder.solver.add(_convert(builder, "any(v == 7 for v in a.range_value)"))
    builder.solver.push()
    builder.solver.add(var[0] != 7, var[1] != 7)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()
    assert builder.solver.check() == z3.sat


def test_constant_range_is_unrolled():
    # 常数界 range() 无需维度个数信息即可确定范围，直接展开。
    builder = _list_builder()
    expr = _convert(builder, "all(v >= 0 for v in range(3))")
    assert not z3.is_quantifier(expr)
    assert z3.is_and(expr)
    assert len(expr.children()) == 3


def test_shape_range_without_rank_bound_keeps_quantifier():
    # 没有文档维度个数范围（dimensions）时推不出上界，保持量词。
    builder = _tensor_builder()
    expr = _convert(builder, "all(self.shape[i] == 1 for i in range(len(self.shape) - 2))")
    assert z3.is_quantifier(expr)
    assert builder.unrolled_quantifiers == []


def test_rank_bounds_feed_int_bounds():
    builder = _tensor_builder({"self.shape": 4})
    converter = ASTtoZ3Converter(builder)
    stop = converter.visit(ast.parse("len(self.shape) - 2", mode="eval").body)
    # 维度个数的下界保守取 0（不依赖文档是否允许 0 维），上界取文档最大值 4。
    assert converter._int_bounds(stop) == (0 - 2, 4 - 2)


def test_shape_range_with_rank_bound_is_unrolled():
    builder = _tensor_builder({"self.shape": 4, "A.shape": 4})
    expr = _convert(builder, "all(self.shape[i] == 1 for i in range(len(self.shape) - 2))")
    assert not z3.is_quantifier(expr)
    assert z3.is_and(expr)
    # stop 的上界 = 4 - 2 = 2，start = 0 → 展开 2 条。
    assert len(expr.children()) == 2
    assert builder.unrolled_quantifiers == [{"param": "range", "kind": "all", "length": 2}]


def test_rank_bound_with_max_and_negative_index_is_unrolled():
    builder = _tensor_builder({"self.shape": 8, "A.shape": 8})
    expr = _convert(
        builder,
        "all(self.shape[-2-j] == 1 or A.shape[-2-j] == 1 "
        "for j in range(1, max(len(self.shape), len(A.shape)) - 1))",
    )
    assert not z3.is_quantifier(expr)
    # stop 的上界 = max(8, 8) - 1 = 7，start = 1 → 展开 1..6 共 6 条。
    assert len(expr.children()) == 6


def test_tensor_list_shape_range_is_unrolled():
    # tensor_list 的 ``.shape`` 落在 ``<name>.elem.shape`` 常量上；维度个数上限表
    # 必须用同一个键，否则 ScatterList C-002 这类量词不会展开（实测 40 分钟 0 用例）。
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("varRef", type_hint="tensor_list", dtype="fp32", length=None)
    builder.declare_var("updates", type_hint="tensor", dtype="fp32")
    builder.set_static_rank_max({"varRef.elem.shape": 8, "updates.shape": 8})
    expr = _convert(
        builder,
        "all(varRef.shape[-i] == updates.shape[-i] or varRef.shape[-i] == 1 "
        "or updates.shape[-i] == 1 "
        "for i in range(1, min(len(varRef.shape), len(updates.shape)) + 1))",
    )
    assert not z3.is_quantifier(expr)
    # stop 的上界 = min(8, 8) + 1 = 9，start = 1 → 展开 1..8 共 8 条。
    assert len(expr.children()) == 8


def test_range_with_step_keeps_quantifier():
    # 步长非 1 时不能按逐下标展开，保持量词。
    builder = _tensor_builder({"self.shape": 4})
    expr = _convert(builder, "all(self.shape[i] == 1 for i in range(0, len(self.shape), 2))")
    assert z3.is_quantifier(expr)


def test_negative_start_range_is_unrolled():
    # aclnnSmoothL1Loss C-001 的形态：range 窗口从负下标开始（从尾部对齐比较）。
    # 一元负号可推界 + 负窗口允许展开后，这条不再回退成 ForAll。
    builder = _tensor_builder({"self.shape": 8, "A.shape": 8})
    expr = _convert(
        builder,
        "all((self.shape[i] == A.shape[i] or self.shape[i] == 1 or A.shape[i] == 1) "
        "for i in range(-min(len(self.shape), len(A.shape)), 0))",
    )
    assert not z3.is_quantifier(expr)
    # 下界取 -min(8, 8) = -8，上界 0 → 展开 -8..-1 共 8 条。
    assert len(_conjuncts(expr)) == 8
    assert builder.unrolled_quantifiers == [{"param": "range", "kind": "all", "length": 8}]


def test_negative_window_unroll_matches_python_indexing():
    """展开负下标窗口后，下标经 ``If(idx < 0, Len+idx, idx)`` 归一，与执行期一致。"""
    rank = 3
    builder = _tensor_builder({"self.shape": rank})
    expr = _convert(builder, "all(self.shape[i] == 1 for i in range(-2, 0))")
    assert not z3.is_quantifier(expr)
    assert len(_conjuncts(expr)) == 2
    shape = builder.var_map["self"].shape
    builder.solver.add(z3.Length(shape) == rank)
    builder.solver.add(expr)
    # range(-2, 0) 对应倒数第 2、倒数第 1 维，即下标 1、2
    for index in (1, 2):
        builder.solver.push()
        builder.solver.add(shape[index] != 1)
        assert builder.solver.check() == z3.unsat
        builder.solver.pop()
    # 第 0 维不在窗口内，可自由取值
    builder.solver.push()
    builder.solver.add(shape[0] != 1)
    assert builder.solver.check() == z3.sat
    builder.solver.pop()


def test_int_bounds_understands_unary_minus():
    builder = _tensor_builder({"self.shape": 8, "A.shape": 8})
    converter = ASTtoZ3Converter(builder)
    start = converter.visit(
        ast.parse("-min(len(self.shape), len(A.shape))", mode="eval").body
    )
    assert converter._int_bounds(start) == (-8, 0)


def test_int_bounds_reads_scalar_declared_range():
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("axis", type_hint="scalar", dtype="int", range_value=[-7, 6])
    converter = ASTtoZ3Converter(builder)
    plain = converter.visit(ast.parse("axis.range_value", mode="eval").body)
    assert converter._int_bounds(plain) == (-7, 6)
    shifted = converter.visit(ast.parse("axis.range_value + 1", mode="eval").body)
    assert converter._int_bounds(shifted) == (-6, 7)


def test_scalar_without_declared_integer_range_keeps_quantifier():
    # 无 range_value（或区间非整数）的标量推不出界，仍回退量词。
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("n", type_hint="scalar", dtype="int32")
    builder.declare_var("f", type_hint="scalar", dtype="float", range_value=[0.0, 1.0])
    builder.declare_var("a", type_hint="list", dtype="int32", length=None)
    converter = ASTtoZ3Converter(builder)
    for name in ("n", "f"):
        bound_expr = converter.visit(ast.parse(name, mode="eval").body)
        assert converter._int_bounds(bound_expr) is None


def test_scalar_range_bound_is_unrolled_and_asserted():
    # aclnnDynamicMxQuantV2 C-016 的形态：range 上界是符号轴。
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("a", type_hint="list", dtype="int32", length=None)
    builder.declare_var("n", type_hint="scalar", dtype="int", range_value=[0, 3])
    expr = _convert(builder, "all(a[i] >= 0 for i in range(0, n.range_value))")
    assert not z3.is_quantifier(expr)
    assert builder.unrolled_quantifiers == [{"param": "range", "kind": "all", "length": 3}]
    # 展开依赖声明区间，必须同时把区间断言进求解器，模型才取不到区间外的值。
    builder.solver.push()
    builder.solver.add(builder.var_map["n"].z3_var == 9)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()


def test_param_domain_assertion_shows_up_in_unsat_core():
    """展开注入的区间断言必须可追踪：UNSAT 时 core 里能看到 unroll_domain 标签。

    换成 `solver.add` 这条断言就没有追踪字面量，core 只会列出 `axis == 9`——
    单独看自洽，排障时看不出真正原因是展开窗口把 axis 钉死了。
    """
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("x", type_hint="tensor", dtype="fp32")
    builder.declare_var("y", type_hint="tensor", dtype="fp32")
    builder.declare_var("axis", type_hint="scalar", dtype="int")
    builder.set_static_param_ranges({"axis": (-7, 6)})
    builder.add_constraint(
        "C-1", "all(y.shape[i] == x.shape[i] for i in range(0, axis.range_value))")
    assert builder.asserted_param_domains == {("axis", -7, 6)}
    builder.add_constraint("C-2", "axis.range_value == 9")
    assert builder.solver.check() == z3.unsat
    core = {str(item) for item in builder.solver.unsat_core()}
    assert "unroll_domain:axis[-7,6]" in core


def test_param_domain_assertion_is_deduplicated():
    """同一 (参数, 区间) 在多处展开时只断言一次。"""
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("axis", type_hint="scalar", dtype="int")
    axis = builder.var_map["axis"].z3_var
    before = len(builder.solver.assertions())
    for _ in range(2):
        builder.assert_param_domain("axis", (-7, 6), z3.And(axis >= -7, axis <= 6))
    assert len(builder.solver.assertions()) == before + 1


def test_static_scalar_ranges_from_constraints():
    """标量参数没有文档声明的取值范围，只能从 `lo <= p.range_value <= hi` 推。"""
    utils = object.__new__(ParamConstraintUtils)
    utils.case_input_map = {
        "x": SimpleNamespace(type="tensor"),
        "axis": SimpleNamespace(type="scalar"),
    }
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("x", type_hint="tensor", dtype="fp32")
    builder.declare_var("axis", type_hint="scalar", dtype="int")
    builder.set_static_rank_max({"x.shape": 7})
    constraints = [
        SimpleNamespace(expr="-len(x.shape) <= axis.range_value <= len(x.shape) - 1")
    ]
    assert utils._static_scalar_ranges_for_unroll(constraints, builder) == {"axis": (-7, 6)}


def test_static_scalar_ranges_skip_unbounded_or_disjunctive():
    utils = object.__new__(ParamConstraintUtils)
    utils.case_input_map = {
        "x": SimpleNamespace(type="tensor"),
        "axis": SimpleNamespace(type="scalar"),
    }
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("x", type_hint="tensor", dtype="fp32")
    builder.declare_var("axis", type_hint="scalar", dtype="int")
    builder.set_static_rank_max({"x.shape": 7})
    # 只有单边界 / 出现在析取里 / 边界推不出 → 都不给区间
    for expr in (
        "axis.range_value <= len(x.shape) - 1",
        "-len(x.shape) <= axis.range_value",
        "(-len(x.shape) <= axis.range_value) or (axis.range_value == 3)",
        "-len(x.shape) <= axis.range_value <= n",
    ):
        assert utils._static_scalar_ranges_for_unroll(
            [SimpleNamespace(expr=expr)], builder) == {}


def test_static_scalar_ranges_from_enumeration():
    """`p in [v...]` 形态取最小/最大当区间端点。

    DynamicMxQuantV2 的 `blocksize.range_value in [32, 64, 128]` 这类枚举式条款
    不带比较运算符，走的是 ``ast.In`` 分支。
    """
    utils = object.__new__(ParamConstraintUtils)
    utils.case_input_map = {"blocksize": SimpleNamespace(type="scalar")}
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("blocksize", type_hint="scalar", dtype="int")
    constraints = [SimpleNamespace(expr="blocksize.range_value in [32, 64, 128]")]
    assert utils._static_scalar_ranges_for_unroll(constraints, builder) == {"blocksize": (32, 128)}


def test_static_scalar_ranges_use_conservative_endpoint_for_strict_compare():
    """严格不等号取保守端：`0 <= p < len(x.shape)` 的上界仍是 7，不得缩到 6。

    展开窗口必须是真实可达下标的**超集**（多出的下标被 guard 短路成真），
    因此严格上界只能取被比较表达式的区间上端，不能自行减一。
    """
    utils = object.__new__(ParamConstraintUtils)
    utils.case_input_map = {
        "x": SimpleNamespace(type="tensor"),
        "axis": SimpleNamespace(type="scalar"),
    }
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("x", type_hint="tensor", dtype="fp32")
    builder.declare_var("axis", type_hint="scalar", dtype="int")
    builder.set_static_rank_max({"x.shape": 7})
    for expr in (
        "0 <= axis.range_value < len(x.shape)",
        "len(x.shape) > axis.range_value >= 0",
    ):
        assert utils._static_scalar_ranges_for_unroll(
            [SimpleNamespace(expr=expr)], builder) == {"axis": (0, 7)}


def test_static_scalar_ranges_ignore_todo_skipped_constraints():
    """`# TODO:` 条款在主路径被丢弃，预扫描也不得从中取界。

    取界会连带把区间断言进求解器；若从一条已丢弃的条款里取界，等于凭空
    恢复了一条本该跳过的约束，改变语义。
    """
    utils = object.__new__(ParamConstraintUtils)
    utils.case_input_map = {
        "x": SimpleNamespace(type="tensor"),
        "axis": SimpleNamespace(type="scalar"),
    }
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("x", type_hint="tensor", dtype="fp32")
    builder.declare_var("axis", type_hint="scalar", dtype="int")
    builder.set_static_rank_max({"x.shape": 7})
    constraints = [
        SimpleNamespace(expr="# TODO: -5 <= axis.range_value <= 5"),
        SimpleNamespace(expr="-len(x.shape) <= axis.range_value <= len(x.shape) - 1"),
    ]
    # 只认第二条；TODO 那条若被读进来，区间会收窄成 (-5, 5)。
    assert utils._static_scalar_ranges_for_unroll(constraints, builder) == {"axis": (-7, 6)}


def test_symbolic_axis_quantifier_unrolls_both_halves():
    # DynamicMxQuantV2 的 C-016：两半都是符号界，展开后不再回退 ForAll。
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("x", type_hint="tensor", dtype="fp32")
    builder.declare_var("m", type_hint="tensor", dtype="fp32")
    builder.declare_var("axis", type_hint="scalar", dtype="int")
    builder.set_static_rank_max({"x.shape": 7, "m.shape": 8})
    # 生产路径：区间来自硬约束 `-len(x.shape) <= axis <= len(x.shape) - 1`
    builder.set_static_param_ranges({"axis": (-7, 6)})
    expr = _convert(
        builder,
        "(all(m.shape[i] == x.shape[i] for i in range(0, axis.range_value)) "
        "and all(m.shape[i] == x.shape[i] "
        "for i in range(axis.range_value + 1, len(x.shape))))",
    )
    assert not z3.is_quantifier(expr)
    # 前半：0..axis-1 的轴界 [-7, 6] → 0..5 共 6 条；后半：axis+1..len-1 的保守界
    # [-6, 7) → 13 条。
    assert [item["length"] for item in builder.unrolled_quantifiers] == [6, 13]


def test_pict_seed_scalar_unrolls_through_constraint_range():
    """真实管线形态：标量带的是 PICT 组合种子整数，区间只能从硬约束反推。

    生产日志是 ``[Declare] axis -> scalar (dtype: int64, range_value: 2)``——种子 2
    不是取值范围，``_param_int_range`` 的 ``_range_spec`` 分支对它无效，只能靠
    ``static_param_ranges``。这条走完整条链路：约束文本 → 预扫描 → builder 区间表
    → 展开 → 定义域断言，末尾用同一形态做反证（只有种子、没有区间表时不展开）。

    注意：``solve_z3_constraints`` 里预扫描与 ``set_static_rank_max`` 的**调用顺序**
    不在本测试覆盖范围内（顺序错了预扫描推不出界），由 DMXQ 端到端 run 兜底。
    """
    utils = object.__new__(ParamConstraintUtils)
    utils.case_input_map = {
        "x": SimpleNamespace(type="tensor"),
        "m": SimpleNamespace(type="tensor"),
        "axis": SimpleNamespace(type="scalar"),
    }
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("x", type_hint="tensor", dtype="fp32")
    builder.declare_var("m", type_hint="tensor", dtype="fp32")
    # 关键：range_value 是 PICT 种子整数，不是 [lo, hi] 声明区间。
    builder.declare_var("axis", type_hint="scalar", dtype="int64", range_value=2)
    builder.set_static_rank_max({"x.shape": 7, "m.shape": 8})

    constraints = [
        SimpleNamespace(expr="-len(x.shape) <= axis.range_value <= len(x.shape) - 1")
    ]
    param_ranges = utils._static_scalar_ranges_for_unroll(constraints, builder)
    assert param_ranges == {"axis": (-7, 6)}
    builder.set_static_param_ranges(param_ranges)

    expr = _convert(
        builder,
        "(all(m.shape[i] == x.shape[i] for i in range(0, axis.range_value)) "
        "and all(m.shape[i] == x.shape[i] "
        "for i in range(axis.range_value + 1, len(x.shape))))",
    )
    assert not z3.is_quantifier(expr)
    assert [item["length"] for item in builder.unrolled_quantifiers] == [6, 13]
    # 展开依赖区间，区间必须已断言进求解器（否则模型可越界，展开漏下标）。
    assert builder.asserted_param_domains == {("axis", -7, 6)}

    # 反证：同一形态只给 PICT 种子、不给区间表时不得展开。
    # 2026-10-23 的故障正是这样——种子整数被当成"已知来源"，实际推不出界。
    bare = Z3ConstraintBuilder(timeout_ms=0)
    bare.declare_var("x", type_hint="tensor", dtype="fp32")
    bare.declare_var("m", type_hint="tensor", dtype="fp32")
    bare.declare_var("axis", type_hint="scalar", dtype="int64", range_value=2)
    bare.set_static_rank_max({"x.shape": 7, "m.shape": 8})
    bare_expr = _convert(
        bare, "all(m.shape[i] == x.shape[i] for i in range(0, axis.range_value))")
    assert z3.is_quantifier(bare_expr)


def test_unrolled_range_matches_quantifier_semantics():
    rank = 3
    builder = _tensor_builder({"self.shape": rank})
    expr = _convert(builder, "all(self.shape[i] == 1 for i in range(len(self.shape) - 1))")
    shape = builder.var_map["self"].shape
    builder.solver.add(z3.Length(shape) == rank)
    builder.solver.add(expr)
    # range(len-1) 只覆盖前 rank-1 个下标：第 0 个为 0 必须 UNSAT。
    builder.solver.push()
    builder.solver.add(shape[0] != 1)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()
    # 最后一个下标不在量词范围内，可自由取值。
    builder.solver.push()
    builder.solver.add(shape[2] != 1)
    assert builder.solver.check() == z3.sat
    builder.solver.pop()


def _tensor_builder(rank_max=None):
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("self", type_hint="tensor", dtype="fp32")
    builder.declare_var("A", type_hint="tensor", dtype="fp32")
    if rank_max:
        builder.set_static_rank_max(rank_max)
    return builder


def test_iter_param_name_for_plain_name():
    builder = _list_builder({"a": 2})
    expr = _convert(builder, "all(v >= 0 for v in a)")
    assert z3.is_and(expr)


# ---------------------------------------------------------------------------
# C. 参数化性质测试：展开语义等价 / 上限边界 / 回退条件
# ---------------------------------------------------------------------------


def _conjuncts(expr):
    """拆 And 子句；单元素时 z3.And(x) 会退化成 x 本身，不能直接取 children()。"""
    return list(expr.children()) if z3.is_and(expr) else [expr]


def _disjuncts(expr):
    return list(expr.children()) if z3.is_or(expr) else [expr]


def _length_builder(length):
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("a", type_hint="list", dtype="int32", length=None)
    builder.set_static_lengths({"a": length})
    var = builder.var_map["a"].z3_var
    builder.solver.add(z3.Length(var) == length)
    return builder, var


@pytest.mark.parametrize("length", [1, 2, 3, 5, 8])
def test_unrolled_all_matches_quantifier_at_every_position(length):
    """逐位置违反都 UNSAT、全满足 SAT —— 等价于 ForAll，不只首尾两个点。"""
    builder, var = _length_builder(length)
    expr = _convert(builder, "all(v >= 0 for v in a.range_value)")
    assert len(_conjuncts(expr)) == length
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat
    for index in range(length):
        builder.solver.push()
        builder.solver.add(var[index] < 0)
        assert builder.solver.check() == z3.unsat
        builder.solver.pop()


@pytest.mark.parametrize("length", [1, 2, 3, 5, 8])
def test_unrolled_any_matches_quantifier_at_every_position(length):
    """每个位置单独成立都 SAT、全部不成立 UNSAT —— 等价于 Exists。"""
    builder, var = _length_builder(length)
    expr = _convert(builder, "any(v == 7 for v in a.range_value)")
    assert len(_disjuncts(expr)) == length
    builder.solver.add(expr)
    builder.solver.push()
    for index in range(length):
        builder.solver.add(var[index] != 7)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()
    for index in range(length):
        builder.solver.push()
        builder.solver.add(var[index] == 7)
        assert builder.solver.check() == z3.sat
        builder.solver.pop()


@pytest.mark.parametrize(
    "length, unrolled",
    [(1, True), (2, True), (127, True), (128, True), (129, False), (0, False), (-1, False)],
)
def test_unroll_boundary_is_inclusive_at_limit(length, unrolled):
    """上界 MAX_STATIC_UNROLL 本身可展开，超出/非正长度回退量词。"""
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("a", type_hint="list", dtype="int32", length=None)
    builder.set_static_lengths({"a": length})
    expr = _convert(builder, "all(v >= 0 for v in a.range_value)")
    if unrolled:
        assert len(_conjuncts(expr)) == length
        assert builder.unrolled_quantifiers == [
            {"param": "a", "kind": "all", "length": length}
        ]
    else:
        assert z3.is_quantifier(expr)
        assert builder.unrolled_quantifiers == []


@pytest.mark.parametrize("length", [1, 2, 3, 5, 8])
def test_unrolled_all_keeps_non_monotonic_predicate(length):
    """取值正负交替（谓词对值非单调）时，逐元素 And 仍逐位置生效。"""
    builder, var = _length_builder(length)
    builder.solver.add(_convert(builder, "all(v * v > 0 for v in a.range_value)"))
    # 正负交替的非零取值逐位满足 v*v > 0 → SAT
    builder.solver.push()
    for index in range(length):
        sign = -1 if index % 2 else 1
        builder.solver.add(var[index] == sign * (index + 1))
    assert builder.solver.check() == z3.sat
    builder.solver.pop()
    # 任一位置取 0 → 该位谓词为假 → UNSAT
    for index in range(length):
        builder.solver.push()
        builder.solver.add(var[index] == 0)
        assert builder.solver.check() == z3.unsat
        builder.solver.pop()


def test_nested_quantifiers_unroll_at_both_levels():
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("a", type_hint="list", dtype="int32", length=None)
    builder.declare_var("b", type_hint="list", dtype="int32", length=None)
    builder.set_static_lengths({"a": 2, "b": 2})
    var_a = builder.var_map["a"].z3_var
    var_b = builder.var_map["b"].z3_var
    builder.solver.add(z3.Length(var_a) == 2, z3.Length(var_b) == 2)
    expr = _convert(
        builder,
        "all(all(w >= 0 for w in b.range_value) for v in a.range_value)",
    )
    assert not z3.is_quantifier(expr)
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat
    builder.solver.push()
    builder.solver.add(var_b[0] < 0)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()


def test_presence_guard_does_not_block_converter_unroll():
    builder, _ = _length_builder(2)
    expr = _convert(builder, "(a is not None) and all(v >= 0 for v in a.range_value)")
    assert not z3.is_quantifier(expr)
    assert builder.unrolled_quantifiers == [{"param": "a", "kind": "all", "length": 2}]


@pytest.mark.parametrize("rank", [2, 3, 5])
def test_unrolled_shape_range_matches_quantifier_at_every_position(rank):
    """range(len(shape)-1) 覆盖 0..rank-2；最后一位不在范围内，可自由取值。"""
    builder = _tensor_builder({"self.shape": rank})
    expr = _convert(
        builder, "all(self.shape[i] == 1 for i in range(len(self.shape) - 1))"
    )
    assert not z3.is_quantifier(expr)
    shape = builder.var_map["self"].shape
    builder.solver.add(z3.Length(shape) == rank)
    builder.solver.add(expr)
    for index in range(rank - 1):
        builder.solver.push()
        builder.solver.add(shape[index] != 1)
        assert builder.solver.check() == z3.unsat
        builder.solver.pop()
    builder.solver.push()
    builder.solver.add(shape[rank - 1] != 1)
    assert builder.solver.check() == z3.sat
    builder.solver.pop()


def test_range_with_negative_step_keeps_quantifier():
    builder = _tensor_builder({"self.shape": 4})
    expr = _convert(
        builder,
        "all(self.shape[i] == 1 for i in range(len(self.shape) - 1, -1, -1))",
    )
    assert z3.is_quantifier(expr)
    assert builder.unrolled_quantifiers == []


def test_range_with_free_scalar_stop_keeps_quantifier():
    # stop 是自由标量时推不出上界，不能按固定宽度展开。
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("n", type_hint="scalar", dtype="int32")
    builder.declare_var("a", type_hint="list", dtype="int32", length=None)
    expr = _convert(builder, "all(a[i] >= 0 for i in range(n))")
    assert z3.is_quantifier(expr)
    assert builder.unrolled_quantifiers == []


def test_static_lengths_exclude_tensor_list_and_free_length_params():
    # tensor_list 的 ``len`` 走 elem 维度语义，不参与 Seq 定长展开；
    # free-length 参数的组合长度只是 PICT 软偏好，不能当硬事实。
    utils = object.__new__(ParamConstraintUtils)
    utils.case_input_map = {
        "a": SimpleNamespace(type="scalars", length=3),
        "free": SimpleNamespace(type="scalars", length=2),
        "tl": SimpleNamespace(type="tensors", length=2),
        "t": SimpleNamespace(type="tensor", length=4),
    }
    utils.param_combinations = None
    assert utils._static_lengths_for_unroll([], {"free"}) == {"a": 3}


def test_static_lengths_merge_constraint_facts_with_hard_lengths():
    utils = object.__new__(ParamConstraintUtils)
    utils.case_input_map = {
        "a": SimpleNamespace(type="scalars", length=3),
        "p": SimpleNamespace(type="scalars", length=None),
        "q": SimpleNamespace(type="scalars", length=None),
    }
    utils.param_combinations = None
    constraints = [SimpleNamespace(expr="len(p) == len(q) and len(q) == 4")]
    assert utils._static_lengths_for_unroll(constraints, set()) == {
        "a": 3,
        "p": 4,
        "q": 4,
    }


@pytest.mark.parametrize("length", [1, 2, 3, 8, MAX_STATIC_UNROLL])
def test_exact_length_within_limit_resolves(length):
    assert resolve_static_lengths([f"len(a) == {length}"]) == {"a": length}
    assert resolve_static_lengths([], {"a": length}) == {"a": length}


@pytest.mark.parametrize("length", [0, MAX_STATIC_UNROLL + 1, 1000])
def test_length_outside_unroll_range_does_not_resolve(length):
    assert resolve_static_lengths([f"len(a) == {length}"]) == {}
    assert resolve_static_lengths([], {"a": length}) == {}
