"""
Constraint AST Adapter.

Module:
    M2 Constraint AST Adapter

Responsibility:
    Validate and adapt Python AST.

    Constraint String

        |
        v

    ConstraintParser

        |
        v

    Python AST

        |
        v

    ASTAdapter

        |
        +----------------+
        |                |
        v                v

Dependency        Evaluator
Analyzer

Version:
    V1.0
"""

import ast

from typing import Type

from agent.generators.operator_param_combine.combination_result_generator.constraint import UnsupportedASTNodeError


class ASTAdapter:
    """
    AST validation and access layer.

    This class DOES NOT:
        - execute AST
        - bind variables
        - create IR
    """

    SUPPORTED_NODES: tuple[Type[ast.AST],...] = (
        # root
        ast.Expression,

        # variable
        ast.Name,
        ast.Attribute,
        ast.Load,

        # value
        ast.Constant,

        # compare
        ast.Compare,

        ast.Eq,
        ast.NotEq,
        ast.Gt,
        ast.GtE,
        ast.Lt,
        ast.LtE,
        ast.In,
        ast.NotIn,

        # bool
        ast.BoolOp,
        ast.And,
        ast.Or,

        # unary
        ast.UnaryOp,
        ast.Not,
        ast.UAdd,
        ast.USub,

        # conditional
        ast.IfExp,

        # collection_literals
        ast.List,
        ast.Tuple,
        ast.Set,
        ast.Dict,

        # binary arithmetic
        ast.BinOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.FloorDiv,

        # function_call
        ast.Call
    )

    def validate(
            self,
            tree: ast.Expression,
    ) -> None:
        """
        Validate AST nodes.

        Raises:
            UnsupportedASTNodeError
        """

        for node in ast.walk(tree):
            if not isinstance(node,self.SUPPORTED_NODES):
                raise UnsupportedASTNodeError("Unsupported AST node: "f"{type(node).__name__}")

    def adapt(
            self,
            tree: ast.Expression,
    ) -> ast.Expression:
        """
        Validate and return AST.

        Current version keeps
        original AST object.

        No IR conversion.
        """

        self.validate(
            tree
        )

        return tree

    def get_attribute_path(
            self,
            node: ast.Attribute,
    ) -> str:
        """
        Convert Attribute AST node
        into dotted path.

        Example:

            x.dtype

        returns:

            x.dtype
        """

        parts = []
        current = node
        while isinstance(
                current,
                ast.Attribute,
        ):
            parts.append(
                current.attr
            )

            current = current.value

        if not isinstance(
                current,
                ast.Name,
        ):
            raise UnsupportedASTNodeError(
                "Attribute root must be variable"
            )

        parts.append(
            current.id
        )

        return ".".join(
            reversed(parts)
        )

    def get_variable_name(
            self,
            node: ast.Name,
    ) -> str:
        """
        Extract variable name.

        Example:

            x

        returns:

            x
        """

        if not isinstance(
                node,
                ast.Name,
        ):
            raise UnsupportedASTNodeError(
                "Node is not variable"
            )

        return node.id
