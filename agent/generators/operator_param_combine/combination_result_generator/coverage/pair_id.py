"""
Pair ID Model.

M3-Step2
"""


from dataclasses import dataclass


@dataclass(
    frozen=True
)
class PairId:
    """
    Unique identifier of Pair.
    """

    value: int

    def __str__(self) -> str:
        return str(self.value)

    def __int__(self) -> int:
        return self.value

    def __index__(self) -> int:
        return self.value