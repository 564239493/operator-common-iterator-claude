"""
Constraint Evaluator.

Module:
    M2 Constraint AST Adapter

Version:
    V1.0

Responsibility:
    Evaluate validated AST safely.
"""

import ast

from agent.generators.operator_param_combine.combination_result_generator.constraint import FunctionRegistry, \
    VariableBindingError, FunctionNotRegisteredError


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
        if isinstance(node.op, ast.And):
            for v in node.values:
                if not self.visit(v):
                    return False
            return True
        if isinstance(node.op, ast.Or):
            for v in node.values:
                if self.visit(v):
                    return True
            return False
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
            return not self.visit(node.operand)
        if isinstance(node.op, ast.USub):
            return -self.visit(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +self.visit(node.operand)
        raise NotImplementedError(f"UnaryOp {type(node.op).__name__}")
