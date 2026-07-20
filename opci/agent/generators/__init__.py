"""Test case generator — public API.

This package is the single source of truth for the case generation logic.

The previous mock implementation (case_builder, dtype_picker, shape_sampler, …)
has been replaced by the formal generation pipeline ported from
``operator_case_generator``:

  ``json_constraints`` (raw dict)  →  ``single_operator_handle``  →  ``list[CaseConfig]``

Migration overview
------------------

* The script files from ``operator_case_generator/operator_case_generator/scripts``
  were moved under this package and re-rooted at ``opci.agent.generators.*``.
* ``operator_handle_main.single_operator_handle`` now accepts either a constraint
  JSON file path or an in-memory constraint dict.
* ``facade.TestCaseGenerator`` is the single entry point for node / route / MCP
  callers; the public API stays stable.
* 用例生成主链路直接消费从 MCP / DB 取出的原始 ``json_constraints`` dict，
  不再做 ``GeneratorContext`` 中间层转换；返回值是 ``single_operator_handle``
  的原始输出 ``list[CaseConfig]``。

Import strategy
---------------

Only lightweight models from ``common_model_definition`` are eagerly imported.
Heavy modules (``facade``, ``operator_handle_main``) that pull in Z3 solver,
numpy, etc. are lazily imported via ``__getattr__`` to avoid blocking MCP
stdio tool calls that only need ``OperatorRule`` for validation.
"""

from __future__ import annotations

# Eager: lightweight (pydantic + enum + typing only)
from opci.agent.generators.common_model_definition import (
    InterConstraintsRuleType,
    InterParamConstraint,
    OperatorRule,
    ParamAttributes,
    ValueWithSrcText,
)

# Lazy: heavy modules (Z3, numpy, etc.) — only loaded when accessed
_LAZY_FACADE = ("DEFAULT_COUNT", "DEFAULT_SEED", "TestCaseGenerator")
_LAZY_HANDLE = ("single_operator_handle", "batch_operator_handel")


def __getattr__(name: str) -> object:
    if name in _LAZY_FACADE:
        from opci.agent.generators.facade import DEFAULT_COUNT, DEFAULT_SEED, TestCaseGenerator
        globals()["DEFAULT_COUNT"] = DEFAULT_COUNT
        globals()["DEFAULT_SEED"] = DEFAULT_SEED
        globals()["TestCaseGenerator"] = TestCaseGenerator
        return globals()[name]
    if name in _LAZY_HANDLE:
        from opci.agent.generators.operator_handle_main import single_operator_handle, batch_operator_handel
        globals()["single_operator_handle"] = single_operator_handle
        globals()["batch_operator_handel"] = batch_operator_handel
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Public facade
    "DEFAULT_COUNT",
    "DEFAULT_SEED",
    "TestCaseGenerator",
    # Formal generation entry point
    "single_operator_handle",
    "batch_operator_handel",
    # Common constraint models
    "InterConstraintsRuleType",
    "InterParamConstraint",
    "OperatorRule",
    "ParamAttributes",
    "ValueWithSrcText",
]
