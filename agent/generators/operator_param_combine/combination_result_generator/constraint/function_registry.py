"""
Constraint Function Registry.

Module:
    M2 Constraint AST Adapter

Version:
    V1.0

Responsibility:
    Manage allowed constraint functions.
"""

from typing import (
    Callable,
    Dict,
)

from agent.generators.operator_param_combine.combination_result_generator.constraint import \
    FunctionAlreadyRegisteredError, FunctionNotRegisteredError

_DEFAULT_FUNCTIONS: dict[str, Callable] = {
    "len": len,
    "abs": abs,
    "any": any,
    "all": all,
    "min": min,
    "max": max,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool
}


class FunctionRegistry:
    """
    Function whitelist registry.

    Only registered functions
    can be executed by Evaluator.
    """

    def __init__(self):
        self._functions: Dict[str,Callable] = {}
        for name, func in _DEFAULT_FUNCTIONS.items():
            self.register(name, func)

    def register(
            self,
            name: str,
            function: Callable,
            *,
            allow_duplicate: bool = False,
    ) -> None:
        """
        Register function.


        Args:

            name:
                Function name.

            function:
                Callable object.

            allow_duplicate:
                Allow overwrite.

        """

        if name in self._functions and not allow_duplicate:
            raise FunctionAlreadyRegisteredError("Function already registered: "f"{name}")
        self._functions[name] = function

    def get(
            self,
            name: str,
    ) -> Callable:
        """
        Get registered function.
        """

        if name not in self._functions:
            raise FunctionNotRegisteredError(
                (
                    "Function not registered: "
                    f"{name}"
                )
            )

        return self._functions[name]

    def exists(
            self,
            name: str,
    ) -> bool:
        """
        Check function exists.
        """

        return (
                name in self._functions
        )

    def remove(
            self,
            name: str,
    ) -> None:
        """
        Remove function.
        """

        if name in self._functions:
            del self._functions[name]

    def list_functions(
            self,
    ) -> list[str]:
        """
        Return registered names.
        """

        return list(
            self._functions.keys()
        )
