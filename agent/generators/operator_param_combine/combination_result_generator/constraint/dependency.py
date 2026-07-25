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


    def analyze(
        self,
        tree: ast.AST,
    ) -> set[str]:
        """
        Analyze AST dependency.

        Args:
            tree:
                validated AST tree

        Returns:
            set[str]
        """


        self.dependencies.clear()
        self.visit(tree)
        return self.dependencies.copy()



    # ==================================================
    # AST Visitors
    # ==================================================


    def visit_Attribute(
        self,
        node: ast.Attribute,
    ):
        """
        Handle attribute access.
        Example:

            x.dtype

        """
        path = self.extract_attribute_path(node)
        self.dependencies.add(path)


        # Important:
        #
        # Do NOT visit child Name node.
        #
        # Otherwise:
        #
        # x.dtype
        #
        # becomes:
        #
        # x
        # x.dtype
        #
        return



    def visit_Name(
        self,
        node: ast.Name,
    ):
        """
        Handle standalone variable.
        Example:
            batch_size
        """
        self.dependencies.add(node.id)


    # ==================================================
    # Helpers
    # ==================================================


    def extract_attribute_path(
        self,
        node: ast.Attribute,
    ) -> str:
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
        while isinstance(current,ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current,ast.Name):
            raise UnsupportedASTNodeError("Attribute root must be variable")
        parts.append(current.id)
        return ".".join(reversed(parts))