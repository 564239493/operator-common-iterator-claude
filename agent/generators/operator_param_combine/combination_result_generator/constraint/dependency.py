"""
Constraint Dependency Analyzer.

Module:
    M2 Constraint AST Adapter

Version:
    V1.0

Responsibility:
    Extract variable dependencies
    from validated Python AST.
"""

import ast

from agent.generators.operator_param_combine.combination_result_generator.constraint import UnsupportedASTNodeError


class DependencyAnalyzer(ast.NodeVisitor):
    """
    Extract constraint dependencies.
    Output:
        attribute-level dependency paths.
    Example:
        x.dtype == weight.dtype
    returns:
        {
            "x.dtype",
            "weight.dtype"
        }
    """

    def __init__(self) -> None:
        self.dependencies: set[str] = set()
        self._bound: set[str] = set()

    def analyze(self, tree: ast.AST) -> set[str]:
        """
        Analyze AST dependency.
        Args:
            tree:
                validated AST tree
        Returns:
            set[str]
        """
        self.dependencies.clear()
        self._bound.clear()
        self.visit(tree)
        return self.dependencies.copy()

    # ==================================================
    # AST Visitors
    # ==================================================

    def visit_Attribute(self, node: ast.Attribute):
        """
        Handle attribute access.
        Example:
            x.dtype
        """
        path = self.extract_attribute_path(node)
        root = path.split(".")[0]
        if root not in self._bound:
            self.dependencies.add(path)
        return

    def visit_Name(self, node: ast.Name):
        """
        Handle standalone variable.
        Example:
            batch_size
        """
        if node.id not in self._bound:
            self.dependencies.add(node.id)

    def visit_Call(self, node: ast.Call):
        """访问调用参数，但跳过函数名（不是变量依赖）。"""
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw)
        # 不访问 node.func —— 函数名不是变量依赖

    def visit_GeneratorExp(self, node: ast.GeneratorExp):
        """只收集迭代源依赖，跳过生成器循环变量。"""
        for gen in node.generators:
            # 迭代源在外部作用域（真实参数）
            self.visit(gen.iter)
            # 进入内部作用域：绑定循环变量
            saved = set(self._bound)
            self._collect_targets(gen.target, self._bound)
            for ifs_node in gen.ifs:
                self.visit(ifs_node)
            self.visit(node.elt)
            self._bound = saved

    @staticmethod
    def _collect_targets(target, bound: set) -> None:
        if isinstance(target, ast.Name):
            bound.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                DependencyAnalyzer._collect_targets(elt, bound)

    # ==================================================
    # Helpers
    # ==================================================

    def extract_attribute_path(self, node: ast.Attribute) -> str:
        """
        Convert Attribute AST
        into dotted path.
        Example:
            x.shape.rank
        returns:
            x.shape.rank
        """
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            raise UnsupportedASTNodeError("Attribute root must be variable")
        parts.append(current.id)
        return ".".join(reversed(parts))
