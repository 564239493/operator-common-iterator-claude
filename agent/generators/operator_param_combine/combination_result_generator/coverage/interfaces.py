"""
Coverage public interfaces.

This module defines contracts exposed
to upper modules.

M4 Generator depends on these interfaces,
not concrete implementations.

"""


from typing import (
    Protocol,
    List,
)

from agent.generators.operator_param_combine.combination_result_generator.coverage import Pair


class PairProviderProtocol(Protocol):
    """
    Pair provider contract.


    Provides available pairs
    for generator.


    Implementations:

        PairUniverse

    """

    def get_pairs(
        self,
    ) -> List[Pair]:
        """
        Return all available pairs.
        """

        ...



class CoverageProtocol(Protocol):
    """
    Coverage tracking contract.
    """


    def mark_covered(
        self,
        pair: Pair,
    ) -> None:
        """
        Mark pair covered.
        """

        ...


    def coverage_rate(
        self,
    ) -> float:
        """
        Return coverage ratio.

        Example:

            0.95

        means:

            95% pair coverage.

        """

        ...

class PairCheckerProtocol(Protocol):
    """
    Pair validation contract.
    """


    def exists(
        self,
        pair: Pair,
    ) -> bool:
        """
        Check pair legality.
        """

        ...