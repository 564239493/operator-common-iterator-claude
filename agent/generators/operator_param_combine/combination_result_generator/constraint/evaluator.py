"""
Constraint Evaluator.

Module:
    M2 Constraint AST Adapter

Version:
    V1.1

Responsibility:
    Evaluate validated AST safely.

M4（Kleene 三值缺席降级）：部分求值/缺席参数场景下，子式可能不可判定。
引入第三值 _UNDECIDABLE（哨兵对象）沿 And/Or/Not/IfExp 三值真值表传播，
顶层收到不可判定按「约束不可证伪 → 放行」处理；缺席参数的 is_present
存在性可判定（短路返回 False）；未填充属性用 UNFILLED 哨兵标记，
参与比较时抛 VariableBindingError 走降级路径。
"""

import ast

from agent.generators.operator_param_combine.combination_result_generator.constraint import FunctionRegistry, \
    VariableBindingError, FunctionNotRegisteredError
from agent.generators.operator_param_combine.combination_result_generator.constraint.exceptions import \
    _Unfilled as _UnfilledSentinel


class _Undecidable:
    """Kleene 三值逻辑的「不可判定」哨兵。

    部分求值（属性未填/参数缺席）下子式无法判真伪时，BoolOp/Not/IfExp 沿
    三值真值表传播本哨兵；顶层 evaluate 收到它时按「约束不可证伪 → 放行」
    处理（与 Z3 侧缺席子式替换 True 的化简意图一致）。
    """

    __slots__ = ()

    def __repr__(self):
        return "<undecidable>"


_UNDECIDABLE = _Undecidable()


class ConstraintEvaluator(ast.NodeVisitor):
    """
    Safe AST evaluator.

    No eval().
    No exec().
    No IR.
    """

    def __init__(self, function_registry: FunctionRegistry | None = None):
        self.function_registry = (function_registry or FunctionRegistry())
        self.context = {}

    def evaluate(self, tree: ast.Expression, context: dict) -> bool:
        """
        Evaluate expression.
        """
        self.context = context
        result = self.visit(tree.body)
        # Kleene 三值：不可判定（缺席/未填子式传播到顶层）→ 约束不可证伪 → 放行
        if result is _UNDECIDABLE:
            return True
        return bool(result)

    # ==================================================
    # Basic Nodes
    # ==================================================

    def visit_Constant(self, node: ast.Constant, ):
        return node.value

    def visit_Name(self, node: ast.Name):
        if node.id not in self.context:
            raise VariableBindingError(
                f"Variable not found: {node.id} — not bound in the evaluation context",
                param=node.id,
                cause="unbound",
            )
        return self.context[node.id]

    def visit_Attribute(self, node: ast.Attribute):
        value = self.visit(node.value)
        owner = node.value.id if isinstance(node.value, ast.Name) else None
        owner_hint = f"'{owner}'" if owner else "the owning expression"

        # M4 根修：缺席参数（映射 None）的存在性是可判定信息——
        # `P.is_present` 直接返回 False，不抛异常。这使 `P.is_present == True`
        # 在缺席行上正确求值为 False（进而 And 短路、not(...) 放行），
        # 消除「缺席参数的存在性比较被当作不可判定」造成的误杀。
        if value is None and node.attr == "is_present":
            return False

        # 三种失败原因后果相同（本属性取不到），但根因完全不同，必须写清楚，
        # 否则日志只留一句 "Attribute not found: dtype"，会被误判成属性名拼写
        # 或建模缺陷，实际最常见的是参数缺席被映射成了 None。
        # 消息保持短：同一批次会对多条约束重复出现，冗长解释会淹没日志本身。
        if isinstance(value, dict):
            if node.attr in value:
                return value[node.attr]
            raise VariableBindingError(
                f"Attribute not found: {node.attr} — {owner_hint} is a dict without this key"
                f" (available keys: {sorted(str(k) for k in value)})",
                param=owner,
                attr=node.attr,
                cause="missing_key",
            )

        if value is None:
            raise VariableBindingError(
                f"Attribute not found: {node.attr} — {owner_hint} is None "
                f"(parameter absent in this row, so its attributes are unreadable)",
                param=owner,
                attr=node.attr,
                cause="absent",
            )

        try:
            return getattr(value, node.attr)
        except AttributeError as exc:
            raise VariableBindingError(
                f"Attribute not found: {node.attr} — {owner_hint} is a {type(value).__name__} "
                f"object without this attribute",
                param=owner,
                attr=node.attr,
                cause="missing_attr",
            ) from exc

    # ==================================================
    # Compare
    # ==================================================

    def visit_Compare(self, node: ast.Compare):
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            if not self.compare(op, left, right):
                return False
            left = right
        return True

    def compare(self, op, left, right):
        # M4：UNFILLED 哨兵（部分求值中尚未填充的属性）参与比较时视为
        # 不可判定——抛 VariableBindingError 走缺席降级路径（And 放行/Or 跳过），
        # 而非用哨兵对象参与 ==/in 等运算得出假性 False。
        if isinstance(left, _UnfilledSentinel) or isinstance(right, _UnfilledSentinel):
            raise VariableBindingError(
                "Attribute value is UNFILLED (partial evaluation) — "
                "comparison undecidable, degraded per absent-subexpr policy",
                cause="unfilled",
            )
        operators = {
            ast.Eq:
                lambda a, b: a == b,
            ast.NotEq:
                lambda a, b: a != b,
            ast.Gt:
                lambda a, b: a > b,
            ast.GtE:
                lambda a, b: a >= b,
            ast.Lt:
                lambda a, b: a < b,
            ast.LtE:
                lambda a, b: a <= b,
            ast.In:
                lambda a, b: a in b,
            ast.NotIn:
                lambda a, b: a not in b,
        }

        for cls, func in operators.items():
            if isinstance(op, cls):
                return func(left, right)
        raise NotImplementedError(type(op))

    # ==================================================
    # Boolean
    # ==================================================

    def visit_BoolOp(self, node: ast.BoolOp):
        # M4：子式级缺席降级（与 Z3 侧 remove_missing_param_exprs 语义对齐），
        # 三值（Kleene）语义——"不可判定"是一等公民：
        # - And：假子式 → False；真子式不约束；不可判定子式挂起。
        #   无假且有挂起 → 不可判定；全真 → True。
        # - Or：真子式 → True；假子式不贡献；不可判定子式挂起。
        #   有真 → True；无真且有挂起 → 不可判定；全假 → False。
        # - 不可判定（_UNDECIDABLE）向上传播，由 Not（取反后仍不可判定）、
        #   IfExp（条件不可判定 → 不可判定）与顶层 evaluate（放行）各自处理。
        #   二值降级（And 缺席按 True / Or 缺席按 False 再取反）在
        #   not(A真 and 缺席...) 形态下必然错杀（gmmV5 400 轮 0 行实证）。
        if isinstance(node.op, ast.And):
            undecidable = 0
            for v in node.values:
                try:
                    value = self.visit(v)
                except VariableBindingError:
                    undecidable += 1
                    continue
                if value is _UNDECIDABLE:
                    undecidable += 1
                    continue
                if not value:
                    return False
            return _UNDECIDABLE if undecidable else True
        if isinstance(node.op, ast.Or):
            undecidable = 0
            for v in node.values:
                try:
                    value = self.visit(v)
                except VariableBindingError:
                    undecidable += 1
                    continue
                if value is _UNDECIDABLE:
                    undecidable += 1
                    continue
                if value:
                    return True
            return _UNDECIDABLE if undecidable else False
        raise NotImplementedError(type(node.op))

    # ==================================================
    # Function Call
    # ==================================================

    def visit_Call(self, node: ast.Call):
        if not isinstance(node.func, ast.Name):
            raise FunctionNotRegisteredError("Only named functions supported")
        func = (self.function_registry.get(node.func.id))
        args = [self.visit(arg) for arg in node.args]
        return func(*args)

    # ==================================================
    # If Expression
    # ==================================================

    def visit_IfExp(self, node: ast.IfExp):
        condition = self.visit(node.test)
        # Kleene 三值：条件不可判定 → 整体不可判定（上抛由顶层放行）
        if condition is _UNDECIDABLE:
            return _UNDECIDABLE
        if condition:
            return self.visit(node.body)
        return self.visit(node.orelse)

    def visit_List(self, node: ast.List):
        return [self.visit(elt) for elt in node.elts]

    def visit_Tuple(self, node: ast.Tuple):
        return tuple(self.visit(elt) for elt in node.elts)

    def visit_Set(self, node: ast.Set):
        return {self.visit(elt) for elt in node.elts}

    def visit_Dict(self, node: ast.Dict):
        return {self.visit(k): self.visit(v) for k, v in zip(node.keys, node.values)}

    def visit_BinOp(self, node: ast.BinOp):
        left = self.visit(node.left)
        right = self.visit(node.right)
        op_map = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b,
            ast.FloorDiv: lambda a, b: a // b,
            ast.Mod: lambda a, b: a % b,
        }
        for cls, fn in op_map.items():
            if isinstance(node.op, cls):
                return fn(left, right)
        raise NotImplementedError(f"BinOp {type(node.op).__name__}")

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            operand = self.visit(node.operand)
            # Kleene 三值：not(不可判定) = 不可判定（顶层按放行处理）
            if operand is _UNDECIDABLE:
                return _UNDECIDABLE
            return not operand
        if isinstance(node.op, ast.USub):
            return -self.visit(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +self.visit(node.operand)
        raise NotImplementedError(f"UnaryOp {type(node.op).__name__}")
