"""SSOT 结构断言：锁定白名单单一真源，防止再次散弹式修改。"""
from __future__ import annotations

from agent.generators.operator_param_combine.combination_result_generator.constraint.function_registry import (
    _DEFAULT_FUNCTIONS,
)
from agent.generators.operator_param_combine.combination_result_generator.constraint.whitelist import (
    ALLOWED_CALLABLES,
    ALLOWED_CONSTANT_NAMES,
    ALLOWED_NAMES,
)
from agent.generators.operator_param_combine.generate_combination_input.combination_constraint_generate import (
    CombinationConstraintGenerate,
)


def test_a3_1_constant_names_are_exactly_three():
    """A3.1 常量名集合不得私自扩容（X-3 漂移）。"""
    assert set(ALLOWED_NAMES) - set(ALLOWED_CALLABLES) == set(ALLOWED_CONSTANT_NAMES)
    assert set(ALLOWED_CONSTANT_NAMES) == {"True", "False", "None"}


def test_a3_2_default_functions_synced_with_ssot():
    """A3.2 准入与执行两份白名单必须同源（X-2）。"""
    assert set(ALLOWED_CALLABLES) == set(_DEFAULT_FUNCTIONS)


def test_a3_3_allowed_builtins_removed():
    """A3.3 死代码 `_ALLOWED_BUILTINS` 必须已删除（X-1）。"""
    assert not hasattr(CombinationConstraintGenerate, "_ALLOWED_BUILTINS")