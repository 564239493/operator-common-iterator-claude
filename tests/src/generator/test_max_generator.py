"""``max/min`` 对列表参数的两类写法：整列表取值与生成器取值。

- ``max(p.range_value)`` / ``max(p)``：整列表最值，长度可以符号化，用量词编码；
- ``max(f(i) for i in ...)``：逐项展开后折叠，窗口由 ``_int_bounds`` 或静态长度表推出。

生成器写法遍历整张列表（``p.range_value[i] for i in range(len(p))``、无 ``if`` 过滤）
时与整列表取值同义，直接走量词编码，列表长度不必静态已知。
"""

import ast

import pytest
import z3

from agent.generators.common_utils.logger_util import init_logger
from agent.generators.param_constraint_solve.expression_preprocess_utils import (
    ASTtoZ3Converter,
)
from agent.generators.param_constraint_solve.z3_expression_solver_utils import (
    Z3ConstraintBuilder,
)


@pytest.fixture(scope="module", autouse=True)
def _logger(tmp_path_factory):
    init_logger(log_name="test_max_generator", log_dir=str(tmp_path_factory.mktemp("logs")))
    yield


def _convert(builder, expr):
    return ASTtoZ3Converter(builder).visit(ast.parse(expr, mode="eval").body)


def _fix_list(builder, name, values):
    seq = builder.var_map[name].get_z3_expr()
    builder.solver.add(z3.Length(seq) == len(values))
    for index, value in enumerate(values):
        builder.solver.add(seq[index] == value)
    return seq


# aclnnBlockSparseAttentionV2 文档 594 行：blockSparseMask 第三维 = ceil(max(序列长度) / 128)
BSAV2_EXPR = (
    "CeilDiv(max(actualSeqLengthsOptional.range_value[i] "
    "for i in range(len(actualSeqLengthsOptional))), 128) "
    "== blockSparseMaskOptional.shape[2]"
)

# 同一规则写成整列表取值（不带生成器）
BSAV2_SEQ_EXPR = (
    "CeilDiv(max(actualSeqLengthsOptional.range_value), 128) "
    "== blockSparseMaskOptional.shape[2]"
)


def _bsav2_builder():
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("actualSeqLengthsOptional", type_hint="list", dtype="int32", length=None)
    builder.declare_var("blockSparseMaskOptional", type_hint="tensor", dtype="int8")
    return builder


def _bsav2_assert(builder, expr, lengths, mask_dim):
    builder.solver.push()
    _fix_list(builder, "actualSeqLengthsOptional", lengths)
    mask = builder.var_map["blockSparseMaskOptional"]
    builder.solver.add(z3.Length(mask.shape) == 4)
    builder.solver.add(mask.shape[2] == mask_dim)
    builder.solver.add(expr)
    result = builder.solver.check()
    builder.solver.pop()
    return result


def test_bsav2_max_generator_matches_python_semantics():
    """生成器写法在列表长度不受静态约束时也能转换，且取的是元素真实最大值。"""
    builder = _bsav2_builder()
    expr = _convert(builder, BSAV2_EXPR)
    assert z3.is_bool(expr)

    # max = 129 → ceil(129 / 128) = 2
    assert _bsav2_assert(builder, expr, [8, 129], 2) == z3.sat
    assert _bsav2_assert(builder, expr, [8, 129], 1) == z3.unsat
    # max = 8 → ceil(8 / 128) = 1
    assert _bsav2_assert(builder, expr, [8, 1], 1) == z3.sat


def test_bsav2_max_whole_list_matches_generator_form():
    """``max(p.range_value)`` 与生成器写法编码结果一致。"""
    builder = _bsav2_builder()
    expr = _convert(builder, BSAV2_SEQ_EXPR)
    assert _bsav2_assert(builder, expr, [8, 129], 2) == z3.sat
    assert _bsav2_assert(builder, expr, [8, 129], 1) == z3.unsat


def test_max_generator_over_whole_list_ignores_seed_value():
    """列表元素可被其他约束改大：max 必须跟着元素走，不能折叠成静态种子。"""
    builder = _bsav2_builder()
    expr = _convert(builder, "max(actualSeqLengthsOptional.range_value) == 200")
    seq = builder.var_map["actualSeqLengthsOptional"].get_z3_expr()
    builder.solver.add(z3.Length(seq) == 2, seq[0] == 1)

    builder.solver.push()
    builder.solver.add(seq[1] == 200, expr)
    assert builder.solver.check() == z3.sat
    builder.solver.pop()

    builder.solver.push()
    builder.solver.add(seq[1] == 199, expr)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()


def test_min_generator_over_whole_list():
    """min 与 max 同一通道。"""
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("repeats", type_hint="list", dtype="int32", length=None)
    expr = _convert(builder, "min(repeats.range_value[i] for i in range(len(repeats))) == 2")
    _fix_list(builder, "repeats", [5, 2, 9])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat

    builder.solver.push()
    builder.solver.add(builder.var_map["repeats"].get_z3_expr()[1] == 3)
    assert builder.solver.check() == z3.unsat
    builder.solver.pop()


def _list_builder():
    builder = Z3ConstraintBuilder(timeout_ms=0)
    builder.declare_var("repeats", type_hint="list", dtype="int32", length=None)
    return builder


def test_max_generator_with_static_bounds_unrolls():
    """窗口能静态确定时逐项展开，不走量词。"""
    builder = _list_builder()
    builder.set_static_length_max({"repeats": 4})
    expr = _convert(builder, "max(repeats.range_value[i] for i in range(len(repeats))) >= 4")
    assert not z3.is_quantifier(expr)
    assert [item["kind"] for item in builder.unrolled_quantifiers] == ["max"]

    _fix_list(builder, "repeats", [1, 2, 3, 4])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat


def test_max_generator_constant_range_unrolls():
    """``range(3)`` 这类常量窗口按常量展开。"""
    builder = _list_builder()
    expr = _convert(builder, "max(repeats.range_value[i] for i in range(2)) == 9")
    assert builder.unrolled_quantifiers == [{"param": "range", "kind": "max", "length": 2}]
    _fix_list(builder, "repeats", [4, 9])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat


def test_max_generator_with_if_filter_uses_static_length():
    """带 ``if`` 过滤时不能退化成整列表取值，仍按静态长度逐项展开。"""
    builder = _list_builder()
    builder.set_static_lengths({"repeats": 4})
    expr = _convert(builder, "max(v for v in repeats.range_value if v < 5) == 3")
    _fix_list(builder, "repeats", [2, 7, 3, 8])
    builder.solver.add(expr)
    assert builder.solver.check() == z3.sat


@pytest.mark.parametrize("expr, message", [
    ("max(repeats.range_value[i] for i in range(0, len(repeats), 2))", "step"),
    ("max(repeats.range_value[i] for i in range(len(repeats)) for j in range(2))",
     "multiple for clauses"),
    ("max(v for v in repeats.range_value if v > 1)", "statically known length"),
])
def test_max_generator_unsupported_shapes_raise(expr, message):
    builder = _list_builder()
    builder.set_static_length_max({"repeats": 4})
    with pytest.raises(NotImplementedError, match=message):
        _convert(builder, expr)
