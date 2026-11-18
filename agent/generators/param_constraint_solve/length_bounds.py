# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
版权信息：华为技术有限公司，版本所有(C) 2026-2026
功能：列表参数长度上限的统一推导，生成管线与约束取证脚本共用同一份实现。

- ``parse_array_length_upper``：解析参数卡 ``array_length``（枚举取最大，区间取最大上端）；
- ``resolve_length_upper``：按优先级取上界（① 文档 ``array_length``，② 约束文本 ``len(p) <= N`` 兜底）；
- ``resolve_unroll_window``：把上界折成静态展开窗口；超过 ``MAX_STATIC_UNROLL`` 时仅对 ``sum()`` 窗口参数截断，其余参数不登记。

调用方须把 ``len(p) <= 窗口`` 硬断言进求解器——展开式每项带 guard，断言后窗口内才是精确的。
"""
from agent.generators.param_constraint_solve.z3_expression_solver_utils import (
    MAX_STATIC_UNROLL,
)

__all__ = [
    "MAX_STATIC_UNROLL",
    "len_ref_names",
    "len_upper_from_exprs",
    "parse_array_length_upper",
    "resolve_length_upper",
    "resolve_unroll_window",
    "sum_window_len_refs",
]


def _is_static_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_array_length_upper(raw):
    """解析参数卡 ``array_length`` 的长度上限；推不出返回 None。

    接受 ``ValueWithSrcText`` / ``{"value": ...}`` 字典 / 原始值三种形态；
    ``N/A``、空列表与非数值字符串视为未声明。``[N, ...]`` 枚举取最大，``[[lo, hi], ...]`` 区间取最大上端（多档声明不区分平台，统一取最大上端）。
    """
    if isinstance(raw, dict):
        raw = raw.get("value")
    elif not isinstance(raw, (str, int, float, list, tuple)) and hasattr(raw, "value"):
        # ValueWithSrcText 等带 value 字段的模型对象
        raw = raw.value
    if isinstance(raw, str):
        return None

    values = []
    if _is_static_int(raw):
        values = [raw]
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if _is_static_int(item):
                values.append(item)
            elif (isinstance(item, (list, tuple)) and len(item) == 2
                  and all(_is_static_int(v) for v in item)):
                values.append(item[1])
    values = [value for value in values if value >= 1]
    return max(values) if values else None


def len_ref_names(exprs) -> set:
    """约束里用 ``len(p)`` / ``p.length`` 引用过长度的参数名。"""
    from agent.generators.param_constraint_solve.param_constraint_utils import (
        _len_ref_names,
    )
    return _len_ref_names(exprs)


def sum_window_len_refs(exprs) -> set:
    """只取作为 ``sum(... for i in range(...))`` 展开窗口的参数名。

    窗口由 ``range()`` 参数的上界决定；超过展开阈值的截断只对这些参数生效，
    其他 ``len(p)`` 引用（如 ``len(p) >= 200``）保持原样。判结构不判语义：
    ``sum`` 外的 ``range(len(p))`` 量词不算窗口。
    """
    from agent.generators.param_constraint_solve.param_constraint_utils import (
        _sum_range_len_refs,
    )
    return _sum_range_len_refs(exprs)


def len_upper_from_exprs(exprs, param_name):
    """约束文本（合取式）里 ``len(p) <= N`` 推出的上界，多个候选取最小；推不出返回 None。"""
    from agent.generators.param_constraint_solve.param_constraint_utils import (
        _len_upper_from_exprs,
    )
    return _len_upper_from_exprs(exprs, param_name)


def resolve_length_upper(param_name, array_length_raw, exprs):
    """按优先级推列表长度上界：① 文档 ``array_length``（推到即用，不回退）；
    ② 约束文本 ``len(p) <= N`` 兜底。推不出返回 None。
    """
    bound = parse_array_length_upper(array_length_raw)
    if bound is not None:
        return bound
    return len_upper_from_exprs(exprs, param_name)


def resolve_unroll_window(param_name, array_length_raw, exprs, allow_clamp=False):
    """把长度上界折成静态展开窗口。

    :param allow_clamp: 参数是否作为 ``sum()`` 窗口（见 ``sum_window_len_refs``）。
        仅窗口参数在界超过 ``MAX_STATIC_UNROLL`` 时截断到阈值，其余参数不登记、量词保持原样。
    :return: ``(window, declared)``；推不出为 ``(None, None)``，未截断时
        ``declared`` 为 None，截断时为截断前上界（供日志）。截断优先取约束文本里更小的上界，都没有才落到阈值。
    """
    bound = resolve_length_upper(param_name, array_length_raw, exprs)
    if not _is_static_int(bound) or bound < 1:
        return None, None
    if bound <= MAX_STATIC_UNROLL:
        return bound, None
    if not allow_clamp:
        return None, None
    text_bound = len_upper_from_exprs(exprs, param_name)
    if text_bound is not None and 1 <= text_bound < MAX_STATIC_UNROLL:
        return text_bound, bound
    return MAX_STATIC_UNROLL, bound
