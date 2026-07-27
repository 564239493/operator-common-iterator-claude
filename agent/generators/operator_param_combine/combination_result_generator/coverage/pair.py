"""
Pair Model.

M3-Step2
"""

from dataclasses import dataclass

from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_id import PairId
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue


@dataclass(
    frozen=True
)
class Pair:
    """
    Two-way interaction pair.

    Example:

        x.dtype=fp16
        weight.dtype=int8

    """

    left: FactorValue

    right: FactorValue

    def __post_init__(self):
        if self.left == self.right:
            raise ValueError(
                "Pair cannot contain same FactorValue"
            )

        normalized = self.normalize()

        object.__setattr__(self, "left", normalized[0])
        object.__setattr__(self, "right", normalized[1])

    @property
    def pair_id(self) -> PairId:
        return PairId(hash(self))

    def normalize(self) -> tuple[FactorValue, FactorValue]:
        values = [self.left, self.right]
        values.sort(key=lambda x: self._sort_key(x))
        return (values[0], values[1])

    def _sort_key(self, value: FactorValue):
        return (
            value.factor.name,
            str(value.value)
        )

    def key(self) -> tuple:
        return (
            self.left,
            self.right
        )
