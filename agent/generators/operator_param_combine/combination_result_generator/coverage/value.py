"""
Factor Value Model.

M3-Step1
"""

from dataclasses import dataclass
from typing import Any

from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor


@dataclass(
    frozen=True
)
class FactorValue:
    """
    A value of a factor.

    Example:

        x.dtype=fp16

    """

    factor: Factor

    value: Any

    def __hash__(self):

        return hash((self.factor.name, self._hashable_value()))

    def _hashable_value(self):
        if isinstance(self.value, list):
            return tuple(self.value)
        if isinstance(self.value, dict):
            return tuple(sorted(self.value.items()))
        return self.value
