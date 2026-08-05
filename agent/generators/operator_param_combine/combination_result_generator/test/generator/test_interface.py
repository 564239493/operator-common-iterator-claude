from generator.interfaces import (
    GeneratorProtocol,
)

class TestInterface:
    def test_protocol_exists(self):

        assert (
            GeneratorProtocol
            is not None
        )