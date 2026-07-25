"""
Constraint Public Interfaces.

This module defines the public contracts
between Constraint subsystem and external modules.

Design principle:

    External modules depend on interfaces,
    NOT concrete implementations.

Example:

    M3 Coverage
          |
          v
    ConstraintProtocol

          ^
          |
    CompiledConstraint (M2)

"""

from typing import (
    Any,
    Dict,
    Protocol,
    runtime_checkable,
)


@runtime_checkable
class ConstraintProtocol(Protocol):
    """
    Constraint execution contract.

    A constraint represents a compiled
    executable validation rule.

    M3/M4 modules only know that:

        constraint can be evaluated

    They do not know:
        - AST
        - Parser
        - Compiler
        - Expression language
    """

    def evaluate(self, context: Dict[str, Any]) -> bool:
        """
        Evaluate constraint.
        Args:
            context:
                Runtime variable context.
                Example:
                {
                    "x":
                    {
                        "dtype":"fp16"
                    }
                }
        Returns:
            bool:
                True:
                    constraint satisfied
                False:
                    constraint violated
        """
        ...


@runtime_checkable
class ConstraintEvaluatorProtocol(Protocol):
    """
    Constraint evaluator contract.
    Provides a unified execution entry.
    Implemented by:
        M2 ConstraintEvaluator
    Used by:
        M3 PairExistenceChecker
    """

    def evaluate(self, constraint: ConstraintProtocol, context: Dict[str, Any]) -> bool:
        """
        Execute constraint evaluation.
        Args:
            constraint:
                Constraint object
                implementing ConstraintProtocol
            context:
                Runtime evaluation context
        Returns:
            bool:
                True if constraint passes
        """
        ...


class ConstraintCompilerProtocol(Protocol):
    """
    Constraint compiler contract.
    Responsible for converting:
        source expression
            |
            v
        executable constraint
    Example:
        "x.dtype == 'fp16'"
            ->
        CompiledConstraint
    """

    def compile(self, expression: str) -> ConstraintProtocol:
        """
        Compile constraint expression.
        Args:
            expression:
                Constraint source expression.
        Returns:
            ConstraintProtocol:
                executable constraint
        """
        ...


class ConstraintContextProtocol(Protocol):
    """
    Runtime context contract.

    Used to describe objects
    accessible during constraint evaluation.
    Example:
        {
            "x":
            {
                "dtype":"fp16"
            }
        }

    """

    def get(self, name: str) -> Any:
        """
        Get runtime variable.
        """
        ...
