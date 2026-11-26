# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
功能：按 dim_axes 场景把轴值解析为每个张量的 shape。

轴值与布局解耦：先按用例序号算出所有轴值，再按选中布局的"张量 -> 轴序列"
组装 shape；不读取 shape_definitions.json。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.operator_param_models.dim_axes import DimAxesError, SceneSpec, _eval_expr

logger = LazyLogger()


class AxisShapeResolver:
    def __init__(self, scene: SceneSpec, seed: int = 42):
        self.scene = scene
        self.seed = int(seed)
        self._case_index = 0
        self._layout: Optional[str] = None
        self._axis_values: Dict[str, int] = {}
        self._layout_by_case: Dict[int, str] = {}

    @property
    def layout(self) -> str:
        if self._layout is None:
            raise DimAxesError(f"场景 {self.scene.name} 尚未 begin_case")
        return self._layout

    @property
    def axis_values(self) -> Dict[str, int]:
        return dict(self._axis_values)

    def managed_tensor(self, param_name: str) -> bool:
        key = self.scene.layout_tensor_key(param_name)
        return any(key in orders for orders in self.scene.layouts.values())

    def begin_case(self, case_index: int, row_layout: Optional[str] = None) -> None:
        self._case_index = int(case_index)
        self._layout = self.scene.resolve_layout(self._case_index, row_layout)
        self._layout_by_case[self._case_index] = self._layout
        used = self.scene.used_axes(self._layout)
        values: Dict[str, int] = {}
        for name in self.scene.topological_axes():
            if name not in used:
                continue
            axis = self.scene.axes[name]
            if axis.is_expr:
                values[name] = int(_eval_expr(axis.expr_ast, values, name))
                continue
            index = sum(
                1
                for earlier_index, earlier_layout in self._layout_by_case.items()
                if earlier_index < self._case_index and name in self.scene.used_axes(earlier_layout)
            )
            values[name] = axis.value_at(index, self.seed, self.scene.axis_pick)
            logger.debug(
                "dim_axes axis value, case=%s layout=%s axis=%s index=%s value=%s",
                self._case_index, self._layout, name, index, values[name],
            )
        values = self.scene.enforce_element_limit(self._layout, values)
        self._axis_values = values

    def resolve(self, param_name: str) -> Tuple[List[int], Dict[str, int], str]:
        if not self.managed_tensor(param_name):
            raise DimAxesError(
                f"场景 {self.scene.name} 未在布局中定义张量 {param_name!r}；"
                "命中 dim_axes 后不允许回落旧 shape 逻辑"
            )
        shape, used = self.scene.build_tensor_shape(
            self.layout, param_name, self._axis_values
        )
        return shape, used, self.layout

    def layout_for_case(self) -> str:
        return self.layout
