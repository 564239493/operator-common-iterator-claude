from typing import Protocol

from agent.generators.operator_param_combine.combination_result_generator.generator.model import GenerationResult


class GeneratorProtocol(
    Protocol
):
    """
    Generator interface.
    """

    def generate(
        self,
    ) -> GenerationResult:
        """
        Generate test suite.
        """
        ...