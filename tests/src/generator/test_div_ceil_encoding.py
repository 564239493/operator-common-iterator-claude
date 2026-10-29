"""整数除法 / 向上取整编码测试（ceil、CeilDiv、CeilAlign、RoundUp）。

对应改动（A1）：
1. `expression_preprocess_utils.ASTtoZ3Converter` 新增 ceil 族调用：
   `ceil(a/b)`、`Ceil(a/b)`、`ceildiv(a,b)`、`CeilDiv(a,b)`、`ceilDiv(a,b)`，
   以及 `CeilAlign` / `AlignUp` / `RoundUp`；编码为“新整数 q + 符号感知夹逼”。
   **`//` 的既有编码不动**（A2 需先找到真实反例再评估），下面的 floor 网格用例
   是回归护栏。
2. `case_expr_evaluator.SAFE_BUILTINS` 同步补齐同名函数，保证真实执行期
   （回归求值）与求解期语义一致。

编码约定（勿回退）：
- 除数符号未知时夹逼写成 `If(b>0, 正带, If(b<0, 负带, True))`。末支必须是
  `True`：`solver.add` 的断言会穿透外层 `If/Implies/or` 守卫，若末支为
  `False`，守卫为假且除数=0 的场景会被误杀（实测 unsat）。
- 常量除数为 0 在转换期抛 `ValueError`，记入 `dropped_constraints`，
  该场景 fail-closed 产 0 用例（与“文档写错了”应有的表现一致）。

放置位置：`tests/src/generator/test_div_ceil_encoding.py`
注意：`.gitignore` 有 `/tests/` 规则，新增文件需 `git add -f` 才会被跟踪。
"""

import re

import pytest
import z3

from agent.generators.common_utils.logger_util import init_logger
from agent.generators.param_constraint_solve.case_expr_evaluator import eval_constraint
from agent.generators.param_constraint_solve.expression_preprocess_utils import (
    ASTtoZ3Converter,
)
from agent.generators.param_constraint_solve.z3_expression_solver_utils import (
    Z3ConstraintBuilder,
)


@pytest.fixture(scope="module", autouse=True)
def _logger(tmp_path_factory):
    # 生成器模块的 LazyLogger 需要显式初始化（与真实运行一致）。
    init_logger(log_name="test_div_ceil_encoding", log_dir=str(tmp_path_factory.mktemp("logs")))
    yield


def _build(exprs, decls=("x", "n", "b", "f")):
    builder = Z3ConstraintBuilder(timeout_ms=20000)
    for name in decls:
        builder.declare_var(name, dtype="int")
    for i, expr in enumerate(exprs):
        builder.add_constraint(f"c{i}", expr)
    return builder


def _value(exprs, var, decls=("x", "n", "b", "f")):
    builder = _build(exprs, decls)
    assert builder.solver.check() == z3.sat
    const = z3.Const(var, z3.IntSort())
    return builder.solver.model().eval(const, model_completion=True).as_long()


def _check(exprs, decls=("x", "n", "b", "f")):
    return str(_build(exprs, decls).solver.check())


# ---------------------------------------------------------------------------
# 向上取整：常量除数
# ---------------------------------------------------------------------------


def test_ceil_constant_divisor():
    assert _value(["n == ceil(x / 64)", "x == 4096"], "n") == 64
    assert _value(["n == ceil(x / 64)", "x == 4097"], "n") == 65
    assert _check(["n == ceil(x / 64)", "x == 4096", "n == 63"]) == "unsat"
    # 结论要能反推回去：给定 n 时 x 的可行区间是 [(n-1)*64+1, n*64]
    assert _check(["n == ceil(x / 64)", "x == 64", "n == 1"]) == "sat"
    assert _check(["n == ceil(x / 64)", "x == 65", "n == 1"]) == "unsat"


# ---------------------------------------------------------------------------
# 向上取整：符号除数（正 / 负）
# ---------------------------------------------------------------------------


def test_ceil_symbolic_positive_divisor():
    assert _value(["n == ceil(x / b)", "x == 100", "b == 8"], "n") == 13
    assert _check(["n == ceil(x / b)", "x == 100", "b == 8", "n == 12"]) == "unsat"


def test_ceil_symbolic_negative_divisor():
    # ceil(100 / -8) = -12（向 +∞ 取整，与 Python -((-a)//b) 一致）
    assert _value(["n == ceil(x / b)", "x == 100", "b == -8"], "n") == -12
    assert _check(["n == ceil(x / b)", "x == 100", "b == -8", "n == -13"]) == "unsat"


# ---------------------------------------------------------------------------
# 与 Python 逐值对照（含负数被除数 / 除数）
# ---------------------------------------------------------------------------

_GRID = [
    (a, b)
    for a in range(-8, 9)
    for b in (-8, -3, -1, 1, 3, 8)
]


@pytest.mark.parametrize("a,b", _GRID)
def test_floor_div_matches_python(a, b):
    # x // b 与被除数符号无关地“向 -∞ 取整”，这是今天的语义，改动不得回归。
    assert _value(["n == x // b", f"x == {a}", f"b == {b}"], "n") == a // b


@pytest.mark.parametrize("a,b", _GRID)
def test_ceil_matches_python(a, b):
    assert _value(["n == ceil(x / b)", f"x == {a}", f"b == {b}"], "n") == -((-a) // b)


# ---------------------------------------------------------------------------
# 文档里的同义写法
# ---------------------------------------------------------------------------

_SPELLINGS = [
    "ceildiv(x, 64)",
    "CeilDiv(x, 64)",
    "ceilDiv(x, 64)",
    "Ceil(x / 64)",
    "ceil(x / 64)",
]


@pytest.mark.parametrize("call", _SPELLINGS)
def test_ceil_spellings_equivalence(call):
    assert _value([f"n == {call}", "x == 4096"], "n") == 64
    assert _value([f"n == {call}", "x == 4097"], "n") == 65
    assert _check([f"n == {call}", "x == 4096", "n == 63"]) == "unsat"


def test_ceil_align_nested():
    # M = CeilAlign(CeilDiv(H, 32), 2)：H=65 → ceil(65/32)=3 → align 到 2 → 4
    assert _value(["n == CeilAlign(CeilDiv(x, 32), 2)", "x == 65"], "n") == 4
    assert _check(["n == CeilAlign(CeilDiv(x, 32), 2)", "x == 65", "n == 3"]) == "unsat"
    assert _value(["n == AlignUp(CeilDiv(x, 32), 2)", "x == 65"], "n") == 4


def test_round_up():
    # RoundUp 在文档里也是“对齐到倍数”，不是向上取整除。
    assert _value(["n == RoundUp(x, 16)", "x == 100"], "n") == 112
    assert _check(["n == RoundUp(x, 16)", "x == 100", "n == 100"]) == "unsat"


def test_ceil_identity_on_integer_variable():
    # ceil(整数) = 本身，不应引入新变量
    builder = _build(["n == ceil(x)", "x == 37"], ("x", "n"))
    assert builder.solver.check() == z3.sat
    assert not [a for a in builder.solver.assertions() if "__div_" in str(a)]


# ---------------------------------------------------------------------------
# 守卫作用域：全局断言不得穿透 If / Implies / or
# ---------------------------------------------------------------------------


def test_vacuous_branch_with_zero_divisor_stays_sat():
    # (f == 0) or (约束)：f == 0 时约束不生效，除数=0 不得把场景拖成 unsat。
    assert _check(
        ["(f == 0) or (n == ceil(x / b))", "f == 0", "b == 0", "x == 100", "n == 7"]
    ) == "sat"


def test_active_branch_still_constrains():
    assert _check(
        ["(f == 0) or (n == ceil(x / b))", "f == 1", "b == 8", "x == 100", "n == 12"]
    ) == "unsat"


def test_active_branch_with_zero_divisor_matches_legacy():
    # 今天 ToInt(ToReal(x)/ToReal(0)) 是“静默可满足”，这里保持一致（不回归）。
    # 若日后要 fail-closed，必须先把夹逼做成表达式内合取，不能只改末支。
    assert _check(
        ["(f == 0) or (n == ceil(x / b))", "f == 1", "b == 0", "x == 100", "n == 7"]
    ) == "sat"


# ---------------------------------------------------------------------------
# 编码细节：去重 / 常量折叠 / 常量除零
# ---------------------------------------------------------------------------


def test_division_var_shared_across_constraints():
    # 缓存挂在 builder 上（每条约束都会 new 转换器，挂在转换器上等于不去重）。
    builder = _build(["n == ceil(x / 8)", "n == ceil(x / 8)", "x == 100"], ("x", "n"))
    names = set()
    for assertion in builder.solver.assertions():
        names |= set(re.findall(r"__div_\d+", str(assertion)))
    assert names == {"__div_1"}


def test_constant_operands_fold_without_new_var():
    builder = _build(["n == ceil(4096 / 64)", "n == 64"], ("n",))
    assert builder.solver.check() == z3.sat
    assert not [a for a in builder.solver.assertions() if "__div_" in str(a)]


def test_constant_zero_divisor_records_conversion_failure():
    builder = _build(["n == ceil(x / 0)"], ("x", "n"))
    drops = builder.dropped_constraints
    assert drops and "division by zero" in str(drops)


# ---------------------------------------------------------------------------
# 真实执行期（回归求值）与求解期同语义
# ---------------------------------------------------------------------------


def test_evaluator_supports_ceil_spellings():
    assert eval_constraint("ceil(513 / 32) == 17", {}) == (True, None)
    assert eval_constraint("Ceil(513 / 32) == 17", {}) == (True, None)
    assert eval_constraint("CeilDiv(513, 32) == 17", {}) == (True, None)
    assert eval_constraint("ceildiv(513, 32) == 17", {}) == (True, None)
    assert eval_constraint("ceilDiv(513, 32) == 17", {}) == (True, None)


def test_evaluator_align_spellings():
    assert eval_constraint("CeilAlign(65, 2) == 66", {}) == (True, None)
    assert eval_constraint("AlignUp(65, 2) == 66", {}) == (True, None)
    assert eval_constraint("RoundUp(100, 16) == 112", {}) == (True, None)


def test_evaluator_ceil_div_is_exact_for_large_values():
    # 超过 2^53 时 `a / b`（真除法）会丢精度，CeilDiv 走整数运算不丢。
    big = 2 ** 53 + 1
    assert eval_constraint(f"CeilDiv({big}, 1) == {big}", {}) == (True, None)
    assert eval_constraint(f"CeilAlign({big}, 8) == {big + 7}", {}) == (True, None)


def test_evaluator_ceil_of_true_division_is_exact_for_large_values():
    # 修复前 `ceil(A / B)` 走浮点真除法：`(2**53 + 1) / 1` 被舍成 `2**53`。
    # 求值前由 _CeilDivRewriter 改写成整数运算，与求解期夹逼编码同语义。
    big = 2 ** 53 + 1
    assert eval_constraint(f"ceil({big} / 1) == {big}", {}) == (True, None)
    assert eval_constraint(f"Ceil({big} / 8) == {big // 8 + 1}", {}) == (True, None)


def test_evaluator_ceil_of_division_matches_python_semantics():
    # 整除/非整除边界与负数被除数
    assert eval_constraint("ceil(513 / 32) == 17", {}) == (True, None)
    assert eval_constraint("ceil(512 / 32) == 16", {}) == (True, None)
    assert eval_constraint("ceil(-7 / 2) == -3", {}) == (True, None)
    assert eval_constraint("Ceil(7 / 2) == 4", {}) == (True, None)
    assert eval_constraint("ceil(513 / 32) == 16", {}) == (False, None)


def test_evaluator_ceil_keeps_non_division_forms():
    # 单参数与非 `/` 参数不改写：ceil(1.5) 走 math.ceil，ceil(A // B) 本身是整数运算
    assert eval_constraint("ceil(1.5) == 2", {}) == (True, None)
    assert eval_constraint("ceil(7 // 2) == 3", {}) == (True, None)
