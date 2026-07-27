from __future__ import annotations

from abc import (
    ABC,
    abstractmethod,
)

from typing import Optional

from agent.generators.operator_param_combine.combination_result_generator.constraint.interfaces import ConstraintProtocol
from agent.generators.operator_param_combine.combination_result_generator.coverage import PairUniverse, CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.generator import GeneratorOptions
from agent.generators.operator_param_combine.combination_result_generator.generator.model import GenerationResult


class BaseGenerator(ABC):
    """
    Generator base class.
    """

    def __init__(
            self,
            universe: PairUniverse,
            coverage_tracker: CoverageTracker,
            constraint: Optional[
                ConstraintProtocol
            ],
            config: GeneratorOptions,
    ) -> None:
        self._universe = universe
        self._coverage_tracker = coverage_tracker
        self._constraint = constraint
        self._config = config

    def generate(self) -> GenerationResult:
        self.initialize()
        result = self.build()
        self.finalize()
        return result

    @abstractmethod
    def initialize(self) -> None:
        """
        Initialize generator.
        """
        raise NotImplementedError

    @abstractmethod
    def build(self) -> GenerationResult:
        """
        Generate suite.
        """
        raise NotImplementedError

    @abstractmethod
    def finalize(self) -> None:
        """
        Cleanup.
        """
        raise NotImplementedError

    def coverage_rate(self) -> float:
        """
        Current coverage.
        """
        return self._coverage_tracker.coverage_rate()
