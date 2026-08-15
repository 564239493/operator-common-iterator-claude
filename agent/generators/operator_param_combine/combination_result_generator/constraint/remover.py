"""
Sub-Expression Remover.

Module:
    M2 Constraint AST Adapter (standalone utility)

Version:
    V1.0

Responsibility:
    移除约束表达式中引用不存在参数的子表达式。

独立接口，不与现有编译管线混用：

    输入：原始表达式字符串 + 已存在参数集合
    输出：移除不存在参数相关子表达式后的字符串

    from agent.generators.operator_param_combine.combination_result_generator.constraint.remover import (
        remove_missing_param_exprs,
    )

    expr = (
        "(x.dtype == 'fp32' and weight.dtype == 'fp32' and biasOptional.dtype == 'fp32' "
        "and groupListOptional.dtype == 'int64' and out.dtype == 'fp32') "
        "or (x.dtype == 'fp16' and weight.dtype == 'fp16' and biasOptional.dtype == 'fp16' "
        "and groupListOptional.dtype == 'int64' and out.dtype == 'fp16')"
    )

    existing = {"x", "weight", "groupListOptional", "out"}   # biasOptional 不存在

    result = remove_missing_param_exprs(expr, existing)
    # result 为不含 biasOptional 的表达式字符串

语义规则:
    - 空结果（所有子式都被移除）          -> "True"
    - ``param is None``（参数存在）       -> False
    - ``param is None``（参数缺失）       -> True
    - ``param is not None``（参数存在）   -> True
    - ``param is not None``（参数缺失）   -> False
    - 其他引用缺失参数的子表达式           -> True（空泛满足）
"""

import ast

from agent.generators.operator_param_combine.combination_result_generator.constraint.dependency import \
    DependencyAnalyzer


def _params_of(node: ast.AST) -> set[str]:
    """提取子树引用的参数名集合（复用 DependencyAnalyzer）。"""
    analyzer = DependencyAnalyzer()
    deps = analyzer.analyze(node)
    return {d.split(".")[0] for d in deps}


class _SubExprRemover(ast.NodeTransformer):
    """移除引用不存在参数的子表达式，并做布尔化简。"""

    def __init__(self, existing_params: frozenset) -> None:
        self.existing_params = existing_params

    def _references_missing(self, node: ast.AST) -> bool:
        params = _params_of(node)
        return any(p not in self.existing_params for p in params)

    def visit_Compare(self, node: ast.Compare):
        # 存在性检查（is None / is not None）统一按 existing_params 判定，
        # 不区分参数是否存在。
        if len(node.ops) == 1:
            op = node.ops[0]
            comp = node.comparators[0]
            if isinstance(comp, ast.Constant) and comp.value is None:
                if isinstance(op, ast.Is) or isinstance(op, ast.IsNot):
                    param_exists = not self._references_missing(node.left)
                    if isinstance(op, ast.Is):
                        # param is None: 存在 → False，缺失 → True
                        return ast.copy_location(ast.Constant(value=not param_exists), node)
                    # param is not None: 存在 → True，缺失 → False
                    return ast.copy_location(ast.Constant(value=param_exists), node)

        # 非存在性检查：参数缺失 → True（空泛满足），存在 → 原样保留
        if not self._references_missing(node):
            return node
        return ast.copy_location(ast.Constant(value=True), node)

    def visit_BoolOp(self, node: ast.BoolOp):
        self.generic_visit(node)
        values = node.values

        if isinstance(node.op, ast.And):
            if any(isinstance(v, ast.Constant) and v.value is False for v in values):
                return ast.copy_location(ast.Constant(value=False), node)
            values = [
                v for v in values
                if not (isinstance(v, ast.Constant) and v.value is True)
            ]
        else:  # Or
            if any(isinstance(v, ast.Constant) and v.value is True for v in values):
                return ast.copy_location(ast.Constant(value=True), node)
            values = [
                v for v in values
                if not (isinstance(v, ast.Constant) and v.value is False)
            ]

        if not values:
            return ast.copy_location(ast.Constant(value=True), node)
        if len(values) == 1:
            return values[0]

        node.values = values
        return node

    def visit_IfExp(self, node: ast.IfExp):
        if self._references_missing(node.test):
            return self.visit(node.body)
        self.generic_visit(node)
        return node

    def visit_Call(self, node: ast.Call):
        if self._references_missing(node):
            return ast.copy_location(ast.Constant(value=True), node)
        self.generic_visit(node)
        return node

    def visit_GeneratorExp(self, node: ast.GeneratorExp):
        if self._references_missing(node):
            return ast.copy_location(ast.Constant(value=True), node)
        return node


def remove_missing_param_exprs(expression: str, existing_params: set[str]) -> str:
    """
    移除约束表达式中引用不存在参数的子表达式。

    Args:
        expression:
            原始约束表达式字符串。
        existing_params:
            已存在的参数名集合。

    Returns:
        移除不存在参数相关子表达式后的字符串。
    """
    if expression is None or expression.strip() == "" or existing_params is None:
        return expression
    tree = ast.parse(expression, mode="eval")
    remover = _SubExprRemover(frozenset(existing_params))
    new_tree = remover.visit(tree)
    return ast.unparse(new_tree)
