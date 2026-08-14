from agent.generators.operator_param_combine.combination_result_generator.generator.interfaces import (
    GeneratorProtocol,
)

class TestInterface:
    def test_protocol_exists(self):

        assert (
            GeneratorProtocol
            is not None
        )