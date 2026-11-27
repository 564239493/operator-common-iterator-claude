# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
功能：维度轴取值配置（dim_axes）的加载、校验与轴值排班。

配置只建模轴：每个场景一套 axes（典型值/区间/派生表达式），每个布局声明
"张量 -> 轴序列"。生成阶段按轴查表取值、按布局组装 shape，不读取
shape_definitions.json。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.data_definition.constants import ParamModelConfig

logger = LazyLogger()

DEFAULT_DIM_AXES_PATH = Path(__file__).resolve().parents[1] / "configs" / "dim_axes.json"

_AXIS_MODEL_KEYS = {"typical", "ranges", "expr"}
_SEGMENT_KEYS = {"gt", "ge", "lt", "le"}
_ALLOWED_EXPR_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Name,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.FloorDiv,
    ast.Mod,
    ast.USub,
    ast.UAdd,
    ast.Load,
)


class DimAxesError(RuntimeError):
    """维度轴配置错误或生成阶段无法满足配置。"""


def _stable_int(*parts: Any) -> int:
    text = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _rng(*parts: Any) -> random.Random:
    return random.Random(_stable_int(*parts))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_token_list(raw: Any, where: str, errors: List[str]) -> List[Any]:
    if not isinstance(raw, list) or not raw:
        errors.append(f"{where}: 轴序列必须是非空列表")
        return []
    tokens: List[Any] = []
    for index, token in enumerate(raw):
        if isinstance(token, str):
            if not token.strip():
                errors.append(f"{where}[{index}]: 轴名不能为空")
            tokens.append(token)
        elif _is_int(token):
            if token < 1:
                errors.append(f"{where}[{index}]: 常量维必须 >= 1，当前 {token}")
            tokens.append(token)
        else:
            errors.append(f"{where}[{index}]: token 只能是轴名或整数，当前 {token!r}")
    return tokens


@dataclass
class Segment:
    lo: int
    hi: int

    def as_text(self) -> str:
        return f"[{self.lo},{self.hi}]"


@dataclass
class AxisSpec:
    name: str
    typical: List[int] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    expr: Optional[str] = None
    expr_ast: Optional[ast.AST] = None
    depends_on: List[str] = field(default_factory=list)

    @property
    def is_expr(self) -> bool:
        return self.expr is not None

    def coverage_schedule(self) -> List[int]:
        """先取完典型值，再为每个区间保留一个代表值（lo）。"""
        values: List[int] = list(self.typical)
        for segment in self.segments:
            values.append(segment.lo)
        return list(dict.fromkeys(values))

    def value_at(self, index: int, seed: int, axis_pick: str) -> int:
        if self.is_expr:
            raise DimAxesError(f"派生轴 {self.name} 不应走采样")
        schedule = self.coverage_schedule()
        if index < len(schedule):
            return schedule[index]
        if self.segments:
            rng = _rng(seed, axis_pick, self.name, index)
            if axis_pick == "scheduled":
                segment = self.segments[index % len(self.segments)]
            else:
                segment = rng.choice(self.segments)
            return rng.randint(segment.lo, segment.hi)
        if not self.typical:
            raise DimAxesError(f"轴 {self.name} 既没有 typical/ranges 也没有 expr")
        return self.typical[index % len(self.typical)]


@dataclass
class SceneSpec:
    name: str
    layout_param: Optional[str]
    axis_pick: str
    axes: Dict[str, AxisSpec]
    layouts: Dict[str, Dict[str, List[Any]]]
    layout_pick: str = "round_robin"
    errors: List[str] = field(default_factory=list)

    @property
    def layout_names(self) -> List[str]:
        return list(self.layouts.keys())

    def tensor_names(self) -> List[str]:
        names: List[str] = []
        for orders in self.layouts.values():
            for name in orders:
                if name not in names:
                    names.append(name)
        return names

    def rank_options(self, param_name: str) -> List[int]:
        ranks = {
            len(orders[param_name])
            for orders in self.layouts.values()
            if param_name in orders
        }
        return sorted(ranks)

    def required_cases(self) -> int:
        length = len(self.layouts)
        for axis in self.axes.values():
            if axis.is_expr:
                continue
            length = max(length, len(axis.coverage_schedule()))
        return max(length, 1)

    def used_axes(self, layout: str) -> set[str]:
        cached = getattr(self, "_used_axes_cache", None)
        if cached is None:
            cached = {}
            self._used_axes_cache = cached
        if layout in cached:
            return cached[layout]
        used: set[str] = set()
        orders = self.layouts.get(layout, {})
        stack: List[str] = []
        for tokens in orders.values():
            for token in tokens:
                if isinstance(token, str):
                    stack.append(token)
        while stack:
            name = stack.pop()
            if name in used or name not in self.axes:
                continue
            used.add(name)
            stack.extend(self.axes[name].depends_on)
        cached[layout] = used
        return used

    def topological_axes(self) -> List[str]:
        return self._topological_axes()

    def _topological_axes(self) -> List[str]:
        ordered: List[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise DimAxesError(f"派生轴表达式存在循环依赖: {name}")
            visiting.add(name)
            for dep in self.axes[name].depends_on:
                if dep in self.axes:
                    visit(dep)
            visiting.discard(name)
            visited.add(name)
            ordered.append(name)

        for name in self.axes:
            visit(name)
        return ordered

    def resolve_axis_values(self, case_index: int, seed: int) -> Dict[str, int]:
        values: Dict[str, int] = {}
        for name in self._topological_axes():
            axis = self.axes[name]
            if axis.is_expr:
                values[name] = int(_eval_expr(axis.expr_ast, values, name))
            else:
                values[name] = axis.value_at(case_index, seed, self.axis_pick)
        return values

    def resolve_layout(self, case_index: int, row_layout: Optional[str]) -> str:
        # 关键：当组合/约束已经给出 layout_param 的取值时，必须优先采用它，
        # 不能再按 case_index 轮转覆盖，否则会出现“inputLayout=BSH 但张量按 TND 生成”
        # 这类自相矛盾用例。round_robin 仅在没有可用取值时兜底。
        if row_layout not in (None, ""):
            if row_layout not in self.layouts:
                raise DimAxesError(
                    f"场景 {self.name} 的 layout_param={self.layout_param} 取到未配置布局 "
                    f"{row_layout!r}，可用布局: {self.layout_names}"
                )
            return row_layout
        if self.layout_pick == "round_robin":
            return self.layout_names[case_index % len(self.layout_names)]
        if self.layout_param:
            raise DimAxesError(
                f"场景 {self.name} 需要 layout_param={self.layout_param} 的取值，但组合结果中没有"
            )
        return self.layout_names[case_index % len(self.layout_names)]

    def layout_tensor_key(self, param_name: str) -> str:
        """Map an ACLNN param name to the layout tensor key used in this scene.

        dim_axes layouts use base tensor names (``blockTable``, ``attenMask``),
        while ACLNN optional params carry an ``Optional`` suffix
        (``blockTableOptional``). Match the base name so optional tensors present in
        a case can also be planned, instead of failing "layout does not cover".
        """
        for orders in self.layouts.values():
            if param_name in orders:
                return param_name
        for suffix in ("Optional", "optional"):
            if param_name.endswith(suffix):
                base = param_name[: -len(suffix)]
                for orders in self.layouts.values():
                    if base in orders:
                        return base
        return param_name

    def build_shape_from_tokens(self, tokens: List[Any], axis_values: Dict[str, int]):
        """按给定轴 token 序列组装 shape（字符串=轴名、整数=常量维）。

        与 ``build_tensor_shape`` 共用同一组装逻辑，供"未在 layouts 声明、
        但由文档 shape 识别出轴序"的张量（如 learnableSink/pseShift）使用。
        """
        shape: List[int] = []
        used: Dict[str, int] = {}

        def record(name: str) -> None:
            if name in used or name not in axis_values:
                return
            used[name] = axis_values[name]
            for dep in self.axes[name].depends_on:
                record(dep)

        for token in tokens:
            if _is_int(token):
                shape.append(int(token))
            else:
                if token not in axis_values:
                    raise DimAxesError(f"轴序引用未解析轴 {token!r}（场景 {self.name}）")
                shape.append(axis_values[token])
                record(token)
        return shape, used

    def build_tensor_shape(self, layout: str, param_name: str, axis_values: Dict[str, int]):
        orders = self.layouts[layout]
        key = self.layout_tensor_key(param_name)
        if key not in orders:
            raise DimAxesError(
                f"布局 {layout} 未定义张量 {param_name!r} 的轴序列（场景 {self.name}）"
            )
        return self.build_shape_from_tokens(orders[key], axis_values)

    def resolve_axes_for_tokens(self, tokens: List[Any], case_index: int,
                                seed: int) -> Dict[str, int]:
        """为任意轴 token 序列按当前用例解析轴值（含派生轴依赖）。

        与 ``resolve_axis_values`` 不同：这里不限于当前布局使用的轴，而是按传入
        token 序列把需要的场景轴（含 expr 派生轴）都解析出来，供未在 layouts
        中声明的张量复用 dim_axes 场景轴值。
        """
        base_axes: set[str] = set()
        for token in tokens:
            if isinstance(token, str) and token in self.axes:
                self._base_axes(token, base_axes)
        values: Dict[str, int] = {}
        for name in self._topological_axes():
            spec = self.axes[name]
            if spec.is_expr:
                if all(dep in values for dep in spec.depends_on):
                    values[name] = int(_eval_expr(spec.expr_ast, values, name))
            elif name in base_axes:
                values[name] = spec.value_at(case_index, seed, self.axis_pick)
        return values

    def _axis_candidates(self, name: str) -> List[int]:
        spec = self.axes[name]
        values = set(spec.typical)
        for segment in spec.segments:
            values.add(segment.lo)
            values.add(segment.hi)
        return sorted(values, reverse=True)

    def _base_axes(self, name: str, sink: set[str]) -> None:
        spec = self.axes[name]
        if not spec.is_expr:
            sink.add(name)
            return
        for dep in spec.depends_on:
            self._base_axes(dep, sink)

    def _refresh_derived(self, values: Dict[str, int]) -> None:
        for name in self._topological_axes():
            spec = self.axes[name]
            if spec.is_expr:
                values[name] = int(_eval_expr(spec.expr_ast, values, name))

    def enforce_element_limit(self, layout: str, values: Dict[str, int],
                              limit: int = ParamModelConfig.TENSOR_TENSOR_ELEMENT_LIMIT
                              ) -> Dict[str, int]:
        """按需降低轴值，保证每个张量元素总数 < limit。

        只调整基础轴（派生轴由依赖重算），并在日志中记录降级，保持确定性。
        """
        for _ in range(64):
            worst_tensor = None
            worst_product = 0
            for param_name, tokens in self.layouts[layout].items():
                product = 1
                for token in tokens:
                    product *= int(token) if _is_int(token) else int(values[token])
                if product > worst_product:
                    worst_product = product
                    worst_tensor = param_name
            if worst_product < limit:
                return values

            base_axes: set[str] = set()
            for token in self.layouts[layout][worst_tensor]:
                if isinstance(token, str):
                    self._base_axes(token, base_axes)
            def reduce_priority(name: str):
                # 覆盖排班越长的轴越贵，最后压；同组先压非典型值，再压大值。
                spec = self.axes[name]
                schedule_len = 0 if spec.is_expr else len(spec.coverage_schedule())
                in_typical = values[name] in set(spec.typical)
                return (schedule_len, 1 if in_typical else 0, -values[name])

            smaller_by_axis = {
                name: [value for value in self._axis_candidates(name) if value < values[name]]
                for name in base_axes
                if values.get(name, 1) > 1
            }
            reducible = sorted(
                (name for name, smaller in smaller_by_axis.items() if smaller),
                key=reduce_priority,
            )
            if not reducible:
                break
            axis_name = reducible[0]
            current = values[axis_name]
            values[axis_name] = smaller_by_axis[axis_name][0]
            self._refresh_derived(values)
            logger.warning(
                "dim_axes element limit: tensor=%s product=%s >= %s, reduce axis %s %s -> %s",
                worst_tensor, worst_product, limit, axis_name, current, values[axis_name],
            )
        worst_product = 0
        for tokens in self.layouts[layout].values():
            product = 1
            for token in tokens:
                product *= int(token) if _is_int(token) else int(values[token])
            worst_product = max(worst_product, product)
        if worst_product >= limit:
            raise DimAxesError(
                f"场景 {self.name} 布局 {layout} 无法在元素上限 {limit} 内生成 shape"
                f"（当前最大 {worst_product}）"
            )
        return values


@dataclass
class Profile:
    id: str
    operators: List[str]
    exclude: List[str]
    scenes: Dict[str, SceneSpec]

    def matches(self, operator_name: str) -> bool:
        import fnmatch

        if not any(fnmatch.fnmatch(operator_name, pattern) for pattern in self.operators):
            return False
        if any(fnmatch.fnmatch(operator_name, pattern) for pattern in self.exclude):
            return False
        return True


@dataclass
class DimAxesConfig:
    version: int
    profiles: List[Profile]
    path: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    def resolve_profile(self, operator_name: str) -> Optional[Profile]:
        for profile in self.profiles:
            if profile.matches(operator_name):
                return profile
        return None

    def list_scenes(self, operator_name: str) -> List[str]:
        profile = self.resolve_profile(operator_name)
        return list(profile.scenes.keys()) if profile else []


def _normalize_segment(raw: Any, where: str, errors: List[str]) -> Optional[Segment]:
    if isinstance(raw, (list, tuple)):
        if len(raw) != 2 or not _is_int(raw[0]) or not _is_int(raw[1]):
            errors.append(f"{where}: 区间简写必须是 [lo, hi] 两个整数")
            return None
        lo, hi, lo_open, hi_open = raw[0], raw[1], False, False
    elif isinstance(raw, dict):
        unknown = set(raw) - _SEGMENT_KEYS
        if unknown:
            errors.append(f"{where}: 区间对象含未知字段 {sorted(unknown)}")
            return None
        if ("ge" in raw) == ("gt" in raw):
            errors.append(f"{where}: 下界必须且只能写 ge/gt 之一")
            return None
        if ("le" in raw) == ("lt" in raw):
            errors.append(f"{where}: 上界必须且只能写 le/lt 之一")
            return None
        lo, lo_open = (raw["ge"], False) if "ge" in raw else (raw["gt"], True)
        hi, hi_open = (raw["le"], False) if "le" in raw else (raw["lt"], True)
        if not _is_int(lo) or not _is_int(hi):
            errors.append(f"{where}: 区间端点必须是整数")
            return None
    else:
        errors.append(f"{where}: 区间必须是 [lo, hi] 或 {{ge/gt, le/lt}} 对象")
        return None

    lo_i = lo + 1 if lo_open else lo
    hi_i = hi - 1 if hi_open else hi
    if lo_i > hi_i:
        errors.append(f"{where}: 整数域内区间为空 -> [{lo_i}, {hi_i}]")
        return None
    if lo_i < 1:
        errors.append(f"{where}: shape 轴不允许小于 1 -> {lo_i}")
        return None
    return Segment(lo_i, hi_i)


def _validate_expr(expr: str, axis_names: set[str], where: str,
                   errors: List[str]) -> Tuple[Optional[ast.AST], List[str]]:
    if not isinstance(expr, str) or not expr.strip():
        errors.append(f"{where}: expr 必须是非空字符串")
        return None, []
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        errors.append(f"{where}: expr 语法错误: {exc}")
        return None, []
    deps: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_EXPR_NODES):
            errors.append(f"{where}: expr 含不允许的语法节点 {type(node).__name__}")
            return None, []
        if isinstance(node, ast.Name):
            if node.id not in axis_names:
                errors.append(f"{where}: expr 引用未定义轴 {node.id!r}")
                return None, []
            deps.append(node.id)
        if isinstance(node, ast.Constant) and not _is_int(node.value):
            errors.append(f"{where}: expr 只允许整数常量，当前 {node.value!r}")
            return None, []
    return tree, list(dict.fromkeys(deps))


def _eval_expr(tree: Optional[ast.AST], values: Dict[str, int], axis_name: str) -> int:
    if tree is None:
        raise DimAxesError(f"派生轴 {axis_name} 的表达式未成功解析")
    try:
        result = _eval_node(tree.body, values)
    except ZeroDivisionError as exc:
        raise DimAxesError(f"派生轴 {axis_name} 表达式除零: {exc}") from exc
    if not _is_int(result):
        raise DimAxesError(f"派生轴 {axis_name} 表达式结果不是整数: {result!r}")
    return int(result)


def _eval_node(node: ast.AST, values: Dict[str, int]) -> int:
    if isinstance(node, ast.Constant):
        return int(node.value)
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise DimAxesError(f"表达式引用的轴 {node.id!r} 尚无取值")
        return int(values[node.id])
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, values)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, values)
        right = _eval_node(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.FloorDiv):
            if right == 0:
                raise ZeroDivisionError("floor division by zero")
            return left // right
        if isinstance(node.op, ast.Mod):
            if right == 0:
                raise ZeroDivisionError("modulo by zero")
            return left % right
    raise DimAxesError(f"不支持的表达式节点: {type(node).__name__}")


def _validate_axis_model(name: str, raw: Any, axis_names: set[str], where: str,
                         errors: List[str]) -> Optional[AxisSpec]:
    if not isinstance(raw, dict):
        errors.append(f"{where}: 轴模型必须是对象")
        return None
    unknown = set(raw) - _AXIS_MODEL_KEYS
    if unknown:
        errors.append(f"{where}: 轴模型含未知字段 {sorted(unknown)}")
        return None

    if "expr" in raw:
        if "typical" in raw or "ranges" in raw:
            errors.append(f"{where}: expr 与 typical/ranges 互斥")
            return None
        tree, deps = _validate_expr(raw["expr"], axis_names - {name}, where, errors)
        return AxisSpec(name=name, expr=raw["expr"], expr_ast=tree, depends_on=deps)

    typical_raw = raw.get("typical", [])
    if typical_raw is None:
        typical_raw = []
    if not isinstance(typical_raw, list):
        errors.append(f"{where}.typical 必须是列表")
        return None
    typical: List[int] = []
    for index, value in enumerate(typical_raw):
        if not _is_int(value) or value < 1:
            errors.append(f"{where}.typical[{index}] 必须是 >=1 的整数")
            continue
        typical.append(value)

    ranges_raw = raw.get("ranges", [])
    if ranges_raw is None:
        ranges_raw = []
    if not isinstance(ranges_raw, list):
        errors.append(f"{where}.ranges 必须是列表")
        return None
    segments: List[Segment] = []
    for index, segment_raw in enumerate(ranges_raw):
        segment = _normalize_segment(segment_raw, f"{where}.ranges[{index}]", errors)
        if segment is not None:
            segments.append(segment)
    segments.sort(key=lambda item: item.lo)
    for previous, current in zip(segments, segments[1:]):
        if current.lo <= previous.hi:
            errors.append(
                f"{where}.ranges 段重叠: {previous.as_text()} 与 {current.as_text()}"
            )

    if not typical and not segments:
        errors.append(f"{where}: 必须至少提供 typical 或 ranges")
        return None
    return AxisSpec(name=name, typical=list(dict.fromkeys(typical)), segments=segments)


def _validate_scene(scene_raw: Any, where: str, errors: List[str]) -> Optional[SceneSpec]:
    if not isinstance(scene_raw, dict):
        errors.append(f"{where}: scene 必须是对象")
        return None
    allowed = {"layout_param", "layout_pick", "axis_pick", "axes", "layouts"}
    unknown = set(scene_raw) - allowed
    if unknown:
        errors.append(f"{where}: scene 含未知字段 {sorted(unknown)}")
        return None

    layout_param = scene_raw.get("layout_param")
    if layout_param is not None and (not isinstance(layout_param, str) or not layout_param.strip()):
        errors.append(f"{where}.layout_param 必须是字符串或 null")
        layout_param = None

    layout_pick = scene_raw.get("layout_pick", "round_robin")
    if layout_pick not in ("round_robin", "row"):
        errors.append(f"{where}.layout_pick 只能是 round_robin/row，当前 {layout_pick!r}")
        layout_pick = "round_robin"

    axis_pick = scene_raw.get("axis_pick", "scheduled")
    if axis_pick not in ("scheduled", "random"):
        errors.append(f"{where}.axis_pick 只能是 scheduled/random，当前 {axis_pick!r}")
        axis_pick = "scheduled"

    axes_raw = scene_raw.get("axes")
    if not isinstance(axes_raw, dict) or not axes_raw:
        errors.append(f"{where}.axes 必须是非空对象")
        return None
    axis_names = set(axes_raw.keys())
    axes: Dict[str, AxisSpec] = {}
    for axis_name, axis_raw in axes_raw.items():
        if not isinstance(axis_name, str) or not axis_name.strip():
            errors.append(f"{where}.axes: 轴名必须是非空字符串")
            continue
        axis = _validate_axis_model(
            axis_name, axis_raw, axis_names, f"{where}.axes[{axis_name}]", errors
        )
        if axis is not None:
            axes[axis_name] = axis

    layouts_raw = scene_raw.get("layouts")
    if not isinstance(layouts_raw, dict) or not layouts_raw:
        errors.append(f"{where}.layouts 必须是非空对象")
        return None
    layouts: Dict[str, Dict[str, List[Any]]] = {}
    tensor_sets: List[Tuple[str, set[str]]] = []
    for layout_name, orders_raw in layouts_raw.items():
        if not isinstance(layout_name, str) or not layout_name.strip():
            errors.append(f"{where}.layouts: 布局名必须是非空字符串")
            continue
        if not isinstance(orders_raw, dict) or not orders_raw:
            errors.append(f"{where}.layouts[{layout_name}] 必须是非空对象")
            continue
        orders: Dict[str, List[Any]] = {}
        for tensor_name, tokens_raw in orders_raw.items():
            if not isinstance(tensor_name, str) or not tensor_name.strip():
                errors.append(f"{where}.layouts[{layout_name}]: 张量名必须是非空字符串")
                continue
            tokens = _as_token_list(
                tokens_raw, f"{where}.layouts[{layout_name}][{tensor_name}]", errors
            )
            for token in tokens:
                if isinstance(token, str) and token not in axes:
                    errors.append(
                        f"{where}.layouts[{layout_name}][{tensor_name}]: 引用未定义轴 {token!r}"
                    )
            orders[tensor_name] = tokens
        layouts[layout_name] = orders
        tensor_sets.append((layout_name, set(orders.keys())))

    if tensor_sets:
        base_layout, base_tensors = tensor_sets[0]
        for layout_name, tensors in tensor_sets[1:]:
            if tensors != base_tensors:
                missing = sorted(base_tensors - tensors)
                extra = sorted(tensors - base_tensors)
                errors.append(
                    f"{where}.layouts 张量集合不一致：{base_layout} 与 {layout_name} "
                    f"缺失={missing} 多出={extra}"
                )

    return SceneSpec(
        name=where,
        layout_param=layout_param,
        layout_pick=layout_pick,
        axis_pick=axis_pick,
        axes=axes,
        layouts=layouts,
    )


def validate_config(config: DimAxesConfig) -> List[str]:
    errors: List[str] = list(config.errors)
    if config.version != 7:
        errors.append(f"version 必须为 7，当前 {config.version!r}")
    if not config.profiles:
        errors.append("profiles 不能为空")
    seen_ids: set[str] = set()
    for profile in config.profiles:
        if not profile.id:
            errors.append("profiles: id 不能为空")
        elif profile.id in seen_ids:
            errors.append(f"profiles: id 重复 {profile.id!r}")
        else:
            seen_ids.add(profile.id)
        if not profile.operators:
            errors.append(f"profile {profile.id}: match.operators 不能为空")
        if not profile.scenes:
            errors.append(f"profile {profile.id}: scenes 不能为空")
        for scene_name, scene in profile.scenes.items():
            scene.name = scene_name
            if not scene.layout_param:
                continue
            for layout_name, orders in scene.layouts.items():
                for tensor_name, tokens in orders.items():
                    if not isinstance(tokens, list) or not tokens:
                        continue
            try:
                scene.resolve_axis_values(0, 0)
            except DimAxesError as exc:
                errors.append(f"profile {profile.id} scene {scene_name}: {exc}")
    return errors


def _load_profiles(raw_profiles: Any, errors: List[str]) -> List[Profile]:
    if not isinstance(raw_profiles, list):
        errors.append("profiles 必须是列表")
        return []
    profiles: List[Profile] = []
    for index, profile_raw in enumerate(raw_profiles):
        where = f"profiles[{index}]"
        if not isinstance(profile_raw, dict):
            errors.append(f"{where}: profile 必须是对象")
            continue
        unknown = set(profile_raw) - {"id", "match", "scenes"}
        if unknown:
            errors.append(f"{where}: profile 含未知字段 {sorted(unknown)}")
            continue
        profile_id = profile_raw.get("id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            errors.append(f"{where}.id 必须是非空字符串")
            profile_id = ""
        match_raw = profile_raw.get("match")
        if not isinstance(match_raw, dict):
            errors.append(f"{where}.match 必须是对象")
            match_raw = {}
        unknown_match = set(match_raw) - {"operators", "exclude"}
        if unknown_match:
            errors.append(f"{where}.match 含未知字段 {sorted(unknown_match)}")
        operators = match_raw.get("operators", [])
        if not isinstance(operators, list) or any(
            not isinstance(item, str) or not item for item in operators
        ):
            errors.append(f"{where}.match.operators 必须是非空字符串列表")
            operators = []
        exclude = match_raw.get("exclude", [])
        if not isinstance(exclude, list) or any(
            not isinstance(item, str) or not item for item in exclude
        ):
            errors.append(f"{where}.match.exclude 必须是字符串列表")
            exclude = []

        scenes_raw = profile_raw.get("scenes")
        scenes: Dict[str, SceneSpec] = {}
        if not isinstance(scenes_raw, dict) or not scenes_raw:
            errors.append(f"{where}.scenes 必须是非空对象")
        else:
            for scene_name, scene_raw in scenes_raw.items():
                if not isinstance(scene_name, str) or not scene_name.strip():
                    errors.append(f"{where}.scenes: 场景名必须是非空字符串")
                    continue
                scene = _validate_scene(scene_raw, f"{where}.scenes[{scene_name}]", errors)
                if scene is not None:
                    scene.name = scene_name
                    scenes[scene_name] = scene
        profiles.append(
            Profile(id=profile_id, operators=operators, exclude=exclude, scenes=scenes)
        )
    return profiles


def load_dim_axes(path: Optional[str | os.PathLike] = None, *,
                  strict: bool = True) -> Optional[DimAxesConfig]:
    config_path = Path(path) if path else DEFAULT_DIM_AXES_PATH
    if not config_path.is_file():
        return None
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DimAxesError(f"读取 dim_axes 配置失败 {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise DimAxesError(f"dim_axes 配置根节点必须是对象: {config_path}")

    errors: List[str] = []
    unknown_root = set(raw) - {"version", "profiles"}
    if unknown_root:
        errors.append(f"根节点含未知字段 {sorted(unknown_root)}")
    version = raw.get("version")
    profiles = _load_profiles(raw.get("profiles"), errors)
    config = DimAxesConfig(
        version=version, profiles=profiles, path=str(config_path), errors=errors
    )
    errors.extend(validate_config(config))
    errors = list(dict.fromkeys(errors))
    config.errors = errors
    if errors and strict:
        raise DimAxesError("dim_axes 配置校验失败:\n- " + "\n- ".join(errors))
    return config


def resolve_scene(config: Optional[DimAxesConfig], operator_name: str,
                  requested_scene: Optional[str] = None
                  ) -> Tuple[Optional[Profile], Optional[SceneSpec], str]:
    if config is None:
        return None, None, "no_config"
    profile = config.resolve_profile(operator_name)
    if profile is None:
        return None, None, "no_profile"
    if requested_scene not in (None, ""):
        scene = profile.scenes.get(requested_scene)
        if scene is None:
            return profile, None, f"scene_not_found:{requested_scene}"
        return profile, scene, "explicit"
    scene = profile.scenes.get("default")
    if scene is not None:
        return profile, scene, "default"
    if len(profile.scenes) == 1:
        return profile, next(iter(profile.scenes.values())), "single_scene"
    return profile, None, "scene_required"


def iter_axis_values(scene: SceneSpec) -> Iterable[Tuple[str, AxisSpec]]:
    for name, axis in scene.axes.items():
        yield name, axis
