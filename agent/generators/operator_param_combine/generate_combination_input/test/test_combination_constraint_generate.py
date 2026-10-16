"""
单元测试：组合约束链路子句级降级工具。

覆盖《参数组合约束链路源码级修复方案》§2.0 新增的两个工具方法。
"""

from __future__ import annotations

from typing import List

import pytest

from agent.generators.operator_param_combine.generate_combination_input.combination_constraint_generate import (
    CombinationConstraintGenerate,
)

# 复用普查报告中的真实「不可表达」形态：带具体下标的维索引在 PICT 中无法表达。
DIM_INDEX_CLAUSE = "x1.shape[1] == x2.shape[0]"
DTYPE_CLAUSE = "x1.dtype == x2.dtype"
RANK_CLAUSE = "x1.rank == x2.rank"


class TestSplitTopLevel:
    """
    覆盖 _split_top_level 的顶层切分语义。
    """

    def test_no_operator_returns_single_element(self) -> None:
        """
        表达式不含目标顶层运算符时，返回仅含原文的单元素列表。
        """
        assert CombinationConstraintGenerate._split_top_level(DTYPE_CLAUSE, "and") == [DTYPE_CLAUSE]

    def test_basic_and_split1(self) -> None:
        """
        基本 and 切分：分隔符被整体吞掉，片段不含两侧空格。
        """
        expr = f"{DTYPE_CLAUSE} and {RANK_CLAUSE}"
        assert CombinationConstraintGenerate._split_top_level(expr, "and") == [DTYPE_CLAUSE, RANK_CLAUSE]

    def test_basic_and_split2(self) -> None:
        """
        基本 and 切分：分隔符被整体吞掉，片段不含两侧空格。
        """
        expr = f"{DTYPE_CLAUSE} and {DIM_INDEX_CLAUSE}"
        assert CombinationConstraintGenerate._split_top_level(expr, "and") == [DTYPE_CLAUSE, DIM_INDEX_CLAUSE]

    def test_multiple_and_split(self) -> None:
        """
        连续多个 and 应切成三段。
        """
        assert CombinationConstraintGenerate._split_top_level("a and b and c", "and") == ["a", "b", "c"]

    def test_basic_or_split(self) -> None:
        """
        基本 or 切分。
        """
        assert CombinationConstraintGenerate._split_top_level("a or b", "or") == ["a", "b"]

    def test_nested_operator_not_split(self) -> None:
        """
        括号内的运算符不得被切分：以 and 切 ``(a and b) or c`` 应只有一段。
        """
        assert CombinationConstraintGenerate._split_top_level("(a and b) or c", "and") == ["(a and b) or c"]

    def test_top_level_or_split_with_nested_and(self) -> None:
        """
        括号内的 and 不影响顶层 or 的切分。
        """
        assert CombinationConstraintGenerate._split_top_level("(a and b) or c", "or") == ["(a and b)", "c"]

    def test_deep_nesting(self) -> None:
        """
        多层嵌套：只有深度 0 处的 or 参与切分。
        """
        expr = "((a or b) and c) or d"
        assert CombinationConstraintGenerate._split_top_level(expr, "or") == ["((a or b) and c)", "d"]

    def test_case_insensitive(self) -> None:
        """
        运算符大小写不敏感。
        """
        assert CombinationConstraintGenerate._split_top_level("A OR B", "or") == ["A", "B"]
        assert CombinationConstraintGenerate._split_top_level("a And b", "and") == ["a", "b"]

    def test_no_false_split_on_substring(self) -> None:
        """
        标识符内的 and 不是顶层运算符（token 带前后空格），不得误切。
        """
        expr = "x1.brand and x2.brand"
        assert CombinationConstraintGenerate._split_top_level(expr, "and") == ["x1.brand", "x2.brand"]

    def test_empty_string(self) -> None:
        """
        空串返回 [""]，由调用方经 atom_valid("") 判定。
        """
        assert CombinationConstraintGenerate._split_top_level("", "and") == [""]

    def test_extra_spaces_retained_and_stripped_by_caller(self) -> None:
        """
        非规范空格（双空格）时片段保留多余空格，依赖调用方 strip 归一。
        """
        parts = CombinationConstraintGenerate._split_top_level("a  and  b", "and")
        assert [p.strip() for p in parts] == ["a", "b"]
        assert parts != ["a", "b"]


def _reject_dim_index(atom: str) -> bool:
    """
    测试用原子可表达性判定：带具体维索引的 shape 子句视为不可表达。

    :param atom: 待判定的原子子句（已 strip）
    :return: 可表达返回 True，否则 False
    """
    return "shape[" not in atom


def _accept_all(atom: str) -> bool:
    """
    测试用原子可表达性判定：一律可表达。

    :param atom: 待判定的原子子句
    :return: 恒返回 True
    """
    del atom
    return True


class TestPruneInvalidSubclauses:
    """
    覆盖 _prune_invalid_subclauses 的降级语义与「只放宽不收紧」性质。
    """

    def test_single_atom_valid_passthrough(self) -> None:
        """
        单原子且可表达：原样返回，不做任何包裹。
        """
        assert CombinationConstraintGenerate._prune_invalid_subclauses(DTYPE_CLAUSE, _accept_all) == DTYPE_CLAUSE

    def test_single_atom_invalid_returns_none(self) -> None:
        """
        单原子且不可表达：返回 None。
        """
        assert CombinationConstraintGenerate._prune_invalid_subclauses(DIM_INDEX_CLAUSE, _reject_dim_index) is None

    def test_and_drops_invalid_and_keeps_rest(self) -> None:
        """
        顶层 and：摘除不可表达合取项，保留项各自补单层括号。
        """
        expr = f"{DIM_INDEX_CLAUSE} and {DTYPE_CLAUSE}"
        assert CombinationConstraintGenerate._prune_invalid_subclauses(expr, _reject_dim_index) == f"({DTYPE_CLAUSE})"

    def test_and_all_invalid_returns_none(self) -> None:
        """
        顶层 and：全部合取项不可表达时返回 None。
        """
        expr = f"{DIM_INDEX_CLAUSE} and x1.shape[2] == x2.shape[1]"
        assert CombinationConstraintGenerate._prune_invalid_subclauses(expr, _reject_dim_index) is None

    def test_and_middle_invalid_only_widens(self) -> None:
        """
        三合取项中间项失效：保留两端，结果严格弱于原表达式（单调放宽）。
        """
        expr = "a and b and c"
        result = CombinationConstraintGenerate._prune_invalid_subclauses(expr, lambda atom: atom != "b")
        assert result == "(a) and (c)"

    def test_no_double_parens_regression(self) -> None:
        """
        回归：片段本身已带括号时不得再补一层，避免 ((...)) 双层括号。
        """
        expr = "(a) and (b and c)"
        result = CombinationConstraintGenerate._prune_invalid_subclauses(expr, _accept_all)
        assert result is not None
        assert "((" not in result
        assert result == "(a) and (b and c)"

    def test_or_all_valid_returns_original(self) -> None:
        """
        顶层 or：所有析取项可表达时原样返回。
        """
        expr = f"{DTYPE_CLAUSE} or {RANK_CLAUSE}"
        assert CombinationConstraintGenerate._prune_invalid_subclauses(expr, _accept_all) == expr

    def test_or_any_invalid_returns_none(self) -> None:
        """
        顶层 or：某个析取项为不可表达的单原子（无法降级）时仍返回 None，绝不摘除。
        """
        expr = f"{DIM_INDEX_CLAUSE} or {DTYPE_CLAUSE}"
        assert CombinationConstraintGenerate._prune_invalid_subclauses(expr, _reject_dim_index) is None

    def test_nested_or_inside_and_preserved_intact(self) -> None:
        """
        and 的成员内含嵌套 or 时，该成员要么整体保留（含括号）要么整体摘除，不被拆散。
        """
        expr = f"{DIM_INDEX_CLAUSE} and ({DTYPE_CLAUSE} or {RANK_CLAUSE})"
        result = CombinationConstraintGenerate._prune_invalid_subclauses(expr, _reject_dim_index)
        assert result == f"({DTYPE_CLAUSE} or {RANK_CLAUSE})"

    def test_atom_valid_receives_stripped_atoms(self) -> None:
        """
        注入的 atom_valid 收到的应是 strip 后片段，不含切分残留空格。
        """
        seen: List[str] = []

        def record(atom: str) -> bool:
            """
            记录收到的原子子句并一律判为可表达。

            :param atom: 待判定原子子句
            :return: 恒返回 True
            """
            seen.append(atom)
            return True

        expr = f"{DTYPE_CLAUSE} and {RANK_CLAUSE}"
        CombinationConstraintGenerate._prune_invalid_subclauses(expr, record)
        assert seen == [DTYPE_CLAUSE, RANK_CLAUSE]

    def test_or_widens_degradable_disjunct(self) -> None:
        """
        顶层 or：析取项为 and 结构、摘除其不可表达合取项后可降级时，只降级该析取项、
        其余析取项保留，结果以 or 重连。
        示例：x1.shape[1] == x2.shape[0] and x1.dtype == x2.dtype or x1.rank == x2.rank
              -> (x1.dtype == x2.dtype) or (x1.rank == x2.rank)
        """
        expr = f"{DIM_INDEX_CLAUSE} and {DTYPE_CLAUSE} or {RANK_CLAUSE}"
        result = CombinationConstraintGenerate._prune_invalid_subclauses(expr, _reject_dim_index)
        assert result == f"({DTYPE_CLAUSE}) or ({RANK_CLAUSE})"

    def test_or_all_disjuncts_undegradable_returns_none(self) -> None:
        """
        顶层 or：所有析取项都是不可表达的单原子（均无法降级）时返回 None。
        示例：x1.shape[1] == x2.shape[0] or x1.shape[2] == x2.shape[1] -> None
        """
        expr = f"{DIM_INDEX_CLAUSE} or x1.shape[2] == x2.shape[1]"
        assert CombinationConstraintGenerate._prune_invalid_subclauses(expr, _reject_dim_index) is None

    def test_or_widened_is_strictly_weaker(self) -> None:
        """
        放宽性质：降级后的析取项不含被摘除的合取项，即原析取项的弱化。
        示例：DIM_INDEX_CLAUSE and DTYPE_CLAUSE and RANK_CLAUSE or DTYPE_CLAUSE
              -> (DTYPE_CLAUSE) and (RANK_CLAUSE) or (DTYPE_CLAUSE)
        """
        expr = f"{DIM_INDEX_CLAUSE} and {DTYPE_CLAUSE} and {RANK_CLAUSE} or {DTYPE_CLAUSE}"
        result = CombinationConstraintGenerate._prune_invalid_subclauses(expr, _reject_dim_index)
        assert result is not None
        assert DIM_INDEX_CLAUSE not in result
        assert result == f"({DTYPE_CLAUSE}) and ({RANK_CLAUSE}) or ({DTYPE_CLAUSE})"

    def test_or_parenthesized_disjunct_degraded_by_shell_strip(self) -> None:
        """
        析取项带外层括号时（M-13）：括号块先剥壳、内部 and 逐项判定，摘除不可表达合取项后
        保留降级串、与其余析取项以 or 重连；降级为纯放宽（(A and B) => B，(B) or C 超集于原式）。
        示例：(x1.shape[1] == x2.shape[0] and x1.dtype == x2.dtype) or x1.rank == x2.rank
              -> (x1.dtype == x2.dtype) or (x1.rank == x2.rank)
        """
        expr = f"({DIM_INDEX_CLAUSE} and {DTYPE_CLAUSE}) or {RANK_CLAUSE}"
        result = CombinationConstraintGenerate._prune_invalid_subclauses(expr, _reject_dim_index)
        assert result == f"({DTYPE_CLAUSE}) or ({RANK_CLAUSE})"

    def test_or_parenthesized_disjunct_all_invalid_strips_to_none(self) -> None:
        """
        括号析取项剥壳后内部无可表达原子时（M-13 边界）：整条返回 None（保守不收紧）。
        示例：(x1.shape[1] == x2.shape[0] and x1.shape[2] == x2.shape[1]) or x1.rank == x2.rank -> None
        """
        expr = f"({DIM_INDEX_CLAUSE} and x1.shape[2] == x2.shape[1]) or {RANK_CLAUSE}"
        assert CombinationConstraintGenerate._prune_invalid_subclauses(expr, _reject_dim_index) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
