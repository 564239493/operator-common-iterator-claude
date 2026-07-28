import ast
import re
from typing import List, Set, Optional, Any, Dict

from agent.generators import OperatorRule
from agent.generators.data_definition.constants import ParamModelConfig, DataMatchMap


class CombinationConstraintGenerate:
    _FACTOR_REF_RE = re.compile(r"(?<![a-zA-Z_])($|(?P<prev>[a-zA-Z_]\w*))\.(?P<attr>[a-zA-Z_]\w*)")

    def __init__(self, operator_rule_data: OperatorRule):
        self.operator_rule_data = operator_rule_data
        self._ALLOWED_BUILTINS = {
            "abs": abs,
            "any": any,
            "all": all,
            "min": min,
            "max": max,
            "len": len,
            "True": True,
            "False": False,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
        }
        self._ALLOWED_NAMES: Set[str] = {
            "abs", "any", "all", "min", "max", "len",
            "True", "False", "None",
            "int", "float", "str", "bool",
        }

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
                _allowed_callables: Set[str] = self._ALLOWED_NAMES - {"True", "False", "None"}
                if isinstance(func, ast.Name) and func.id not in _allowed_callables:
                    return False

        return True

    @staticmethod
    def get_param_range_value_expr(param_name: str, dtype_list: List[str], range_value_profile: List[Any]):
        """
        根据dtype确认data_profile可以取的数据模型，构建表达式进行约束
        如："(x1.dtype in ['FLOAT32','FLOAT16','BFLOAT16'] and x1.range in ['PosNormal','PosInf','SubNormal']) or (x1.dtype in ['INT8','INT16'] and x1.range in ['Zero','NegNormal','NaN'])"
        Args:
            param_name: 参数名称
            dtype_list: 参数支持的所有数据类型
            range_value_profile: 该参数支持的所有数据模型
        Returns: str表达式或None（无条件限制时）
        """
        float_dtype = []
        float_data_profile = []
        int_dtype = []
        int_data_profile = []
        for dtype in dtype_list:
            dtype_value = DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP.get(dtype)
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
        """将表达式中的 len(xx.shape) 替换为 xx.dimension。"""
        return re.sub(r"len\(([a-zA-Z_]\w*)\.shape\)", r"\1.dimension", expr)

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
            is_valid = (
                    constraint.expr_type in ParamModelConfig.COMBINATION_USE_CONSTRAINT_TYPE
                    or self.is_valid_combinatorial_constraint(raw_expr))
            if not is_valid:
                continue
            valid_expr = CombinationConstraintGenerate.replace_shape_len_to_dimension(expr=raw_expr)
            if parameters and not self._is_semantically_valid_for_combination(
                    valid_expr, valid_factor_refs, parameters):
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
                    if values and all(not isinstance(v, (int, float)) for v in values):
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
        """检查常量列表中是否至少有一个值在因子取值集合中"""
        param, attr = ref.split(".", 1)
        values = parameters.get(param, {}).get(attr, [])
        for c in constants:
            if c in values:
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

        # Step 2: 构建短名 → 候选全名集合（多对一安全）
        short_to_full: Dict[str, Set[str]] = {}
        for full_name, short_name in DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP.items():
            if isinstance(full_name, str) and isinstance(short_name, str):
                short_to_full.setdefault(short_name.lower(), set()).add(full_name)

        _QUOTED_STR_RE = re.compile(r"""('[^']+'|"[^"]+")""")

        def _replacer(m: re.Match) -> str:
            original = m.group(0)
            quote = original[0]
            inner = original[1:-1]
            lower = inner.lower()

            # ① 直接大小写匹配
            if lower in all_string_values and all_string_values[lower] != inner:
                return f"{quote}{all_string_values[lower]}{quote}"

            # ② dtype 别名匹配：从候选集中选择实际存在于参数中的全名
            if lower in short_to_full:
                for full_name in short_to_full[lower]:
                    full_lower = full_name.lower()
                    if full_lower in all_string_values:
                        return f"{quote}{all_string_values[full_lower]}{quote}"

            return original

        return _QUOTED_STR_RE.sub(_replacer, expr)

    def solve_dtype_support_description(self) -> List[str]:
        # 这里对于dtype_support_description中的数据，不能随机选择一组作为全组合的数据，需要将此节点中的数据转换成表达式，
        #  加入constraint中
        dtype_support_description = self.operator_rule_data.dtype_support_description
        if dtype_support_description is None:
            return []
        dtype_support_constraints = []
        for dtype_support in dtype_support_description:
            dtype_constraint = []
            for param_name, dtype_value in dtype_support.items():
                constraint = f"{param_name}.dtype == {dtype_value}"
                dtype_constraint.append(constraint)
            dtype_constraint_str = " and ".join(dtype_constraint)
            dtype_support_constraints.append(dtype_constraint_str)
        return dtype_support_constraints


    def solve_format_support_map(self) -> List[str]:
        # 这里对于format_support_description中的数据，不能随机选择一组作为全组合的数据，需要将此节点中的数据转换成表达式，
        #  加入constraint中
        format_support_description = self.operator_rule_data.format_support_description
        if format_support_description is None:
            return []
        format_support_constraints = []
        for format_support in format_support_description:
            format_constraint = []
            for param_name, format_value in format_support.items():
                constraint = f"{param_name}.format == {format_value}"
                format_constraint.append(constraint)
            format_constraint_str = " and ".join(format_constraint)
            format_support_constraints.append(format_constraint_str)
        return format_support_constraints
