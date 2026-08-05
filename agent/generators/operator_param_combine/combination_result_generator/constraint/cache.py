"""
Constraint AST Cache.

Module:
    M2 Constraint AST Adapter

Version:
    V1.0

Responsibility:
    Cache parsed constraint AST.
"""


import ast


from typing import Dict



class ConstraintCache:
    """
    Cache constraint AST.

    Cache target:

        expression -> AST

    Not:

        expression -> evaluation result
    """


    def __init__(self):

        self._cache: Dict[
            str,
            ast.AST
        ] = {}



    def get(
        self,
        expression: str,
    ) -> ast.AST | None:
        """
        Get cached AST.

        Returns:
            AST or None
        """


        return self._cache.get(
            expression
        )



    def put(
        self,
        expression: str,
        tree: ast.AST,
    ) -> None:
        """
        Store AST.
        """


        self._cache[
            expression
        ] = tree



    def contains(
        self,
        expression: str,
    ) -> bool:
        """
        Check cache exists.
        """


        return (
            expression
            in self._cache
        )



    def remove(
        self,
        expression: str,
    ) -> None:
        """
        Remove cache entry.
        """


        self._cache.pop(
            expression,
            None
        )



    def clear(
        self,
    ) -> None:
        """
        Clear all cache.
        """


        self._cache.clear()



    def size(
        self,
    ) -> int:
        """
        Return cache size.
        """


        return len(
            self._cache
        )