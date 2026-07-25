"""
Constraint Compiler.

Module:
    M2 Constraint AST Adapter

Version:
    V1.0

Responsibility:

    Compile constraint expression
    into executable constraint object.

"""

import ast

from dataclasses import dataclass

from agent.generators.operator_param_combine.combination_result_generator.constraint.ast_adapter import ASTAdapter
from agent.generators.operator_param_combine.combination_result_generator.constraint.ast_parser import ConstraintParser
from agent.generators.operator_param_combine.combination_result_generator.constraint.cache import ConstraintCache
from agent.generators.operator_param_combine.combination_result_generator.constraint.dependency import \
    DependencyAnalyzer
from agent.generators.common_utils.logger_util import LazyLogger

logger = LazyLogger()

@dataclass(
    frozen=True
)
class CompiledConstraint:
    """
    Compiled constraint object.

    Contains:

        original expression

        validated AST

        dependency metadata

    """

    expression: str

    tree: ast.Expression

    dependencies: frozenset[str]


class ConstraintCompiler:
    """
    Constraint compilation entry.

    Pipeline:
        Expression
            |
            v
        Cache
            |
            v
        Parser
            |
            v
        Adapter
            |
            v
        Dependency Analyzer
            |
            v
        CompiledConstraint

    """

    def __init__(
            self,
            parser: ConstraintParser | None = None,
            adapter: ASTAdapter | None = None,
            cache: ConstraintCache | None = None,
            dependency_analyzer:
            DependencyAnalyzer | None = None,
    ):
        self.parser = (
                parser
                or ConstraintParser()
        )

        self.adapter = (
                adapter
                or ASTAdapter()
        )

        self.cache = (
                cache
                or ConstraintCache()
        )

        self.dependency_analyzer = (
                dependency_analyzer
                or DependencyAnalyzer()
        )

    def compile(
            self,
            expression: str,
    ) -> CompiledConstraint:
        """
        Compile constraint.
        Args:
            expression:
                constraint string
        Returns:
            CompiledConstraint
        """
        logger.debug(f"Compiling constraint expression, expr : {expression}")
        tree = self.cache.get(expression)
        if tree is None:
            tree = self.parser.parse(expression)
            self.adapter.validate(tree)
            self.cache.put(expression, tree)
        dependencies = (self.dependency_analyzer.analyze(tree))
        logger.debug(f"[compiler] Compiling constraint expression end, expr : {expression}")
        return CompiledConstraint(expression=expression, tree=tree, dependencies=frozenset(dependencies))
