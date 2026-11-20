"""
Constraint AST Parser.

Module:
    M2 Constraint AST Adapter

Responsibility:
    Convert constraint expression string
    into Python AST.

Version:
    V1.0
"""


import ast

from agent.generators.common_utils.expression_analysis import ExpressionPreprocessor
from agent.generators.operator_param_combine.combination_result_generator.constraint import ConstraintSyntaxError


class ConstraintParser:
    """
    Parse constraint expression
    into Python AST.

    This class only handles parsing.

    AST validation belongs to ASTAdapter.
    """

    def __init__(self, param_names=None):
        # 真实参数名清单：用于兼容 Python 关键字参数名（如 from）。
        self.param_names = param_names


    def parse(
        self,
        expression: str,
    ) -> ast.Expression:
        """
        Parse constraint expression.

        Args:
            expression:
                Python constraint expression.

        Returns:
            ast.Expression

        Raises:
            ConstraintSyntaxError:
                Invalid syntax.
        """


        if not isinstance(
            expression,
            str,
        ):

            raise ConstraintSyntaxError(
                f"Constraint expression must be string, expr : '{expression}', expr type : '{type(expression)}'"
            )


        if not expression.strip():

            raise ConstraintSyntaxError(
                "Constraint expression cannot be empty"
            )


        try:

            tree = ExpressionPreprocessor.parse_expression(
                expression,
                self.param_names,
            )


        except SyntaxError as exc:

            raise ConstraintSyntaxError(
                (
                    "Invalid constraint syntax: "
                    f"{exc.msg}"
                )
            ) from exc


        return tree