# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
版权信息：华为技术有限公司，版本所有(C) 2025-2025
修改记录：2026/1/5 19:44
功能：参数约束关系实现
"""
import ast
import copy
import re
from collections import defaultdict
from typing import List, Dict, Any

import z3
from pydantic import BaseModel

from agent.generators.atk_common_utils.case_config import CaseConfig
from agent.generators.common_model_definition import InterParamConstraint, OperatorRule
from agent.generators.common_utils.common_dispatcher import CommonDispatcher
from agent.generators.common_utils.data_handle_utils import DataHandleUtil
from agent.generators.common_utils.expression_analysis import ExpressionPreprocessor
from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.data_definition.constants import ParamModelConfig, DataMatchMap
from agent.generators.data_definition.param_models_def import ParameterPropertyData, ParamRangeValueType
from agent.generators.operator_param_combine.combination_result_generator.constraint.remover import \
    remove_missing_param_exprs
from agent.generators.operator_param_combine.generate_combination_input.combination_constraint_generate import \
    CombinationConstraintGenerate
from agent.generators.operator_param_models.case_generate import CaseGenerate
from agent.generators.param_constraint_solve.customize_expression_solver_utils import CustomizeConstraintPatch
from agent.generators.param_constraint_solve.case_expr_evaluator import build_namespace, eval_constraint
from agent.generators.param_constraint_solve.z3_expression_solver_utils import (
    Z3ConstraintBuilder,
    ASTtoZ3Converter,
    MAX_STATIC_UNROLL,
)

logger = LazyLogger()


class ParamSetValueFlag(BaseModel):
    dtype: bool = False
    shape: bool = False
    range_values: bool = False
    format: bool = False
    length: bool = False


# ---------------------------------------------------------------------------
# all()/any() 定长展开：静态长度解析
# ---------------------------------------------------------------------------
# 背景：`array_length` 固定（或约束写了 `len(p) == N`）之后，`all(<pred> for v in p.range_value)`
# 仍被编码成 `ForAll(k, 0<=k<Length(p) => ...)`，Z3 不会因为长度有常数上界就退化成点对点
# 子句，长数组求解明显变慢。这里用纯 AST 解析出「唯一确定的长度」，交给 builder 在转换期
# 展开成 And/Or，约束文件保持紧凑写法。
# 只认**合取式**里的事实：带 `or`/`not`/`if-else` 的约束可能只是某个分支成立，不能据此断言长度。


def _is_static_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _len_target_name(node):
    """把 `len(p)` / `p.length` 归一成参数名，其余形态返回 None。"""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
    ):
        return node.args[0].id
    if isinstance(node, ast.Attribute) and node.attr == "length" and isinstance(node.value, ast.Name):
        return node.value.id
    return None


def _is_conjunctive(tree) -> bool:
    """表达式是否只由 and/比较组成（无 or、not、条件表达式）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and not isinstance(node.op, ast.And):
            return False
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return False
        if isinstance(node, ast.IfExp):
            return False
    return True


def _exact_len_facts(tree) -> tuple[dict, list]:
    """抽取 `len(p) == N` 常量事实与 `len(p) == len(q)` 等长边（支持链式比较）。"""
    constants: dict[str, set] = {}
    edges: list[tuple[str, str]] = []

    def add_constant(name, value):
        constants.setdefault(name, set()).add(value)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left_node = node.left
        for op, right_node in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq):
                left_name = _len_target_name(left_node)
                right_name = _len_target_name(right_node)
                if left_name and isinstance(right_node, ast.Constant) and _is_static_int(right_node.value):
                    add_constant(left_name, right_node.value)
                elif right_name and isinstance(left_node, ast.Constant) and _is_static_int(left_node.value):
                    add_constant(right_name, left_node.value)
                elif left_name and right_name:
                    edges.append((left_name, right_name))
            left_node = right_node
    return constants, edges


def resolve_static_lengths(exprs, hard_lengths=None) -> dict:
    """从约束表达式与组合期硬钉长度解析出「唯一确定的长度」。

    :param exprs: 约束表达式文本列表
    :param hard_lengths: 已在变量声明期硬钉的长度（非 free-length 参数的组合长度）
    :return: param -> length；只返回能唯一确定且 1..MAX_STATIC_UNROLL 的参数
    """
    hard = {
        str(name): value
        for name, value in (hard_lengths or {}).items()
        if _is_static_int(value) and value >= 1
    }
    parent: dict[str, str] = {}

    def find(name):
        parent.setdefault(name, name)
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(left, right):
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    constants: dict[str, set] = {}
    for expr in exprs or []:
        if not isinstance(expr, str) or not expr.strip():
            continue
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            continue
        if not _is_conjunctive(tree):
            continue
        expr_constants, edges = _exact_len_facts(tree)
        for name, values in expr_constants.items():
            constants.setdefault(name, set()).update(values)
        for left, right in edges:
            union(left, right)

    members = set(parent) | set(hard) | set(constants)
    by_root: dict[str, list[str]] = {}
    for name in members:
        by_root.setdefault(find(name), []).append(name)

    resolved: dict[str, int] = {}
    for names in by_root.values():
        values = set()
        for name in names:
            if name in hard:
                values.add(hard[name])
            values.update(constants.get(name, set()))
        if len(values) != 1:
            continue
        value = values.pop()
        if 1 <= value <= MAX_STATIC_UNROLL:
            for name in names:
                resolved[name] = value
        else:
            logger.debug(f"Static length {value} exceeds unroll limit {MAX_STATIC_UNROLL}, keep quantifier")
    return resolved


# ---------------------------------------------------------------------------
# reduce 类算子输出 shape 确定性派生（keepDim=false 去轴 / true 置 1）
# ---------------------------------------------------------------------------
# 轴参数名提示集合（小写匹配）。reduce 语义由「同时存在轴参数 + keepDim 参数」共同标识，
# 避免把仅带 dim 的切分/门控算子（如 GeGluV3，无 keepDim）误纳入。
_REDUCE_DIM_PARAM_NAMES = {
    "dim", "dims", "axis", "axes", "reduce_dim", "reduce_dims", "reduce_axis", "reduce_axes",
}
_REDUCE_KEEPDIM_PARAM_NAMES = {
    "keepdim", "keepdims", "keep_dim", "keep_dims",
}
# 源 tensor 名称提示（关系约束缺失时的兜底）
_REDUCE_SOURCE_PARAM_NAMES = ("self", "x", "input", "input_x", "in", "lhs", "src")
# 可作为输出 shape 派生依据的约束类型（按 relation_params 定位源 tensor，不解析表达式）
_REDUCE_OUTPUT_SHAPE_DEP_TYPES = {"shape_dependency", "shape_value_dependency"}


def _as_case_input_map(case) -> Dict[str, Any]:
    """把 CaseConfig.inputs（可能含嵌套 list）摊平为 {param_name: InputCaseConfig}。"""
    by_name: Dict[str, Any] = {}
    for item in getattr(case, "inputs", None) or []:
        items = item if isinstance(item, (list, tuple)) else [item]
        for sub_item in items:
            name = getattr(sub_item, "name", None)
            if name:
                by_name[name] = sub_item
    return by_name


def normalize_reduce_axes(raw_axes, rank: int):
    """对轴序列做去重 + 负轴归一化。

    保留首次出现顺序；任一轴归一化后越界（<0 或 >=rank）返回 None（调用方拒产）。
    """
    normalized: List[int] = []
    for axis in raw_axes:
        try:
            axis = int(axis)
        except (TypeError, ValueError):
            return None
        if axis < 0:
            axis += rank
        if axis < 0 or axis >= rank:
            return None
        if axis not in normalized:
            normalized.append(axis)
    return normalized


def _extract_reduce_axes(dim_case):
    """从轴参数提取执行器视角的原始轴序列。

    镜像执行层 `_expand_attrs_input` 对带 length 的 attrs 展开语义：length>0 时
    标量复制 length 份、list 长度不等时以首元素复制 length 份；否则按 list/标量原样。
    """
    range_value = getattr(dim_case, "range_values", None)
    length = getattr(dim_case, "length", None)
    try:
        length = int(length) if length is not None else None
    except (TypeError, ValueError):
        length = None
    if length is not None and length > 0:
        if isinstance(range_value, (list, tuple)):
            rv_list = list(range_value)
        elif range_value is not None:
            rv_list = [range_value]
        else:
            rv_list = []
        if len(rv_list) == length:
            return rv_list
        if len(rv_list) == 1:
            return rv_list * length
        if not rv_list:
            return []
        return [rv_list[0]] * length
    if range_value is None:
        return None
    if isinstance(range_value, (list, tuple)):
        return list(range_value)
    return [range_value]


def _resolve_reduce_source(output_name, output_names, dim_param, keepdim_param, by_name, constraints):
    """定位被 reduce 的源 tensor：优先关系约束 relation_params，其次名称提示兜底。"""
    for dependency in constraints or []:
        if getattr(dependency, "expr_type", None) not in _REDUCE_OUTPUT_SHAPE_DEP_TYPES:
            continue
        relation_params = list(getattr(dependency, "relation_params", None) or [])
        if output_name not in relation_params:
            continue
        for param_name in relation_params:
            if param_name in output_names or param_name in (dim_param, keepdim_param):
                continue
            case_input = by_name.get(param_name)
            if (case_input is not None and case_input.type in ParamModelConfig.TENSOR_ATK_TYPE
                    and case_input.shape is not None):
                return param_name
    for hint in _REDUCE_SOURCE_PARAM_NAMES:
        for param_name, case_input in by_name.items():
            if (param_name.lower() == hint and case_input.type in ParamModelConfig.TENSOR_ATK_TYPE
                    and case_input.shape is not None):
                return param_name
    return None


def derive_reduce_output_shapes(case, operator_rule_data) -> bool:
    """reduce 类算子输出 shape 确定性派生 + 轴参数去重/负轴归一化。

    对同时声明 dim/axis 轴参数与 keepDim/keepdim 布尔参数的算子，不再把 out.shape
    作为自由变量交 Z3 随机求解，而是按 self/dim/keepDim 确定性派生：
      - keepDim=true：非 reduce 轴取 self 对应轴，reduce 轴置 1；
      - keepDim=false：按序去除 reduce 轴后的 self 子序列。
    同时把轴参数归一化（去重 + 负轴转正）后原地回写，从源头杜绝重复轴/越界轴用例。

    非 reduce（缺 dim 或 keepDim）或无法定位源 tensor 时原样返回 True（不改动）。
    轴归一化后越界返回 False，由上层拒产该 case。函数幂等，可重复调用。
    """
    outputs = getattr(operator_rule_data, "outputs", None) or {}
    output_names = list(outputs.keys()) if hasattr(outputs, "keys") else list(outputs)
    if not output_names:
        return True
    by_name = _as_case_input_map(case)
    dim_param = None
    keepdim_param = None
    for param_name in by_name:
        lower_name = param_name.lower()
        if dim_param is None and lower_name in _REDUCE_DIM_PARAM_NAMES:
            dim_param = param_name
        if keepdim_param is None and lower_name in _REDUCE_KEEPDIM_PARAM_NAMES:
            keepdim_param = param_name
    if dim_param is None or keepdim_param is None:
        return True
    dim_case = by_name.get(dim_param)
    keepdim_case = by_name.get(keepdim_param)
    if dim_case is None or keepdim_case is None:
        return True

    constraints = getattr(operator_rule_data, "constraints_in_parameters", None)
    if isinstance(constraints, dict):
        constraints = [item for value in constraints.values() if isinstance(value, list)
                       for item in value]
    output_name_set = set(output_names)
    source_param = None
    for output_name in output_names:
        source_param = _resolve_reduce_source(output_name, output_name_set, dim_param, keepdim_param,
                                              by_name, constraints)
        if source_param is not None:
            break
    if source_param is None:
        logger.debug(
            f"Skip reduce output derivation, source tensor not located, "
            f"operator name : '{getattr(operator_rule_data, 'operator_name', '')}'")
        return True

    source_shape = list(by_name[source_param].shape or [])
    rank = len(source_shape)
    raw_axes = _extract_reduce_axes(dim_case)
    if raw_axes is None:
        return True
    axes = normalize_reduce_axes(raw_axes, rank)
    if axes is None:
        logger.error(
            f"Reduce axis out of range after normalization, operator name : "
            f"'{getattr(operator_rule_data, 'operator_name', '')}', dim : {raw_axes}, "
            f"source : '{source_param}', rank : {rank}")
        return False

    # 轴参数原地回写：去重 + 负轴归一化。带 length 的列表类轴参数必须同步收敛
    # length，否则执行层仍按原 length 复制首元素 → 重复轴无法根除；list 形态直接
    # 写归一化轴序列，标量形态写首个归一化轴。
    if getattr(dim_case, "length", None) is not None:
        dim_case.__setattr__("range_values", list(axes))
        dim_case.__setattr__("length", len(axes))
    else:
        dim_case.__setattr__("range_values", axes[0] if axes else 0)

    keep_dim = bool(keepdim_case.range_values)
    axis_set = set(axes)
    derived_shape = ([1 if i in axis_set else source_shape[i] for i in range(rank)] if keep_dim
                     else [source_shape[i] for i in range(rank) if i not in axis_set])
    for output_name in output_names:
        output_case = by_name.get(output_name)
        if output_case is None or getattr(output_case, "type", None) not in ParamModelConfig.TENSOR_ATK_TYPE:
            continue
        output_case.__setattr__("shape", list(derived_shape))
        logger.info(
            f"Derive reduce output shape, operator name : "
            f"'{getattr(operator_rule_data, 'operator_name', '')}', output : '{output_name}', "
            f"keepDim : {keep_dim}, dim : {axes}, shape : {derived_shape}")
    return True


class ParamConstraintUtils(CommonDispatcher):
    def __init__(self, case: CaseConfig, case_generate_instance: CaseGenerate,
                 inter_param_constraints: List[InterParamConstraint], operator_rule_data: OperatorRule,
                 param_combinations: Dict[str, ParameterPropertyData] = None,
                 param_domain_data: Dict[str, Any] = None,
                 is_generate_real_data: bool = False):
        self.inter_param_constraints = inter_param_constraints
        # 形如{'x1': {'DType': 'BFLOAT16', 'DataProfile': 'NaN', 'DimCount': '2', 'DimProperty': 'Has_Large_Size',
        # 'DataProfile': 'SubNormal', 'Memory': 'Contiguous'}, 'epsilon': {'Value': '1e-5'},
        # 'additionalOutput': {'Mode': 'True'}}
        self.param_combinations = param_combinations
        self.param_domain_data = param_domain_data
        self.operator_rule_data = operator_rule_data
        # 是否生成真实数据，若为FALSE只保留生成数据模型的名称，用于后续实际执行的时候调用生成真实数据
        self.is_generate_real_data = is_generate_real_data
        # 数据生成实例，用于在不满足约束条件时，重新生成参数的数据
        self.case_generate_instance = case_generate_instance
        self.operator_name = case.name
        self.case = case
        self.case_input_map = {case_input.name: case_input for case_input in case.inputs}
        self.broadcast_master_params = defaultdict(List[InterParamConstraint])
        self.has_set_value_param = defaultdict(ParamSetValueFlag)
        self.relation_params = list(operator_rule_data.inputs.keys())
        self.customize_constraint_patch = CustomizeConstraintPatch(case=case,
                                                                   case_generate_instance=case_generate_instance,
                                                                   inter_param_constraints=inter_param_constraints,
                                                                   operator_rule_data=operator_rule_data,
                                                                   param_combinations=param_combinations,
                                                                   is_generate_real_data=is_generate_real_data)
        self.dtype_domain_data, self.format_domain_data = self.get_param_domain_value()
        self._LEN_REF_RE = re.compile(r"\blen\(\s*([A-Za-z_]\w*)\s*\)")

    def get_param_domain_value(self) -> tuple[Dict[str, List], Dict[str, List]]:
        """
        获取所有参数的dtype和format的值域，用于构建求解条件
        :return: 每个参数dtype的值域数据和format值域数据
        """
        dtype_domain_data = {}
        format_domain_data = {}
        for param_name in self.case_input_map.keys():
            param_type = self.case_input_map.get(param_name).type
            dtype_domain = self.param_domain_data.get("parameters", {}).get(param_name, {}).get("dtype")
            format_domain = self.param_domain_data.get("parameters", {}).get(param_name, {}).get("format")
            dtype_domain = [DataHandleUtil.data_dtype_map(param_type, dtype) for dtype in dtype_domain]
            if dtype_domain:
                dtype_domain_data[param_name] = dtype_domain
            if format_domain:
                format_domain_data[param_name] = format_domain
        return dtype_domain_data, format_domain_data

    def is_param_all_input(self, relation_params: List[str]):
        """
        判断参数的角色是否都为输入
        :param relation_params: 约束相关的参数名称
        """
        for param_name in relation_params:
            if param_name not in self.case_input_map:
                logger.error(f"Can't match this parameter in input params, param name : {param_name}")
                return False
        return True

    def set_has_value_param_status(self, param_name, constraint_type: str):
        """
        将已确定的参数的属性的状态在self.has_set_value_param中设置为True，
        :param param_name: 参数名称
        :param constraint_type: 约束的类型
        :return: None
        """
        param_set_value_flag = self.has_set_value_param.get(param_name, ParamSetValueFlag())
        for type_key, type_list in DataMatchMap.CONSTRAINT_TYPE_MAP.items():
            if constraint_type in type_list:
                param_set_value_flag.__setattr__(type_key, True)
        self.has_set_value_param[param_name] = param_set_value_flag

    def correct_operator_param(self):
        """
        修复case的参数，在源数据上修改
        :return: None
        """
        logger.info(f"Start correct case param, operator name : {self.operator_name}")
        customize_constraints = []
        z3_constraints = []

        for constraint_relation in self.inter_param_constraints:
            relation_type = constraint_relation.expr_type
            if relation_type in ParamModelConfig.STRICT_CONSTRAINT_TYPE:
                customize_constraints.append(constraint_relation)
            else:
                # 这里用于筛选输出参数，如果约束表达式中包含输出参数，此表达式不进行处理，但有些情况下，表达式中除了关于输出参数的表达式，
                # 还可能有只包含输入参数的子表达式，如果不处理可能会导致部分表达式不生效
                # if not self.is_param_all_input(constraint_relation.relation_params):
                #     continue
                logger.debug(f"Relation type : {relation_type}, expr : {constraint_relation.expr}, use z3 solver")
                z3_constraints.append(constraint_relation)
        if not self.solve_z3_constraints(z3_constraints):
            logger.error(
                f"Correct case param failed because Z3 constraints are "
                f"invalid or unsatisfied, operator name : {self.operator_name}"
            )
            return False
        for customize_constraint in customize_constraints:
            relation_type = customize_constraint.expr_type
            logger.debug(f"Relation type : {relation_type}, use strict constraint logical")
            strict_check_result, relation_params = self.customize_constraint_patch.dispatch(relation_type,
                                                                                            customize_constraint)
            if not strict_check_result:
                logger.debug(
                    f"Relation type : {relation_type}, use strict constraint logical failed, "
                    f"check result : {strict_check_result}")
                return False
            for param_name in relation_params:
                self.set_has_value_param_status(param_name, relation_type)
            logger.debug(f"Relation type : {relation_type}, use strict constraint logical success")
        logger.info(f"End correct case param, operator name : {self.operator_name}")
        return True

    @staticmethod
    def adapter_dtype_in_expr(expr: str) -> str:
        """
        适配表达式中的数据类型，如原始表达式中为INT4, 统一替换为int, INT8统一替换为int8,
        需要替换的值位于DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP
        :param expr: 需要被替换的表达式
        :return: 替换之后的表达式
        """
        if not isinstance(expr, str):
            logger.info(f"Expr type is not string, can't be replace, expr : {expr}")
            return expr
        for key, value in DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP.items():
            if isinstance(expr, str):
                expr = re.sub(rf"\b{re.escape(key)}\b", value, expr)
        return expr

    def choice_no_conflicts_expr(self, builder: Z3ConstraintBuilder, param_union_expr: List[str],
                                 param_static_expr_list: List[str]) -> None:
        """
        筛选和参数静态表达式不冲突的初始值表达式。
        使用 Z3 push/pop 增量检测，避免重复创建求解器。同时将确认不冲突的表达式永久添加至 builder。

        :param builder: 已声明变量并添加了 JSON 约束的 Z3ConstraintBuilder 实例
        :param param_union_expr: 关联表达式列表（会被原地修改）
        :param param_static_expr_list: 参数静态表达式列表
        """
        if not param_static_expr_list:
            return

        # 快速路径：一次添加全部，若 SAT 直接返回
        builder.solver.push()
        for static_expr in param_static_expr_list:
            replace_expr = ExpressionPreprocessor.apply_keyword_replace(static_expr)
            if ExpressionPreprocessor.validate_expression(replace_expr):
                tree = ast.parse(replace_expr, mode='eval')
                converter = ASTtoZ3Converter(builder)
                z3_constraint = converter.visit(tree.body)
                if z3_constraint is not None:
                    builder.solver.assert_and_track(z3_constraint, f"batch_chk:{static_expr[:50]}")
        check_status = builder.solver.check()
        if check_status == z3.sat:
            builder.solver.pop()
            # 永久添加所有静态表达式
            for static_expr in param_static_expr_list:
                param_union_expr.append(static_expr)
                replace_expr = ExpressionPreprocessor.apply_keyword_replace(static_expr)
                if ExpressionPreprocessor.validate_expression(replace_expr):
                    tree = ast.parse(replace_expr, mode='eval')
                    converter = ASTtoZ3Converter(builder)
                    z3_constraint = converter.visit(tree.body)
                    if z3_constraint is not None:
                        builder.solver.assert_and_track(z3_constraint, f"perm:{static_expr[:50]}")
            logger.debug(f"Batch check SAT, all {len(param_static_expr_list)} exprs kept")
            return
        elif check_status == z3.unknown:
            logger.error(f"Batch check SAT, unknown reason : {builder.solver.reason_unknown()}")
        else:
            unsat_core = builder.solver.unsat_core()
            logger.warning(f"Batch check SAT, unsat_core is {unsat_core}")
        builder.solver.pop()
        try:
            # 快速路径：一次添加全部，若 SAT 直接返回
            builder.solver.push()
            for static_expr in param_static_expr_list:
                replace_expr = ExpressionPreprocessor.apply_keyword_replace(static_expr)
                if ExpressionPreprocessor.validate_expression(replace_expr):
                    tree = ast.parse(replace_expr, mode='eval')
                    converter = ASTtoZ3Converter(builder)
                    z3_constraint = converter.visit(tree.body)
                    if z3_constraint is not None:
                        builder.solver.assert_and_track(z3_constraint, f"batch_chk:{static_expr[:50]}")
                        logger.debug(
                            f"Operator name : '{self.operator_name}', add static expr success : '{static_expr}'")
        except Exception as e:
            logger.error(f"Operator name : {self.operator_name}, add static expr failed : '{str(e)}'")
            return
        try:
            if builder.solver.check() == z3.sat:
                builder.solver.pop()
                # 永久添加所有静态表达式
                for static_expr in param_static_expr_list:
                    param_union_expr.append(static_expr)
                    replace_expr = ExpressionPreprocessor.apply_keyword_replace(static_expr)
                    if ExpressionPreprocessor.validate_expression(replace_expr):
                        tree = ast.parse(replace_expr, mode='eval')
                        converter = ASTtoZ3Converter(builder)
                        z3_constraint = converter.visit(tree.body)
                        if z3_constraint is not None:
                            builder.solver.assert_and_track(z3_constraint, f"perm:{static_expr}")
                logger.debug(f"Batch check SAT, all {len(param_static_expr_list)} exprs kept")
                return
            builder.solver.pop()
        except Exception as e:
            logger.error(f"Operator name : {self.operator_name}, check conflict expr failed : '{str(e)}'")
            return

        # 回退路径：逐个 push/pop 增量检测
        for static_expr in param_static_expr_list:
            builder.solver.push()
            try:
                replace_expr = ExpressionPreprocessor.apply_keyword_replace(static_expr)
                is_sat = False
                if ExpressionPreprocessor.validate_expression(replace_expr):
                    tree = ast.parse(replace_expr, mode='eval')
                    converter = ASTtoZ3Converter(builder)
                    z3_constraint = converter.visit(tree.body)
                    if z3_constraint is not None:
                        builder.solver.assert_and_track(z3_constraint, f"chk:{static_expr[:50]}")
                        check_status = builder.solver.check()
                        if check_status == z3.sat:
                            is_sat = True
                        elif check_status == z3.unknown:
                            logger.error(f"chk:{static_expr[:50]}, unknown reason : {builder.solver.reason_unknown()}")
                        else:
                            unsat_core = builder.solver.unsat_core()
                            logger.warning(f"chk:{static_expr[:50]}, unsat_core is {unsat_core}")
            finally:
                builder.solver.pop()
        try:
            for static_expr in param_static_expr_list:
                builder.solver.push()
                try:
                    replace_expr = ExpressionPreprocessor.apply_keyword_replace(static_expr)
                    is_sat = False
                    if ExpressionPreprocessor.validate_expression(replace_expr):
                        tree = ast.parse(replace_expr, mode='eval')
                        converter = ASTtoZ3Converter(builder)
                        z3_constraint = converter.visit(tree.body)
                        if z3_constraint is not None:
                            builder.solver.assert_and_track(z3_constraint, f"chk:{static_expr}")
                            if builder.solver.check() == z3.sat:
                                is_sat = True
                finally:
                    builder.solver.pop()

                if is_sat:
                    logger.debug(f"Check param value expr, result : no conflicts, expr : {static_expr}")
                    param_union_expr.append(static_expr)
                    # 永久添加
                    replace_expr = ExpressionPreprocessor.apply_keyword_replace(static_expr)
                    if ExpressionPreprocessor.validate_expression(replace_expr):
                        tree = ast.parse(replace_expr, mode='eval')
                        converter = ASTtoZ3Converter(builder)
                        z3_constraint = converter.visit(tree.body)
                        if z3_constraint is not None:
                            builder.solver.assert_and_track(z3_constraint, f"perm:{static_expr}")
        except Exception as e:
            logger.error(f"Operator name : {self.operator_name}, feedback conflict expr failed : '{str(e)}'")
            return

    def choice_no_conflicts_expr_core(self, builder: Z3ConstraintBuilder, param_union_expr: List[str],
                                      param_static_expr_list: List[str]) -> None:
        """
        core 模式：用 unsat_core 驱动筛选与参数静态表达式不冲突的初始值表达式（实验性）。

        把每条 static 以软约束 ``Implies(tag, z)`` 加入（一次 push），然后用
        ``check(assumptions=tags)`` 检测：SAT 则全部保留；UNSAT 则取 unsat_core，
        从 assume 集移除一个冲突 tag 再检，直至 SAT 或 UNKNOWN。最后 pop 并把仍在 assume
        中的 static 永久加入 builder。相比 ``choice_no_conflicts_expr`` 的 O(N) 次
        push/check/pop，此处为 O(冲突数) 次 check。

        注意：``assert_and_track`` 会把 tag 钉死 true（无法用 assumptions 选择性关闭），
        故改用 ``solver.add(Implies(tag, z))`` 实现真正的软约束 + ``check(*tags)`` +
        ``unsat_core()``，与 Java 端 ``choiceNoConflictsExprCore`` 一致。

        :param builder: 已声明变量并添加了 JSON 约束的 Z3ConstraintBuilder 实例
        :param param_union_expr: 关联表达式列表（会被原地修改）
        :param param_static_expr_list: 参数静态表达式列表
        """
        if not param_static_expr_list:
            return

        def _build_constraint(expr_str: str):
            """对应 Java buildConstraint：关键字替换 + 语法校验 + AST→Z3 表达式。"""
            replaced = ExpressionPreprocessor.apply_keyword_replace(expr_str)
            if not ExpressionPreprocessor.validate_expression(replaced):
                return None
            tree = ast.parse(replaced, mode='eval')
            return ASTtoZ3Converter(builder).visit(tree.body)

        builder.solver.push()
        # static_expr -> 软约束 tag（仅对可成功构建的表达式建 tag）
        tag_of: Dict[str, z3.BoolRef] = {}

        # 输出/派生侧参数优先于顺序参数丢弃，避免原版先丢输入致 Z3 凭空重造 shape 而超时。
        param_of_tag: Dict[z3.BoolRef, str] = {}
        output_param_names = set(self.operator_rule_data.outputs.keys()) \
            if getattr(self.operator_rule_data, "outputs", None) else set()
        # 派生侧参数自动识别
        derived_param_names = self._detect_derived_params(
            self.inter_param_constraints, set(self.case_input_map.keys()))

        for idx, static_expr in enumerate(param_static_expr_list):
            try:
                z3_constraint = _build_constraint(static_expr)
                if z3_constraint is not None:
                    # chk_core 前缀 + idx 保证 tag 唯一，避免与 naive 模式的 chk: 撞名
                    tag = z3.Bool(f"chk_core:{idx}:{static_expr[:40]}")
                    builder.solver.add(z3.Implies(tag, z3_constraint))
                    tag_of[static_expr] = tag
                    # 从静态表达式反解所属参数名，建立 tag→参数名映射供丢选择判别输出/派生侧。
                    name_match = re.match(r"^(?:len\()?(\w+)[.\)]", static_expr)
                    if name_match:
                        param_of_tag[tag] = name_match.group(1)
            except Exception as e:
                logger.error(f"choice_no_conflicts_expr_core, failed , expr : '{static_expr}', err msg : {str(e)}")

        assume = list(tag_of.values())
        iters = 0
        while assume:
            iters += 1
            check_status = builder.solver.check(*assume)
            if check_status == z3.sat:
                break
            if check_status == z3.unknown:
                logger.warning(
                    f"[choice_core] check UNKNOWN (iters={iters}, assume={len(assume)}/{len(tag_of)}) "
                    f"reason: {builder.solver.reason_unknown()}")
                break
            # UNSAT：取 unsat_core，从 assume 集移除一个冲突 tag
            unsat_core = builder.solver.unsat_core()
            core_list = list(unsat_core)
            if not core_list:
                logger.warning("[choice_core] empty unsat_core on UNSAT — base infeasible?")
                break
            core_set = set(core_list)
            # 按输出 > 派生 > 原版序三级优先级丢弃
            candidates = [tag for tag in assume if tag in core_set]
            if not candidates:
                logger.warning("[choice_core] unsat_core contains no static tag — base infeasible?")
                break
            # 三级丢弃优先级：输出 > 派生 > 原版顺序；但（`len(X.shape) in/== N`）从输出优先豁免、降为保底。
            def _is_rank_pin(expr_str):
                return bool(re.match(r'^len\(\w+(\.shape)?\)\s*(in\b|==)', expr_str or ''))
            output_candidates = [t for t in candidates
                                 if param_of_tag.get(t) in output_param_names
                                 and not _is_rank_pin(
                                     next((e for e, tg in tag_of.items() if tg is t), ''))]
            derived_candidates = [t for t in candidates
                                  if param_of_tag.get(t) in derived_param_names]
            if output_candidates:
                to_remove = output_candidates[0]
                logger.info(
                    f"[choice_core] drop-priority: output-side static preferred, "
                    f"param : '{param_of_tag.get(to_remove)}'")
            elif derived_candidates:
                to_remove = derived_candidates[0]
                logger.info(
                    f"[choice_core] drop-priority: derived-side static preferred, "
                    f"param : '{param_of_tag.get(to_remove)}'")
            else:
                to_remove = candidates[0]
            assume.remove(to_remove)
            dropped_expr = next(
                (expr for expr, tag in tag_of.items() if tag is to_remove), None)
            logger.warning(
                f"[choice_core] drop conflicting static : '{dropped_expr}'")

        logger.info(f"[choice_core] iters={iters}, kept {len(assume)}/{len(tag_of)} statics")
        builder.solver.pop()

        # 永久加入仍在 assume 中的 static
        kept = set(assume)
        for static_expr, tag in tag_of.items():
            if tag in kept:
                param_union_expr.append(static_expr)
                z3_constraint = _build_constraint(static_expr)
                if z3_constraint is not None:
                    builder.solver.assert_and_track(z3_constraint, f"perm_core:{static_expr[:50]}")

    @staticmethod
    def _detect_derived_params(inter_param_constraints, param_universe) -> set:
        """从约束表达式的比较结构自动识别"派生侧"参数（被其他参数决定的一方）。

        识别规则（保守，只取无歧义信号）：
        - 遍历每条约束表达式 AST 中的比较节点（==/!=/>/>=/</<=）；
        - 若某比较的左右两侧参数集**不相交**、且一侧恰为**单参数**、另一侧含
          **≥2 个参数**，则单参数侧判定为派生侧。典型形态：
          ``len(result.shape) == max(len(self.shape), len(target.shape))``
          → result 是被 self/target 决定的一方；
        - 单参数 vs 单参数（如 ``a.shape[i] == b.shape[i]``）视为对称约束，
          不判向；与常量比较（``beta.range_value >= 0``）无参数对方，跳过。

        仅影响丢弃优先级（第 2 级，介于输出参数与原版顺序之间），不影响约束
        语义与求解正确性；误判的代价只是丢了一个本不该优先丢的静态，Z3 仍会
        求解出合法解。

        :param inter_param_constraints: 约束列表（InterParamConstraint 或含 expr 键的 dict）
        :param param_universe: 参数名全集（case 输入输出），用于过滤循环变量等噪音
        :return: 派生侧参数名集合
        """
        derived = set()
        if not param_universe:
            return derived
        for constraint in inter_param_constraints or []:
            expr = getattr(constraint, "expr", None)
            if isinstance(constraint, dict):
                expr = constraint.get("expr")
            if not expr or not isinstance(expr, str):
                continue
            try:
                replaced = ExpressionPreprocessor.apply_keyword_replace(expr)
                if not ExpressionPreprocessor.validate_expression(replaced):
                    continue
                tree = ast.parse(replaced, mode="eval")
            except Exception:
                continue
            # comprehension 循环变量（i/idx 等）不是参数，排除
            loop_vars = {n.target.id for n in ast.walk(tree)
                         if isinstance(n, ast.comprehension)
                         and isinstance(n.target, ast.Name)}

            def _params(node):
                return {n.id for n in ast.walk(node)
                        if isinstance(n, ast.Name)
                        and n.id in param_universe
                        and n.id not in loop_vars}

            def _is_bare_attr(node):
                """裸属性链（Name/Attribute/Subscript，无 Call/运算/条件表达式）。"""
                return not any(isinstance(n, (ast.Call, ast.BinOp, ast.UnaryOp,
                                              ast.BoolOp, ast.IfExp))
                               for n in ast.walk(node))

            for node in ast.walk(tree):
                if not (isinstance(node, ast.Compare) and len(node.ops) == 1
                        and isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.Gt,
                                                     ast.GtE, ast.Lt, ast.LtE))):
                    continue
                left_params = _params(node.left)
                right_params = _params(node.comparators[0])
                if not left_params or not right_params:
                    continue
                if not left_params.isdisjoint(right_params):
                    continue
                if len(left_params) == 1 and len(right_params) >= 2:
                    derived |= left_params
                elif len(right_params) == 1 and len(left_params) >= 2:
                    derived |= right_params
                elif len(left_params) == 1 and len(right_params) == 1:
                    # 单 vs 单：仅当一侧为裸属性链、另一侧含计算（a == f(b) 形态，
                    # 如 weightScale == f(weight)）时，裸属性侧判为派生侧；
                    # 两侧均裸（a.shape == b.shape）或均含计算视为对称，不判向。
                    # 已知局限：计算式在左侧的写法（f(a) == b）会判反向——文档
                    # 约定派生方通常裸写在左侧，误判仅影响丢弃优先级不影响正确性。
                    left_bare = _is_bare_attr(node.left)
                    right_bare = _is_bare_attr(node.comparators[0])
                    if left_bare and not right_bare:
                        derived |= left_params
                    elif right_bare and not left_bare:
                        derived |= right_params
        return derived

    def build_param_dtype_constraint(self, constraint_exprs: List[str],
                                     builder: Z3ConstraintBuilder, check: bool = True) -> None:
        """
        查找是否有参数的dtype属性取值已确定，如果有，是否和已有的规则冲突，如不冲突，则添加为求解条件，避免每次求解结构都相同，如冲突，则不添加
        :param constraint_exprs: 约束表达式对象（会被原地修改）
        :param builder: Z3求解器构建器
        :param check: 是否立即执行冲突检测
        """
        static_value_exprs = []
        relation_param = list(self.case_input_map.keys())
        for param in relation_param:
            param_dtype = self.case_input_map.get(param).dtype
            # 仅对可识别的张量 dtype 生成静态约束。None/NoneType 等占位(例如仅支持 None
            # 的预留 tensor 参数)不是可求解 dtype，若原样注入 `param.dtype == 'NoneType'`
            # 会让 convert_operand 抛 "Unknown dtype string"。DTYPE_SPECS 的键与 Z3 层
            # DTYPE_MAP 一致，据此过滤即可精确跳过不可识别项。
            if param_dtype is None or str(param_dtype) not in DataMatchMap.DTYPE_SPECS:
                continue
            static_value_exprs.append(f"{param}.dtype == '{param_dtype}'")
        if check:
            self.choice_no_conflicts_expr(builder=builder, param_union_expr=constraint_exprs,
                                          param_static_expr_list=static_value_exprs)
        else:
            constraint_exprs.extend(static_value_exprs)

    def build_param_range_value_constraint(self, constraint_exprs: List[str],
                                           builder: Z3ConstraintBuilder, check: bool = True,
                                           hard_static_expr_list: List[str] | None = None) -> None:
        """
        对于range_value，如果range_value是浮点数或整数，则直接加入求解条件表达式，否则不加入求解表达式
        :param constraint_exprs: 约束条件数据（会被原地修改）
        :param builder: Z3求解器构建器
        :param check: 是否立即执行冲突检测
        :param hard_static_expr_list: 标量参数的取值域静态表达式硬约束输出列表（可
            选）。标量参数（非 TENSOR_ATK_TYPE）的 allowed_range_value 是文档声明
            的合法取值域，不允许在冲突消解中被当作偏好丢弃；传入该列表时，标量
            域表达式改写入此列表，由 solve_z3_constraints 在偏好消解前永久断言。
            tensor 参数的 range_value 是数据内容区间，仍按偏好静态参与消解。
        """
        static_range_value_expr_list = []
        scalar_range_value_attr_name = "range_value"
        list_tensor_range_value_attr_name = "range_value[0]"
        relation_params = list(self.case_input_map.keys())
        for param_name in relation_params:
            param_attr = self.operator_rule_data.inputs.get(param_name)
            if param_attr is None:
                param_attr = self.operator_rule_data.outputs.get(param_name)
            if param_attr is None:
                continue
            param_range_value_constraint, value_type = DataHandleUtil.get_relevant_attribute_value(
                param_name, param_attr.allowed_range_value, "allowed_range_value")
            if param_range_value_constraint is None:
                continue
            param_type = self.case_input_map.get(param_name).type
            z3_param_type = DataMatchMap.Z3_VAR_TYPE_MAP.get(param_type, "tensor")
            if z3_param_type in ParamModelConfig.TENSOR_ATK_TYPE:
                range_value_attr_name = list_tensor_range_value_attr_name
            else:
                range_value_attr_name = scalar_range_value_attr_name
            param_range_value_expr_list = []
            for value_rule in param_range_value_constraint:
                if value_type == ParamRangeValueType.ENUM.value:
                    if value_rule is None:
                        param_range_value_expr_list.append(f"{param_name} is {value_rule}")
                    elif isinstance(value_rule, str):
                        param_range_value_expr_list.append(f"{param_name}.{range_value_attr_name} == '{value_rule}'")
                    elif isinstance(value_rule, list):
                        value_rule_expr_list = []
                        for val_index, val in enumerate(value_rule):
                            value_rule_expr_list.append(f"{param_name}[{val_index}] == {val}")
                        value_rule_expr_str = " and ".join(value_rule_expr_list)
                        value_rule_expr_str = f"({value_rule_expr_str})"
                        param_range_value_expr_list.append(value_rule_expr_str)
                    else:
                        param_range_value_expr_list.append(f"{param_name}.{range_value_attr_name} == {value_rule}")
                else:
                    if isinstance(value_rule, (list, tuple)) and len(value_rule) >= 2:
                        param_range_value_expr_list.append(
                            f"({param_name}.{range_value_attr_name} > {value_rule[0]} and {param_name}.{range_value_attr_name} < {value_rule[1]})")
                    elif isinstance(value_rule, (int, float, bool)):
                        # Some extracted torch_npu defaults are represented as
                        # an exact scalar even when the outer rule is tagged
                        # as range. Treat that as a singleton domain instead
                        # of calling len() on the scalar.
                        param_range_value_expr_list.append(
                            f"{param_name}.{range_value_attr_name} == {value_rule}"
                        )
                    else:
                        logger.error(
                            f"Param name : {param_name}, allowed range value is invalid, type : 'range', value : '{value_rule}'")
            param_range_value_expr = " or ".join(param_range_value_expr_list)
            if (hard_static_expr_list is not None
                    and z3_param_type not in ParamModelConfig.TENSOR_ATK_TYPE):
                # 标量取值域 → 硬约束通道，不参与冲突丢弃（防域外解，见
                # solve_z3_constraints 注释）
                hard_static_expr_list.append(param_range_value_expr)
            else:
                static_range_value_expr_list.append(param_range_value_expr)
        if check:
            self.choice_no_conflicts_expr(builder=builder, param_union_expr=constraint_exprs,
                                          param_static_expr_list=static_range_value_expr_list)
        else:
            constraint_exprs.extend(static_range_value_expr_list)

    def build_param_range_value_seed_constraint(self, constraint_exprs: List[str],
                                                builder: Z3ConstraintBuilder, check: bool = True) -> None:
        """把 list 型参数（attrs/scalars/tensors）的 per-row 取值钉入 Z3 求解。

        背景（aclnnAffineGrid theta.shape[0]==1 实证）：PICT 给 size.range_value
        分配的标量（如 128）传入 ListVar 后仅存入 _range_spec —— 既不构成约束
        （_add_initial_range_constraints 已禁用），也不参与求解。导致 size 元素
        仅剩 JSON 表达式约束，size[0] 贴 '0<x' 下界取 1，theta.shape[0] 恒为 1，
        per-row 多样性全链路丢失。dtype/format/shape/length 四个静态通道都读
        per-row 值，唯独 range_value 缺失 —— 本方法补上这条通道。

        种子表达式形态（per-row 值的两种形态）：
        - list（如 [-2,-1]）  -> "{param}.range_value == [-2, -1]"
          整数组取值钉住（Seq==List 比较，转换器 334-344 行支持）
        - 标量（如 128）      -> "{param}.range_value[0] == 128"
          维度代表值钉住首元素，其余元素仍由 JSON 约束（如 0<x<=100000）限定

        安全性：走偏好静态通道（choice_core 冲突消解），种子与 JSON 约束冲突时
        丢弃种子保 JSON 约束，不会整 case UNSAT —— 与 dtype/length 通道同模式。
        """
        seed_static_value_expr_list = []
        relation_param = list(self.case_input_map.keys())
        for param in relation_param:
            case_input = self.case_input_map.get(param)
            # 仅处理 list 型参数（attrs/scalars/tensors）；tensor/scalar 各有
            # 自己的取值通道（tensor 的 range_value 是数据内容区间，scalar 的
            # build_param_range_value_constraint 已按源 JSON 域处理）
            if case_input.type not in ParamModelConfig.LIST_ATK_TYPE:
                continue
            # tensorList类参数的取值范围如果是区间[-1,1]，不适合使用x.range_value = [-1,1]来表达，表达式左边是数组变量，后边是数值变量，
            # Z3无法将[-1,1]识别为数组，这类表达式不要加入求解模型，scalars/attrs 类型（如 tuningConfigOptional）不受影响，照常生成
            if DataMatchMap.Z3_VAR_TYPE_MAP.get(case_input.type) == "tensor_list":
                logger.debug(
                    f"Range value seed skipped, tensor_list param does not support "
                    f"'equal to list' form, param name : '{param}', "
                    f"seed : '{case_input.range_values}'")
                continue
            range_value_seed = case_input.range_values
            if range_value_seed is None:
                continue
            if isinstance(range_value_seed, list):
                if not range_value_seed:
                    continue
                # list 形态：整个数组的取值即 per-row 选择（如 alltoAllAxes 的
                # [-2,-1]）。元素需为数值字面量，profile 名（如 'Pos'）跳过
                if not all(isinstance(element, (int, float, bool)) for element in range_value_seed):
                    logger.debug(
                        f"Range value seed skipped, non-numeric elements, param name : '{param}', "
                        f"seed : '{range_value_seed}'")
                    continue
                seed_expr = "{param_name}.range_value == {seed_value}".format(
                    param_name=param, seed_value=range_value_seed)
            elif isinstance(range_value_seed, (int, float)):
                # 标量形态：维度代表值（如 SHAPE_DIM_VALUES 的 128），钉住首元素
                seed_expr = "{param_name}.range_value[0] == {seed_value}".format(
                    param_name=param, seed_value=range_value_seed)
            else:
                # profile 名（'Pos'/'Max' 等）或其他形态：无对应 Z3 钉住语义，跳过
                logger.debug(
                    f"Range value seed skipped, unsupported seed form, param name : '{param}', "
                    f"seed : '{range_value_seed}'")
                continue
            logger.debug(
                f"Range value seed constraint, param name : '{param}', seed expr : '{seed_expr}'")
            seed_static_value_expr_list.append(seed_expr)
        if check:
            self.choice_no_conflicts_expr(builder=builder, param_union_expr=constraint_exprs,
                                          param_static_expr_list=seed_static_value_expr_list)
        else:
            constraint_exprs.extend(seed_static_value_expr_list)

    def build_param_shape_len_constraint(self, constraint_exprs: List[str], builder: Z3ConstraintBuilder,
                                         check: bool = True,
                                         hard_static_expr_list: List[str] | None = None) -> None:
        """
        基于约束数据冲的dimension创建shape的len条件
        :param constraint_exprs: 约束表达式对象（会被原地修改）
        :param builder: Z3求解器构建器
        :param check: 是否立即执行冲突检测
        :param hard_static_expr_list: tensor 秩（len(shape)）静态表达式硬约束输出列表（可选）。
            tensor 维度是文档声明的结构硬约束，不允许在冲突消解中被当作偏好丢弃（防静默
            降秩：rank 表达式被丢后 shape 长度失去约束，解出 rank 1/2 的 self，设备
            CheckTbcShape 才失败）。传入时秩表达式写入此列表，由 solve_z3_constraints
            在偏好消解前永久断言；与硬约束集冲突时 whole solve UNSAT → 返回 False →
            上层按 param combination 轮换重试，快速失败不产错秩用例。
        """
        shape_len_static_value_expr_list = []
        relation_params = list(self.case_input_map.keys())
        for param_name in relation_params:
            param_ori_data = self.operator_rule_data.inputs.get(
                param_name) if param_name in self.operator_rule_data.inputs else self.operator_rule_data.outputs.get(
                param_name)
            if param_ori_data is None:
                continue
            param_shape_dimension, _ = DataHandleUtil.get_relevant_attribute_value(param_name,
                                                                                   param_ori_data.dimensions,
                                                                                   "dimensions")
            # 如果参数的shape属性为None，表明该参数不是tensor，没有shape属性，不需要添加shape约束
            if param_shape_dimension is None:
                continue
            if isinstance(param_shape_dimension, list):
                param_shape_dimension = list(set(param_shape_dimension))
                shape_len_expr = f"len({param_name}.shape) in {param_shape_dimension}"
            elif isinstance(param_shape_dimension, int):
                shape_len_expr = f"len({param_name}.shape) == {param_shape_dimension}"
            else:
                logger.warning(f"Can't get valid tensor dimension data, param name : {param_name}")
                continue
            if hard_static_expr_list is not None:
                hard_static_expr_list.append(shape_len_expr)
            else:
                shape_len_static_value_expr_list.append(shape_len_expr)

        if check:
            self.choice_no_conflicts_expr(builder=builder, param_union_expr=constraint_exprs,
                                          param_static_expr_list=shape_len_static_value_expr_list)
        else:
            constraint_exprs.extend(shape_len_static_value_expr_list)

    def build_param_shape_constraint(self, constraint_exprs: List[str],
                                     builder: Z3ConstraintBuilder, check: bool = True) -> None:
        """
        对于shape,如果该参数的parameter_constraints中shape约束没有axis_value的约束表达式，则不添加确定值的约束，避免约束冲突，
        此时将axis_value中的约束表达式添加至列表中，如果没有axis_value的约束表达式，则将shape当前取值添加至表达式列表
        :param constraint_exprs: 约束表达式对象（会被原地修改）
        :param builder: Z3求解器构建器
        :param check: 是否立即执行冲突检测
        """
        shape_static_value_expr_list = []
        relation_params = list(self.case_input_map.keys())
        for param_name in relation_params:
            if param_name not in self.case_input_map:
                continue
            case_param_shape = self.case_input_map.get(param_name).shape
            # 如果参数的shape属性为None，表明该参数不是tensor，没有shape属性，不需要添加shape约束
            if case_param_shape is None:
                continue
            shape_static_value_expr_list.append(f"{param_name}.shape == {case_param_shape}")

        if check:
            self.choice_no_conflicts_expr(builder=builder, param_union_expr=constraint_exprs,
                                          param_static_expr_list=shape_static_value_expr_list)
        else:
            constraint_exprs.extend(shape_static_value_expr_list)

    def build_param_format_constraint(self, constraint_exprs: List[str],
                                      builder: Z3ConstraintBuilder, check: bool = True) -> None:
        """
        查找是否有参数的format属性取值已确定，如果有，是否和已有的规则冲突，如不冲突，则添加为求解条件，避免每次求解结构都相同，如冲突，则不添加
        :param constraint_exprs: 约束表达式对象（会被原地修改）
        :param builder: Z3求解器构建器
        :param check: 是否立即执行冲突检测
        """
        format_static_value_expr_list = []
        relation_param = list(self.case_input_map.keys())
        for param in relation_param:
            if self.case_input_map.get(param).format is not None:
                format_static_value_expr_list.append(f"{param}.format == '{self.case_input_map.get(param).format}'")
        if check:
            self.choice_no_conflicts_expr(builder=builder, param_union_expr=constraint_exprs,
                                          param_static_expr_list=format_static_value_expr_list)
        else:
            constraint_exprs.extend(format_static_value_expr_list)

    def build_param_length_constraint(self, constraint_exprs: List[str],
                                      builder: Z3ConstraintBuilder, check: bool = True) -> None:
        length_static_value_expr_list = []
        relation_param = list(self.case_input_map.keys())
        for param in relation_param:
            if self.case_input_map.get(param).type in ParamModelConfig.LIST_ATK_TYPE and self.case_input_map.get(
                    param).length is not None:
                logger.debug(
                    f"******************* param.length : len({param}) == {self.case_input_map.get(param).length} ******************")
                length_static_value_expr_list.append(f"len({param}) == {self.case_input_map.get(param).length}")
        if check:
            self.choice_no_conflicts_expr(builder=builder, param_union_expr=constraint_exprs,
                                          param_static_expr_list=length_static_value_expr_list)
        else:
            constraint_exprs.extend(length_static_value_expr_list)

    def set_param_is_present(self, builder: Z3ConstraintBuilder):
        """
        设置求解器中is_present的取值，因为在组合生成阶段，已根据allowed_range_value和部分is_None表达式，预处理参数的is_present取值，
        这里直接取组合中参数的is_present取值
        :param builder: z3求解器实例
        """
        for param_name in self.case_input_map:
            param_property_data = self.param_combinations.get(param_name)
            if param_property_data is None:
                is_present_value = False
            else:
                is_present_value = param_property_data.is_present if hasattr(param_property_data,
                                                                             "is_present") else False
            if param_name not in builder.var_map:
                continue
            builder.solver.add(builder.var_map[param_name].is_present == is_present_value)

    def solve_no_present_param_in_constraint(self, constraints):
        """
        对于表达式中的 is None 和 is not None,根据is_present属性进行后处理，如果is_present为False,则将表达式中的和此参数相关的子表达式，
        如x is None 整个替换为True
        """
        constraint_after_solve_none = []
        existing_params = set()
        if self.param_combinations is not None:
            for param_name, property_data in self.param_combinations.items():
                if property_data.is_present:
                    existing_params.add(param_name)

        for constraint in constraints:
            # 处理当前参数和约束相关的参数，如果参数不在case_input_map中，则认为其is_present为False
            expr_ori = constraint.expr
            if expr_ori is None or expr_ori.strip() == "":
                continue
            logger.debug("Solve none in constraint : ")
            logger.debug(f"     ori expr : '{expr_ori}'")
            new_expr = remove_missing_param_exprs(expression=expr_ori, existing_params=existing_params)
            logger.debug(f"after replace : '{new_expr}'")
            constraint_solve_none = copy.deepcopy(constraint)
            constraint_solve_none.expr = new_expr
            constraint_after_solve_none.append(constraint_solve_none)
        return constraint_after_solve_none

    def _length_coupled_params(self, z3_constraints) -> set:
        """返回"长度被跨参数约束引用"的参数名集合。

        这些参数的长度若在变量声明期被硬钉（TensorListVar 的
        solver.add(self.length == 组合值) / ListVar 的
        solver.add(z3.Length(z3_var) == 组合值)），则约束中跨参数长度等式
        （如 len(x) == len(weight) == len(out)）退化为常数间算术 → 必然
        UNSAT；而组合阶段的长度偏好走 choice_no_conflicts_expr_core 的
        【软】约束通道，无权剔除 base 中的硬钉。将此类参数置 length=None
        交 Z3 自由求解；PICT 组合值仍由 build_param_length_constraint 以
        软静态保留，一致时优先、冲突时自动放宽。

        参数引用提取复用 CombinationConstraintGenerate._FACTOR_REF_RE
        （param.attr 形态）并上 len(P) 直接引用的并集，避免裸标识符正则
        把 len/shape/min 等函数名与属性名误计为参数。
        """
        operator_params = set(self.case_input_map.keys())
        coupled: set = set()
        for constraint in z3_constraints or []:
            expr = getattr(constraint, "expr", "") or ""
            if not expr:
                continue
            params_in_expr = {
                match.group("prev")
                for match in CombinationConstraintGenerate._FACTOR_REF_RE.finditer(expr)
                if match.group("prev")
            }
            params_in_expr |= set(self._LEN_REF_RE.findall(expr))
            params_in_expr &= operator_params
            if len(params_in_expr) < 2:  # 只认跨参数表达式
                continue
            for param_name in self._LEN_REF_RE.findall(expr):
                if param_name in operator_params and (params_in_expr - {param_name}):
                    coupled.add(param_name)
        return coupled

    def _static_lengths_for_unroll(self, constraints, free_len) -> dict:
        """收集可静态确定长度的 list 参数，供 all()/any() 定长展开。

        两类来源（都要求参数在本组合中 present 且 Z3 类型为 list/Seq）：
        - 变量声明期已硬钉的组合长度（非 free-length 参数的 `length`）；
        - 合取约束里唯一可推的 `len(p) == N` / `len(p) == len(q)` 常量。
        free-length 参数的组合长度是 PICT 软偏好，不能当作硬事实。
        """
        hard_lengths = {}
        for param_name, param_info in self.case_input_map.items():
            if param_name in free_len:
                continue
            if DataMatchMap.Z3_VAR_TYPE_MAP.get(param_info.type) != "list":
                continue
            combination = self.param_combinations.get(param_name) if self.param_combinations else None
            if combination is not None and not combination.is_present:
                continue
            length = getattr(param_info, "length", None)
            if _is_static_int(length) and length >= 1:
                hard_lengths[param_name] = length
        return resolve_static_lengths(
            [getattr(constraint, "expr", "") or "" for constraint in constraints or []],
            hard_lengths,
        )

    def _static_rank_max_for_unroll(self) -> dict:
        """收集文档声明的张量维度个数上限，供 range() 量词守护式展开。

        来源是文档 `dimensions` 列（如 2-8，指 len(p.shape) 的上界，不是某个轴的长度）。
        这个值已被 ``build_param_shape_len_constraint`` 写成 `len(p.shape) in [...]` 永久
        断言，属确定性事实，不会被冲突消解丢弃。

        :return: shape seq 常量名（``"<param>.shape"``，命名同 param_var_definition）-> 上界
        """
        rank_max = {}
        for param_name in self.case_input_map.keys():
            param_ori_data = (self.operator_rule_data.inputs.get(param_name)
                              if param_name in self.operator_rule_data.inputs
                              else self.operator_rule_data.outputs.get(param_name))
            if param_ori_data is None:
                continue
            dims, _ = DataHandleUtil.get_relevant_attribute_value(
                param_name, param_ori_data.dimensions, "dimensions")
            if isinstance(dims, int) and not isinstance(dims, bool):
                values = [dims]
            elif isinstance(dims, list):
                values = [v for v in dims if isinstance(v, int) and not isinstance(v, bool)]
            else:
                # None / 非法形态：推不出维度个数上限，对应量词保持原样。
                continue
            if values:
                # tensor_list 的 ``p.shape`` 落到单个元素的 shape seq 常量
                # ``<name>.elem.shape``（TensorListVar.elem_shape），普通 tensor 才是
                # ``<name>.shape``。键必须与 param_var_definition 一致，否则转换器
                # 查表落空、range() 量词不会展开（ScatterList C-002 实测）。
                case_input = self.case_input_map.get(param_name)
                z3_type = DataMatchMap.Z3_VAR_TYPE_MAP.get(
                    getattr(case_input, "type", None)) if case_input is not None else None
                suffix = "elem.shape" if z3_type == "tensor_list" else "shape"
                rank_max[f"{param_name}.{suffix}"] = max(values)
        return rank_max

    def declare_param_in_z3(self, builder: Z3ConstraintBuilder, is_print_log=False, free_length_params=None):
        """
        声明每个变量，指定变量的type(Tensor, scalar，list)，以及数据类型(float, int, string, bool)
        :param is_print_log: 日志中是否打印详细信息
        :param builder: z3求解器实例
        :param free_length_params: 长度交由Z3自由求解的参数名集合（长度被跨参数
            约束引用的参数）。这些参数声明期不硬性设置length属性值，PICT 组合值仍经
            build_param_length_constraint 以软静态偏好保留，一致时优先、冲突时放宽。
        :return: None
        """
        free_length_params = free_length_params or set()
        for param_name in self.case_input_map.keys():
            param_info = self.case_input_map.get(param_name)
            if not param_info:
                continue
            param_property_data = self.param_combinations.get(param_name)
            is_present_value = param_property_data.is_present if param_property_data is not None else False
            if not is_present_value:
                continue
            param_type = param_info.type
            z3_param_type = DataMatchMap.Z3_VAR_TYPE_MAP.get(param_type, "tensor")
            param_dtype = param_info.dtype
            param_length = param_info.length
            range_values = self.case_input_map.get(param_name).range_values
            if param_type in ParamModelConfig.TENSOR_ATK_TYPE:
                dtype_domain = self.dtype_domain_data.get(param_name)
                format_domain = self.format_domain_data.get(param_name)
                # tensor 分支：仅 tensor_list 接受 length；被跨参数长度关系引用的
                # 参数（free_length_params）不硬钉，交 Z3 求解（普通 tensor 维持
                # 原语义传 None）
                length_arg = (None if z3_param_type != "tensor_list" or param_name in free_length_params
                              else param_length)
                builder.declare_var(param_name, type_hint=z3_param_type, dtype=param_dtype, allowed_dtypes=dtype_domain,
                                    allowed_formats=format_domain, range_value=range_values,
                                    length=length_arg, is_print_log=is_print_log)
            else:
                # 非 tensor 分支：list 类型（scalars/attrs，aclIntArray 等）的
                # ListVar 构造期同样 z3.Length(z3_var) == length 硬钉，存在与
                # tensor_list 同构的跨参数 UNSAT 风险，按同一规则释放；scalar
                # 类型不接收 length（declare_var 工厂截断），显式传 None 消除误导
                length_arg = (None
                              if z3_param_type != "list" or param_name in free_length_params
                              else param_length)
                builder.declare_var(param_name, z3_param_type, dtype=param_dtype, range_value=range_values,
                                    length=length_arg, is_print_log=is_print_log)

        self.set_param_is_present(builder)

    def _post_check_tensor_rank_and_elements(self) -> bool:
        """落盘前用真 Python 语义复核 tensor 秩与元素总数（防静默降秩回归）。

        消费 case_expr_evaluator 的 build_namespace/eval_constraint，对每个 tensor 参数
        按文档声明 dimensions 复核 len(param.shape)（int → ==，list → in），并以真
        Python 乘积复核元素总数不超过 TENSOR_TENSOR_ELEMENT_LIMIT。任一不满足即返回
        False，交由上层拒产该 case（快速失败，不落错秩/超限用例）。eval 本身失败
        （ok=None，如 shape 为 None、expr 含未暴露属性）按 fail-open 跳过，不阻断。
        """
        namespace = build_namespace(self.case_input_map)
        element_limit = ParamModelConfig.TENSOR_TENSOR_ELEMENT_LIMIT
        for param_name, case_param in self.case_input_map.items():
            case_param_shape = getattr(case_param, "shape", None)
            if case_param_shape is None:
                continue
            param_ori_data = self.operator_rule_data.inputs.get(
                param_name) if param_name in self.operator_rule_data.inputs else self.operator_rule_data.outputs.get(
                param_name)
            if param_ori_data is None:
                continue
            param_shape_dimension, _ = DataHandleUtil.get_relevant_attribute_value(
                param_name, param_ori_data.dimensions, "dimensions")
            if param_shape_dimension is None:
                continue
            if isinstance(param_shape_dimension, list):
                rank_expr = f"len({param_name}.shape) in {list(set(param_shape_dimension))}"
            elif isinstance(param_shape_dimension, int):
                rank_expr = f"len({param_name}.shape) == {param_shape_dimension}"
            else:
                continue
            ok, err = eval_constraint(rank_expr, namespace)
            if ok is False:
                logger.error(
                    f"Post-check tensor rank violated, operator name : '{self.operator_name}', "
                    f"param name : '{param_name}', shape : {case_param_shape}, expr : '{rank_expr}'")
                return False
            if ok is None:
                logger.warning(
                    f"Post-check tensor rank eval skipped (fail-open), param name : '{param_name}', "
                    f"expr : '{rank_expr}', err : '{err}'")
            try:
                product = 1
                for dim in case_param_shape:
                    product *= int(dim)
            except (TypeError, ValueError) as e:
                logger.warning(
                    f"Post-check tensor elements skipped (fail-open), param name : '{param_name}', "
                    f"shape : {case_param_shape}, err msg : '{str(e)}'")
                continue
            if product > element_limit:
                logger.error(
                    f"Post-check tensor elements exceeded limit, operator name : '{self.operator_name}', "
                    f"param name : '{param_name}', shape : {case_param_shape}, "
                    f"elements : {product}, limit : {element_limit}")
                return False
        return True

    def solve_z3_constraints(self, z3_constraints: List[InterParamConstraint]):
        """
        针对所有的参数关联表达式，使用z3求解器求解
        :param: z3_constraints: 表达式列表，包含所有需要求解的表达式
        """

        logger.info(f"Start solving solution of constraints by Z3, operator name : {self.operator_name}")
        builder = Z3ConstraintBuilder()
        # 长度被跨参数约束引用的参数：声明期不硬性设置length，交 Z3 统筹求解。
        # 否则PICT 逐参数独立采样的长度值（如 len(x)=1/len(weight)=128）与
        # len(x)==len(weight)==len(out) 类关系在 base 硬钉下必然 UNSAT
        free_len = self._length_coupled_params(z3_constraints)
        if free_len:
            logger.info(f"Free (Z3-solved) lengths for cross-parameter coupled params : '{sorted(free_len)}'")
        self.declare_param_in_z3(builder=builder, is_print_log=True, free_length_params=free_len)
        constraint_after_solve_no = self.solve_no_present_param_in_constraint(z3_constraints)
        # all()/any() 定长展开：长度已确定的参数，量词在转换期展开成逐元素 And/Or
        # （约束文本不变，逻辑见 resolve_static_lengths）。
        static_lengths = self._static_lengths_for_unroll(constraint_after_solve_no, free_len)
        if static_lengths:
            builder.set_static_lengths(
                {name: value for name, value in static_lengths.items() if name in builder.var_map}
            )
        # range() 展开的循环上界：文档 dimensions 已作为硬约束断言，可据此推出取值范围。
        rank_max = self._static_rank_max_for_unroll()
        if rank_max:
            builder.set_static_rank_max(rank_max)
        expr_list = []
        for constraint_expr in constraint_after_solve_no:
            expr = constraint_expr.expr
            if expr is None or not expr:
                continue
            stripped = expr.strip()
            if stripped == "True":
                # 所有子式都涉及缺失参数，约束空泛满足，无约束力，跳过
                logger.debug(f"Skip vacuous constraint (True): '{expr}'")
                continue
            if stripped == "False":
                # 约束恒假（如缺失参数的 is not None），标记冲突后跳过
                logger.error(f"Constraint evaluates to False (conflict): '{expr}'")
                return False
            expr_list.append(expr)

        # 先添加 JSON 约束到求解器
        json_expr_dict = {f"json:{expr}": expr for expr in expr_list}
        builder.add_constraints(expr_str_dict=json_expr_dict)

        # fail-closed：任何 JSON 约束真实转换失败（非 todo_skip）即阻断该 case，
        # 禁止静默丢弃约束后产出缺约束的"伪 SAT"非法用例（曾致
        # aclnnApplyAdamWQuant absmax 尺寸失控 → NPU aivec 崩溃）。
        real_drops = [d for d in builder.dropped_constraints if d.get("reason") != "todo_skip"]
        if real_drops:
            logger.error(
                f"operator name : '{self.operator_name}', "
                f"{len(real_drops)} constraint(s) failed to convert and were "
                f"dropped (fail-closed): {[d.get('expr') for d in real_drops]}"
            )
            return False

        # 收集所有静态表达式，一次性批量冲突检测（5 次 choice_no_conflicts_expr → 1 次）
        all_static = []
        domain_static = []
        self.build_param_dtype_constraint(all_static, builder, check=False)
        self.build_param_format_constraint(all_static, builder, check=False)
        self.build_param_shape_constraint(all_static, builder, check=False)
        self.build_param_range_value_constraint(all_static, builder, check=False,
                                                hard_static_expr_list=domain_static)
        self.build_param_range_value_seed_constraint(all_static, builder, check=False)
        self.build_param_shape_len_constraint(all_static, builder, check=False,
                                              hard_static_expr_list=domain_static)
        self.build_param_length_constraint(all_static, builder, check=False)

        # 文档声明的标量取值域（allowed_range_value enum/range）是硬约束：在偏好
        # 消解之前永久断言。旧实现把它与 dtype/长度等偏好静态同权重送入
        # choice_no_conflicts_expr_core，联合不可满足时 unsat_core 可能丢掉域
        # 约束，标量随即漂到文档域外——20260917 run iter_001 FC-001 实证：
        # groupType enum [-1,0,2] 被丢后解出 4/5，全部 not(gt==X) 门控对域外值
        # 空真，生成非法用例。域约束先断言后，与偏好冲突时消解只会丢弃偏好
        # 静态；与硬约束集（JSON 关系约束）本身冲突时 whole solve UNSAT →
        # 返回 False → 上层按 param combination 轮换重试，快速失败不产非法用例。
        for domain_expr in domain_static:
            if not domain_expr or not domain_expr.strip():
                continue
            try:
                replaced = ExpressionPreprocessor.apply_keyword_replace(domain_expr)
                if not ExpressionPreprocessor.validate_expression(replaced):
                    logger.error(
                        f"Domain static expr invalid, skip hard assert : '{domain_expr}'")
                    continue
                tree = ast.parse(replaced, mode='eval')
                z3_constraint = ASTtoZ3Converter(builder).visit(tree.body)
                if z3_constraint is not None:
                    builder.solver.assert_and_track(
                        z3_constraint, f"perm_domain:{domain_expr[:50]}")
            except Exception as e:
                logger.error(
                    f"hard domain static assert failed, expr : '{domain_expr}', "
                    f"err msg : '{str(e)}'")

        if all_static:
            self.choice_no_conflicts_expr_core(builder=builder, param_union_expr=expr_list,
                                               param_static_expr_list=all_static)

        logger.info("Start whole expr solve")
        try:
            solver_result = builder.solve()
        except Exception as e:
            logger.error(f"operator name : '{self.operator_name}',Constraint set solve failed, err msg : '{str(e)}'")
            return False
        if not solver_result:
            logger.error(
                f"Z3 solver error, no solution can satisfy constraints, operator name : {self.operator_name}")
            return False
        logger.info(f"End solving solution of constraints by Z3, operator name : {self.operator_name}")

        absent_params = []

        for param_name in self.case_input_map:
            if param_name not in solver_result or solver_result.get(param_name).get("is_present") is False:
                absent_params.append(param_name)
        self.case.inputs = [inp for inp in self.case.inputs if inp.name not in absent_params]
        for param_name in absent_params:
            self.case_input_map.pop(param_name)

        for param_name in self.case_input_map.keys():
            property_dict = solver_result.get(param_name, {})
            for field_name in ParamSetValueFlag.model_fields.keys():
                # 参数必须在case_input_map中，且数据值不为None，如非tensor参数没有shape属性，但是Z3求解器会对表达式涉及到的所有参数所有属性都会求解
                param_attr_ori_status = param_name in self.case_input_map and getattr(
                    self.case_input_map.get(param_name), field_name, None) is not None
                attr_value = property_dict.get(field_name, None)
                if field_name == "length":
                    # tensor_list 的 length 由 Z3 求解为自由变量（采样器未钉值时 length=None、
                    # ori_status=False），但 Z3 已解出 len（受 cross_param 如 len(x)<=128、
                    # len(A)==len(B) 约束，并主动规避触发 sum()/shape 重约束的 len=1）。
                    # 必须回写该解，否则 cases.json length=None 致执行器默认 len=1 违反约束
                    # （tensorList-len Z3 逃逸）。仅写 >0 的解，丢弃 resolve_model 的 0 默认（未解）。
                    # 非 tensor_list 参数其 Var 不返回 length，attr_value=None 自然跳过。
                    if attr_value is not None and attr_value > 0:
                        self.case_input_map[param_name].__setattr__(field_name, attr_value)
                elif param_attr_ori_status and attr_value is not None:
                    self.case_input_map[param_name].__setattr__(field_name, attr_value)
        # reduce 类算子：out 是派生输出，不把 out.shape 当自由变量；按 self/dim/keepDim
        # 确定性派生（keepDim=false 去轴 / true 置 1），并就地归一化 dim（去重 + 负轴转正），
        # 从源头消除 CheckShape（out 轴任意）与 CheckDimValid（重复轴）失败。派生先于下方
        # 秩复核，保证复核的是最终派生 shape。
        if not derive_reduce_output_shapes(self.case, self.operator_rule_data):
            logger.error(
                f"Reject case, reduce axis out of range after normalization, "
                f"operator name : '{self.operator_name}'")
            return False
        # 生成优先：Z3 已给出解后直接保留用例。旧的 Python PostCheck 会再次 eval
        # constraints_in_parameters，并把“伪 SAT”用例 fail-closed 丢弃；复杂算子中
        # Param 包装、可选参数和 SeqSort 语义不完整，曾导致 0/10 用例全部被误杀。
        # 仅保留收窄后的秩/元素总数后置复核（case_expr_evaluator 真 Python 语义），
        # 命中秩违例或元素超限才拒产；其余合法性仍由后续 TTK/ATK 执行结果观察。
        if not self._post_check_tensor_rank_and_elements():
            logger.error(
                f"Post-check rejected resolved case (tensor rank/elements), "
                f"operator name : '{self.operator_name}'")
            return False
        return True
