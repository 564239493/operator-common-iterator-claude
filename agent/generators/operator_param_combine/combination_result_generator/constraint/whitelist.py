"""
Constraint Whitelist — 单一真源（Single Source of Truth）。

Module:
    M2 Constraint AST Adapter

Responsibility:
    统一维护「允许哪些内建函数被约束表达式调用」这一知识。
    Gate1 准入（`_ALLOWED_NAMES`）、执行期注册（`_DEFAULT_FUNCTIONS`）、
    脚本参数提取（`_expr_param_names`）一律由此派生，不再各自手工维护。
"""
from __future__ import annotations

import math
from typing import Callable

ALLOWED_CALLABLES: dict[str, Callable] = {
    "len": len,
    "abs": abs,
    "any": any,
    "all": all,
    "min": min,
    "max": max,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "sum": sum,
    "prod": math.prod,
}

ALLOWED_CONSTANT_NAMES: frozenset[str] = frozenset({"True", "False", "None"})

ALLOWED_NAMES: frozenset[str] = frozenset(ALLOWED_CALLABLES) | ALLOWED_CONSTANT_NAMES