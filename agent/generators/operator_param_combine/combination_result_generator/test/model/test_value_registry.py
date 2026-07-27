from model.value_registry import (
    ValueRegistry
)


class TestValueRegistry:
    """
    验证注册，重复注册
    """

    def test_register_value(self):
        registry = ValueRegistry()
        value_id = registry.register(
            "fp16"
        )
        assert value_id == 0

    def test_duplicate_register(self):
        registry = ValueRegistry()
        id1 = registry.register(
            "fp16"
        )
        id2 = registry.register(
            "fp16"
        )
        assert id1 == id2

    def test_reverse_lookup(self):
        registry = ValueRegistry()
        registry.register(
            "fp16"
        )
        assert (
                registry.get_value(0) == "fp16"
        )
