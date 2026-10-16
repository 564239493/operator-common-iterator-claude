from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import List, Set, Optional, Any, Dict, TYPE_CHECKING, Callable

from agent.generators.common_utils.data_handle_utils import DataHandleUtil
from agent.generators.operator_param_combine.combination_result_generator.constraint.whitelist import ALLOWED_NAMES, \
    ALLOWED_CALLABLES

if TYPE_CHECKING:
    from agent.generators import OperatorRule
from agent.generators.data_definition.constants import ParamModelConfig, DataMatchMap


@dataclass(frozen=True)
class DroppedRecord:
    """
    一条被降级或丢弃的约束记录，用于核对与回溯。
    :param expr: 原始约束表达式
    :param reason: 丢弃或降级原因（英文，便于日志检索）
    :param detail: 附加细节（如命中的维索引引用、缺失的因子名），逗号分隔
    """

    expr: str
    reason: str
    detail: str = ""


def _collect_name_ids(expr: str) -> Set[str]:
    """
    收集表达式中所有 ``ast.Name`` 的标识符。

    用于降级台账记录未命中白名单的名字。
    :param expr: 约束表达式原文
    :return: 表达式中出现的标识符集合
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        # 解析失败时无可用标识符，返回空集合，交由调用方按原逻辑继续处理
        return set()
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


class CombinationConstraintGenerate:
    _FACTOR_REF_RE = re.compile(r"(?<![a-zA-Z_])($|(?P<prev>[a-zA-Z_]\w*))\.(?P<attr>[a-zA-Z_]\w*)")
    # 维索引探测：匹配 `<name>.shape[`，供降级原因落盘使用，不改变表达式。
    # 边界 (?<![a-zA-Z_]) 与本文件 _FACTOR_REF_RE 保持一致。
    _DIM_INDEX_RE = re.compile(r"(?<![a-zA-Z_])([a-zA-Z_]\w*)\.shape\s*\[")

    def __init__(self, operator_rule_data: OperatorRule):
        self.operator_rule_data = operator_rule_data
        self._ALLOWED_NAMES: Set[str] = set(ALLOWED_NAMES)

    @staticmethod
    def _dot_to_underscore(name: str) -> str:
        """Replace dots with underscores in a fully qualified factor name."""
        return name.replace(".", "_")

    @classmethod
    def _transform_expression(cls, raw: str) -> str:
        """Replace dotted factor references with underscored variable names.

        ``x1.dtype == 'FLOAT32'``  ->  ``x1_dtype == 'FLOAT32'``
        """

        def _replacer(m: re.Match) -> str:
            # m.group(0) is the full match including the dot
            # The dot is before "attr", so we reconstruct param_attr
            full = m.group(0)
            prev = m.group("prev")
            attr = m.group("attr")
            if prev is None:
                return full  # shouldn't happen with this regex
            return f"{prev}_{attr}"

        return cls._FACTOR_REF_RE.sub(_replacer, raw)

    def is_valid_combinatorial_constraint(self,
                                          expr: str,
                                          factor_names: Optional[List[str]] = None,
                                          ) -> bool:
        """Check whether *expr* is valid for the combinatorial generation engine.

        A constraint is valid only when every identifier it references is
        either a factor name (``param.attr``) or an allowed built-in.
        Expressions that **index into** a factor (e.g. ``x1.shape[0]``) or
        call methods on a factor (e.g. ``x1.shape.startswith('N')``) are
        rejected — the engine treats factor values as atomic scalars.

        The check uses ``ast.parse`` on the **already-transformed**
        expression (dots → underscores), so the same transformation
        applied by :func:`_transform_expression` is applied here first.

        Args:
            expr: Raw constraint string (e.g. ``"x1.shape[1] == x2.shape[0]"``).
            factor_names: Optional explicit list of valid factor names.
                If ``None`` (default), factor names are extracted from the
                expression itself via the same regex the engine uses.

        Returns:
            ``True`` if the expression can be used by the combinatorial engine.

        Example:
            >>> is_valid_combinatorial_constraint("x1.dtype == x2.dtype")
            True
            >>> is_valid_combinatorial_constraint("x1.shape[0] == x2.shape[1]")
            False
            >>> is_valid_combinatorial_constraint("len(x1.dtype) > 1")
            True
        """
        # Derive valid factor IDs from the expression itself (same regex)
        if factor_names is None:
            raw_refs: Set[str] = set()
            for m in self._FACTOR_REF_RE.finditer(expr):
                prev = m.group("prev")
                attr = m.group("attr")
                if prev is not None:
                    raw_refs.add(f"{prev}.{attr}")
            valid_ids = {CombinationConstraintGenerate._dot_to_underscore(fn) for fn in raw_refs}
        else:
            valid_ids = {CombinationConstraintGenerate._dot_to_underscore(fn) for fn in factor_names}

        transformed = CombinationConstraintGenerate._transform_expression(expr)
        allowed: Set[str] = valid_ids | self._ALLOWED_NAMES

        try:
            tree = ast.parse(transformed, mode="eval")
        except SyntaxError:
            return False

        for node in ast.walk(tree):
            # ---- Every Name must be known ----
            if isinstance(node, ast.Name):
                if node.id not in allowed:
                    return False

            # ---- Subscript on a factor reference (e.g. x1_shape[0]) ----
            # 此处不再决定整条去留，而是作为「该原子子句不可表达」的判定入口，
            # 由 check_constraint_expr 走 _prune_invalid_subclauses 做子句级降级。
            if isinstance(node, ast.Subscript):
                val = node.value
                if isinstance(val, ast.Name) and val.id in valid_ids:
                    return False

            # ---- Attribute/method call on a factor reference ----
            if isinstance(node, ast.Attribute):
                val = node.value
                if isinstance(val, ast.Name) and val.id in valid_ids:
                    return False

            # ---- Unauthorised function call ----
            if isinstance(node, ast.Call):
                func = node.func
                _allowed_callables: Set[str] = set(ALLOWED_CALLABLES)
                if isinstance(func, ast.Name) and func.id not in _allowed_callables:
                    return False

        return True

    @staticmethod
    def _split_top_level(expr: str, op: str) -> List[str]:
        """
        按顶层布尔运算符切分表达式，保留原始文本片段。
        只在括号深度为 0 处切分，避免误切 ``(a and b) or c`` 中括号内的 ``and``。
        :param expr: 待切分的约束表达式原文
        :param op: 顶层运算符字面量，只取 ``"and"`` 或 ``"or"``
        :return: 顶层切分后的片段列表；无该运算符时返回仅含原文的单元素列表
        """
        token = f" {op} "
        lowered = expr.lower()
        parts: List[str] = []
        buffer: List[str] = []
        depth = 0
        index = 0
        while index < len(expr):
            char = expr[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            if depth == 0 and lowered.startswith(token, index):
                parts.append("".join(buffer))
                buffer = []
                index += len(token)
                continue
            buffer.append(char)
            index += 1
        parts.append("".join(buffer))
        return parts

    @classmethod
    def _prune_invalid_subclauses(cls, expr: str,
                                  atom_valid: Callable[[str], bool]) -> Optional[str]:
        """
        子句级降级：整条表达式含不可表达原子子句时，按顶层布尔结构安全摘除。
        安全性规则如下：
        顶层 ``or`` 只要有一个析取项不可表达即返回 ``None``，因为析取项不可摘除，
        摘除会收紧约束、可能误删合法组合；
        顶层 ``and`` 摘除不可表达合取项后返回剩余合取项，放宽约束是安全的，
        多生成的组合由 PICT 二次约束过滤兜底；
        单原子子句可表达返回原串，否则返回 ``None``。
        :param expr: 待降级的约束表达式原文
        :param atom_valid: 原子子句可表达性判定函数，由各修复点注入
        :return: 降级后的表达式；无法安全降级时返回 ``None``
        """
        top_or = cls._split_top_level(expr, "or")
        if len(top_or) > 1:
            # 全部析取项均可表达：原样返回。
            # 示例：x.dtype == y.dtype or x.rank == y.rank
            #       -> x.dtype == y.dtype or x.rank == y.rank
            if all(atom_valid(p.strip()) for p in top_or):
                return expr
            # 逐个析取项独立降级：递归摘除该析取项内不可表达的合取项、保留其余，再以 or 重连；
            # 任一析取项无法降级时整条返回 None。
            # 示例：x.shape[0] == 2 and x.dtype == y.dtype or x.rank == y.rank
            #       -> (x.dtype == y.dtype) or (x.rank == y.rank)
            # 示例：x.shape[0] == 2 or x.dtype == y.dtype
            #       -> None
            widened: List[str] = []
            for part in top_or:
                degraded = cls._prune_invalid_subclauses(part.strip(), atom_valid)
                if degraded is None:
                    return None
                widened.append(
                    degraded if (degraded.startswith("(") and degraded.endswith(")")) else f"({degraded})"
                )
            return " or ".join(widened)
        top_and = cls._split_top_level(expr, "and")
        if len(top_and) > 1:
            # 逐个合取项判定：可表达者原样保留；不可表达者若是整体被括号包裹的子句，
            # 先剥壳递归降级，降级成功则保留其降级串（已套回一层括号），失败才摘除。
            # 示例：x.length == 1 and (y.shape[0] == 2 and z.dtype == w.dtype)
            #       -> (x.length == 1) and (z.dtype == w.dtype)
            # 示例：x.length == 1 and (y.shape[0] == 2)
            #       -> (x.length == 1)        # 无整体包裹或降级失败，退回摘除
            kept: List[str] = []
            for part in top_and:
                piece = part.strip()
                if atom_valid(piece):
                    kept.append(piece)
                    continue
                degraded = cls._prune_bracketed_subclause(piece, atom_valid)
                if degraded is not None:
                    kept.append(degraded)
            if not kept:
                return None
            # 顶层已无 ``or``（上面已拦截），故剩余片段以 ``and`` 重连不会改变优先级；
            # 此处按「是否整体被一对匹配括号包裹」判定，避免把 ``(a) and (b)`` 误判为已包裹。
            return " and ".join(
                p if cls._is_fully_wrapped(p) else f"({p})" for p in kept
            )

        return expr if atom_valid(expr) else cls._prune_bracketed_subclause(expr, atom_valid)

    @classmethod
    def _is_fully_wrapped(cls, expr: str) -> bool:
        """
        判断表达式是否整体被一对匹配的圆括号包裹。
        :param expr: 待判定表达式
        :return: 整体被一对匹配括号包裹时返回 True

        示例：
            "(x.length == 1 and weight.length == 1)" -> True
            "(x.length == 1) and (weight.length == 1)" -> False（深度在首个 ")" 处归零，不在末位）
            "x.length == 1" -> False
        """
        text = expr.strip()
        if len(text) < 2 or not text.startswith("(") or not text.endswith(")"):
            return False
        depth = 0
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index == len(text) - 1
        return False

    @classmethod
    def _strip_balanced_outer_parens(cls, expr: str) -> Optional[str]:
        """
        循环剥掉整体包裹的平衡外层括号。
        :param expr: 待剥壳表达式
        :return: 剥净后的内层文本；未发生剥壳（本就无整体包裹）时返回 None

        示例：
            "((x.length == 1 and weight.length == 1))" -> "x.length == 1 and weight.length == 1"（多重括号剥净）
            "x.length == 1" -> None（无整体包裹，未发生剥壳）
        """
        text = expr.strip()
        changed = False
        while cls._is_fully_wrapped(text):
            text = text[1:-1].strip()
            changed = True
        return text if changed else None

    @classmethod
    def _prune_bracketed_subclause(cls, atom: str,
                                   atom_valid: Callable[[str], bool]) -> Optional[str]:
        """
        对整体被括号包裹的原子子句剥壳后递归降级；无法降级时返回 None（由调用方回退整块摘除）。
        :param atom: 已被判定为不可表达的原子子句
        :param atom_valid: 原子子句可表达性判定函数
        :return: 已由一对括号包裹的降级串；无法降级时返回 None

        示例（atom_valid 为真实 Gate1 判定：y.shape[0] == 2 等下标形式不可表达，z.dtype == w.dtype 可表达）：
            "(y.shape[0] == 2 and z.dtype == w.dtype)" -> "(z.dtype == w.dtype)"（剥壳后 and 摘除，重新套回括号）
            "(y.shape[0] == 2)" -> None（剥壳后单原子仍不可表达，回退整块摘）
            "(transposeWeight.range_value or weight.shape[1] <= 65535)" -> None（or 某支剪空，回退整块摘）
        """
        inner = cls._strip_balanced_outer_parens(atom)
        if inner is None:
            return None
        degraded = cls._prune_invalid_subclauses(inner, atom_valid)
        if degraded is None:
            return None
        return degraded if cls._is_fully_wrapped(degraded) else f"({degraded})"

    @staticmethod
    def get_param_range_value_expr(param_name: str, param_type: str, dtype_list: List[str],
                                   range_value_profile: List[Any]):
        """
        根据dtype确认data_profile可以取的数据模型，构建表达式进行约束
        如："(x1.dtype in ['FLOAT32','FLOAT16','BFLOAT16'] and x1.range in ['PosNormal','PosInf','SubNormal']) or (x1.dtype in ['INT8','INT16'] and x1.range in ['Zero','NegNormal','NaN'])"
        Args:
            param_name: 参数名称
            param_type: 参数类型
            dtype_list: 参数支持的所有数据类型
            range_value_profile: 该参数支持的所有数据模型
        Returns: str表达式或None（无条件限制时）
        """
        float_dtype = []
        float_data_profile = []
        int_dtype = []
        int_data_profile = []
        for dtype in dtype_list:
            dtype_value = DataHandleUtil.data_dtype_map(data_type=param_type, data_ori_dtype=dtype)
            if dtype_value in ParamModelConfig.FLOAT_DTYPE:
                float_dtype.append(dtype)
            elif dtype_value in ParamModelConfig.INT_DTYPE:
                int_dtype.append(dtype)

        for range_value in range_value_profile:
            if range_value in ParamModelConfig.FLOAT_TENSOR_DATA_PROFILE:
                float_data_profile.append(range_value)
            elif range_value in ParamModelConfig.INT_TENSOR_DATA_PROFILE:
                int_data_profile.append(range_value)

        if float_dtype and float_data_profile and int_dtype and int_data_profile:
            float_expr = f"({param_name}.dtype in {float_dtype} and {param_name}.range_value in {float_data_profile})"
            int_expr = f"({param_name}.dtype in {int_dtype} and {param_name}.range_value in {int_data_profile})"
            final_expr = f"{float_expr} or {int_expr}"
            return final_expr
        return None

    @staticmethod
    def replace_shape_len_to_dimension(expr: str) -> str:
        """
        将表达式中的 len(xx.shape) 替换为 xx.dimension。
        维索引 ``x.shape[i]`` 在组合阶段无对应标量因子（shape 被抽象为不分维
        ``shape_property`` 加 ``dimension`` 秩），本方法不为其虚构映射，交由
        ``check_constraint_expr`` 的子句级降级摘除并记录 dropped 原因。
        :param expr: 待改写的约束表达式原文
        :return: 改写后的表达式
        """
        return re.sub(r"len\(([a-zA-Z_]\w*)\.shape\)", r"\1.dimension", expr)

    @classmethod
    def detect_unrepresentable_dim_index(cls, expr: str) -> List[str]:
        """
        返回表达式中所有不可表达的维索引引用（如 ``x1.shape``）。
        仅供降级原因落盘，便于核对与回溯，不改变表达式。
        :param expr: 待探测的约束表达式原文
        :return: 命中的张量名列表
        """
        return [m.group(1) for m in cls._DIM_INDEX_RE.finditer(expr)]

    @staticmethod
    def _propagate_equal_values(parameters: Dict, constraints: List[str]) -> None:
        """扫描约束中的 X.attr == Y.attr，将值域从已定义侧传播到空侧"""
        _EQ_PAIR_RE = re.compile(
            r"(?<![a-zA-Z_.])([a-zA-Z_]\w*\.[a-zA-Z_]\w*)\s*==\s*([a-zA-Z_]\w*\.[a-zA-Z_]\w*)")
        changed = True
        while changed:
            changed = False
            for constraint in constraints:
                for m in _EQ_PAIR_RE.finditer(constraint):
                    ref_a = m.group(1)
                    ref_b = m.group(2)
                    if ref_a == ref_b:
                        continue

                    pa, aa = ref_a.split(".", 1)
                    pb, ab = ref_b.split(".", 1)
                    ha = pa in parameters and aa in parameters[pa]
                    hb = pb in parameters and ab in parameters[pb]

                    va = parameters[pa][aa] if ha else []
                    vb = parameters[pb][ab] if hb else []

                    if va and not vb and hb:
                        parameters[pb][ab] = list(va)
                        changed = True
                    elif vb and not va and ha:
                        parameters[pa][aa] = list(vb)
                        changed = True

    @staticmethod
    def _replace_len_to_length(expr: str, param_dict: Dict[str, str],
                               valid_factor_refs: Optional[Set[str]] = None) -> str:
        def _replacer(m: re.Match) -> str:
            param = m.group(1)
            if param_dict.get(param) in ParamModelConfig.LIST_ATK_TYPE:
                factor_ref = f"{param}.length"
                if valid_factor_refs is None or factor_ref in valid_factor_refs:
                    return factor_ref
            return m.group(0)

        return re.sub(r"len\(([a-zA-Z_]\w*)\)", _replacer, expr)

    def propagate_constraint_values(self, parameters: Dict, param_type_dict: Dict[str, str]) -> None:
        """值域传播入口：转换原始约束 → 通过 == 关系传播值域 → 更新 parameters"""
        transformed_constraints = []
        for constraint in self.operator_rule_data.constraints_in_parameters:
            expr = CombinationConstraintGenerate._transform_presence_to_is_present(constraint.expr)
            expr = CombinationConstraintGenerate._replace_len_to_length(expr, param_type_dict)
            transformed_constraints.append(expr)

        CombinationConstraintGenerate._propagate_equal_values(parameters, transformed_constraints)

    def check_constraint_expr(self, param_type_dict: Dict[str, str],
                              parameters: Dict[str, Dict[str, List[Any]]] = None) -> List[str]:
        """
        检查有哪些表达式可以在组合生成的时候直接使用，用于筛选无效组合，保留有效组合
        :param param_type_dict: 参数类型
        :parameters: 参数取值集合
        Returns: List[str]
        """
        valid_expr_list = []
        dropped_records: List[DroppedRecord] = []
        valid_factor_refs: Set[str] = set()
        if parameters:
            for param_name, attrs in parameters.items():
                for attr_name, values in attrs.items():
                    if values:
                        valid_factor_refs.add(f"{param_name}.{attr_name}")

        for constraint in self.operator_rule_data.constraints_in_parameters:
            raw_expr = self._transform_presence_to_is_present(constraint.expr)
            raw_expr = CombinationConstraintGenerate._replace_len_to_length(raw_expr, param_type_dict,
                                                                            valid_factor_refs)
            raw_expr = CombinationConstraintGenerate._normalize_string_constants(raw_expr, parameters)
            if constraint.expr_type in ParamModelConfig.COMBINATION_USE_CONSTRAINT_TYPE:
                # 白名单类型直接放行（保持原语义）
                valid_expr_list.append(
                    CombinationConstraintGenerate.replace_shape_len_to_dimension(expr=raw_expr))
                continue

            if self.is_valid_combinatorial_constraint(raw_expr):
                candidate = raw_expr
            else:
                # 整条不过 -> 子句级降级：摘除不可表达原子子句，保留其余
                candidate = CombinationConstraintGenerate._prune_invalid_subclauses(
                    raw_expr, self.is_valid_combinatorial_constraint)
            if candidate is None:
                unknown_names = sorted(_collect_name_ids(raw_expr) - self._ALLOWED_NAMES)
                dim_refs = CombinationConstraintGenerate.detect_unrepresentable_dim_index(raw_expr)
                dropped_records.append(DroppedRecord(
                    expr=constraint.expr,
                    reason="clause pruned: unknown name / unrepresentable dim index, not safely prunable",
                    detail=";".join(
                        part for part in (
                            f"unknown_names={','.join(unknown_names)}" if unknown_names else "",
                            f"dim_refs={','.join(dim_refs)}" if dim_refs else "",
                        ) if part
                    ),
                ))
                continue

            valid_expr = CombinationConstraintGenerate.replace_shape_len_to_dimension(expr=candidate)
            if parameters and not self._is_semantically_valid_for_combination(
                    valid_expr, valid_factor_refs, parameters):
                # 整条不合语义 -> 子句级降级，保留合法子句
                valid_expr = self._prune_semantically_valid(valid_expr, valid_factor_refs, parameters)
                if valid_expr is None:
                    dropped_records.append(DroppedRecord(
                        expr=constraint.expr,
                        reason="gate2 semantic reject (factor-ref/numeric/literal), not safely prunable",
                    ))
                    continue
            valid_expr_list.append(valid_expr)
        return valid_expr_list

    def _is_semantically_valid_for_combination(
            self,
            expr: str,
            valid_factor_refs: Set[str],
            parameters: Dict,
    ) -> bool:
        """语义校验：因子存在性 → 数值运算符兼容性 → 常数值域交集"""
        if not CombinationConstraintGenerate._check_factor_refs_exist(expr, valid_factor_refs):
            return False
        if not CombinationConstraintGenerate._check_numeric_operator_compatibility(
                expr, valid_factor_refs, parameters):
            return False
        if not CombinationConstraintGenerate._check_literal_value_compatibility(
                expr, valid_factor_refs, parameters):
            return False
        return True

    def _prune_semantically_valid(self, expr: str, valid_factor_refs: Set[str],
                                  parameters: Dict) -> Optional[str]:
        """
        P3/P4/P5 共用入口：把 Gate2 三项检查下沉到原子子句级，摘除不合语义的子句。

        :param expr: 已改写为组合阶段因子口径的约束表达式
        :param valid_factor_refs: 组合阶段实际建模出的因子引用集合
        :param parameters: 参数字典，提供各因子值域用于语义判定
        :return: 降级后的表达式；无法安全降级时返回 ``None``
        """
        return CombinationConstraintGenerate._prune_invalid_subclauses(
            expr,
            lambda clause: self._is_semantically_valid_for_combination(
                CombinationConstraintGenerate.replace_shape_len_to_dimension(expr=clause),
                valid_factor_refs, parameters),
        )

    @staticmethod
    def _check_factor_refs_exist(expr: str, valid_factor_refs: Set[str]) -> bool:
        """提取表达式中所有 param.attr 引用，校验每个引用都在 valid_factor_refs 中存在"""
        refs: Set[str] = set()
        for m in CombinationConstraintGenerate._FACTOR_REF_RE.finditer(expr):
            prev = m.group("prev")
            attr = m.group("attr")
            if prev is not None:
                refs.add(f"{prev}.{attr}")
        for ref in refs:
            if ref not in valid_factor_refs:
                return False
        return True

    @classmethod
    def _check_numeric_operator_compatibility(
            cls, expr: str, valid_factor_refs: Set[str], parameters: Dict
    ) -> bool:
        """检查数值运算符 (< > >= <= + - * / % //) 是否作用于非数值的因子值"""
        transformed = cls._transform_expression(expr)
        try:
            tree = ast.parse(transformed, mode="eval")
        except SyntaxError:
            return False

        under_to_dot: Dict[str, str] = {}
        for ref in valid_factor_refs:
            under_to_dot[cls._dot_to_underscore(ref)] = ref

        compare_numeric_ops = {ast.Lt, ast.Gt, ast.LtE, ast.GtE}
        binop_numeric_ops = {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv}

        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and any(type(op) in compare_numeric_ops for op in node.ops):
                operands = [node.left] + list(node.comparators)
            elif isinstance(node, ast.BinOp) and type(node.op) in binop_numeric_ops:
                operands = [node.left, node.right]
            else:
                continue

            for operand in operands:
                if isinstance(operand, ast.Name) and operand.id in under_to_dot:
                    ref = under_to_dot[operand.id]
                    param, attr = ref.split(".", 1)
                    values = parameters.get(param, {}).get(attr, [])
                    if not values:
                        # 域为空 = 未知，不据此拒绝（保持保守放行）
                        continue
                    if all(not isinstance(v, (int, float)) or isinstance(v, bool) for v in values):
                        # 值域全为非数值（字符串/枚举/bool）-> 判定为「不可表达子句」
                        return False
        return True

    @classmethod
    def _check_literal_value_compatibility(
            cls, expr: str, valid_factor_refs: Set[str], parameters: Dict) -> bool:
        """检查 == <常量> 和 in [<常量列表>] 中常量与因子取值集合是否有交集"""
        transformed = cls._transform_expression(expr)
        try:
            tree = ast.parse(transformed, mode="eval")
        except SyntaxError:
            return False

        under_to_dot: Dict[str, str] = {}
        for ref in valid_factor_refs:
            under_to_dot[cls._dot_to_underscore(ref)] = ref

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1:
                continue
            op = node.ops[0]
            left, right = node.left, node.comparators[0]

            # 模式: factor == <常量>
            if isinstance(op, ast.Eq):
                ref, const_node = None, None
                if isinstance(left, ast.Name) and left.id in under_to_dot:
                    ref = under_to_dot[left.id]
                    const_node = right
                elif isinstance(right, ast.Name) and right.id in under_to_dot:
                    ref = under_to_dot[right.id]
                    const_node = left
                if ref and const_node:
                    constants = CombinationConstraintGenerate._extract_leaf_constants(const_node)
                    if constants and not CombinationConstraintGenerate._constants_match_values(
                            ref, constants, parameters):
                        return False

            # 模式: factor in [<常量列表>]
            elif isinstance(op, ast.In):
                if isinstance(left, ast.Name) and left.id in under_to_dot:
                    ref = under_to_dot[left.id]
                    if isinstance(right, (ast.List, ast.Tuple)):
                        constants = CombinationConstraintGenerate._extract_leaf_constants(right)
                        if constants and not CombinationConstraintGenerate._constants_match_values(
                                ref, constants, parameters):
                            return False

        return True

    @staticmethod
    def _extract_leaf_constants(node) -> List[Any]:
        """从 AST 节点提取叶子常量列表，包含非常量元素的列表返回空列表"""
        if isinstance(node, ast.Constant):
            return [node.value]
        if isinstance(node, (ast.List, ast.Tuple)):
            result = []
            for elt in node.elts:
                if isinstance(elt, ast.Constant):
                    result.append(elt.value)
                else:
                    return []
            return result
        return []

    @staticmethod
    def _constants_match_values(ref: str, constants: List[Any], parameters: Dict) -> bool:
        """
        检查常量列表中是否至少有一个值落在该因子的取值集合中。

        P5 增强：精确匹配失败后，再对字符串常量做一次小写归一匹配，
        对齐 ``_normalize_string_constants`` 的别名口径，消除大小写引起的「假无交集」。
        :param ref: 因子引用，形如 ``param.attr``
        :param constants: 约束中的字面量常量列表
        :param parameters: 参数字典，提供该因子的实际值域
        :return: 常量列表与实际值域是否存在交集
        """
        param, attr = ref.split(".", 1)
        values = parameters.get(param, {}).get(attr, [])
        for c in constants:
            if c in values:
                return True
        lowered = {str(v).lower() for v in values if isinstance(v, str)}
        for c in constants:
            if isinstance(c, str) and c.lower() in lowered:
                return True
        return False

    @staticmethod
    def _transform_presence_to_is_present(expr: str) -> str:
        """将 xxx is None → xxx.is_present == False, xxx is not None → xxx.is_present == True"""

        def _replacer(m: re.Match) -> str:
            param = m.group(1)
            is_not = m.group(2) is not None
            value = "True" if is_not else "False"
            return f"{param}.is_present == {value}"

        return re.sub(r"(\w+)\s+is\s+(not\s+)?None", _replacer, expr)

    @staticmethod
    def _normalize_string_constants(expr: str, parameters: Dict) -> str:
        if not parameters:
            return expr

        # Step 1: 从参数值构建 lower → actual 映射
        all_string_values: Dict[str, str] = {}
        for param_name, attrs in parameters.items():
            for attr_name, values in attrs.items():
                for v in values:
                    if isinstance(v, str):
                        all_string_values[v.lower()] = v

        # Step 2: 构建短名 → 候选全名列表（多对一安全；保序去重，消除 set 顺序依赖）
        short_to_full: Dict[str, List[str]] = {}
        for full_name, short_name in DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP.items():
            if isinstance(full_name, str) and isinstance(short_name, str):
                bucket = short_to_full.setdefault(short_name.lower(), [])
                if full_name not in bucket:
                    bucket.append(full_name)

        _QUOTED_STR_RE = re.compile(r"""('[^']+'|"[^"]+")""")

        def _replacer(m: re.Match) -> str:
            original = m.group(0)
            quote = original[0]
            inner = original[1:-1]
            lower = inner.lower()

            # ① 直接大小写匹配
            if lower in all_string_values and all_string_values[lower] != inner:
                return f"{quote}{all_string_values[lower]}{quote}"
            # ② dtype 别名匹配：从候选中确定性选择实际存在于参数中的全名
            #    P7：原实现遍历 set，顺序取决于 PYTHONHASHSEED，导致
            #        多候选（如 int64 -> INT64 / int64_t）结果摆动。
            #    改为「确定性排序 + 优先级」：
            #      (a) 与原文 inner 大小写一致者优先；
            #      (b) 其次按全名长度升序（更短更具体）；
            #      (c) 最后按字典序，保证完全确定。
            if lower in short_to_full:
                candidates = sorted(
                    short_to_full[lower],
                    key=lambda full_name: (
                        0 if full_name == inner else 1,
                        len(full_name),
                        full_name,
                    ),
                )
                for full_name in candidates:
                    full_lower = full_name.lower()
                    if full_lower in all_string_values:
                        return f"{quote}{all_string_values[full_lower]}{quote}"

            return original

        return _QUOTED_STR_RE.sub(_replacer, expr)

    def solve_dtype_support_description(self) -> tuple[str | None, List[str] | None]:
        # 这里对于dtype_support_description中的数据，不能随机选择一组作为全组合的数据，需要将此节点中的数据转换成表达式，
        #  加入constraint中
        dtype_support_description = self.operator_rule_data.dtype_support_description
        if dtype_support_description is None:
            return None, None
        dtype_support_constraints = []
        relation_param = []
        for dtype_support in dtype_support_description:
            dtype_constraint = []
            for param_name, dtype_value in dtype_support.items():
                constraint = f"{param_name}.dtype == '{dtype_value}'"
                dtype_constraint.append(constraint)
                relation_param.append(param_name)
            dtype_constraint_str = " and ".join(dtype_constraint)
            dtype_support_constraints.append(dtype_constraint_str)
        dtype_support_constraints_str = " or ".join(f"({item})" for item in dtype_support_constraints)
        return dtype_support_constraints_str, relation_param

    def solve_format_support_map(self) -> tuple[str | None, List[str] | None]:
        # 这里对于format_support_description中的数据，不能随机选择一组作为全组合的数据，需要将此节点中的数据转换成表达式，
        #  加入constraint中
        format_support_description = self.operator_rule_data.format_support_description
        if format_support_description is None:
            return None, None
        format_support_constraints = []
        relation_param = []
        for format_support in format_support_description:
            format_constraint = []
            for param_name, format_value in format_support.items():
                constraint = f"{param_name}.format == '{format_value}'"
                format_constraint.append(constraint)
                relation_param.append(param_name)
            format_constraint_str = " and ".join(format_constraint)
            format_support_constraints.append(format_constraint_str)
        format_support_constraints_str = " or ".join(f"({item})" for item in format_support_constraints)
        return format_support_constraints_str, relation_param
