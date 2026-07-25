import time

from model.variable_model import (
    VariableModel
)
from model.variable_registry import (
    VariableRegistry
)


def create_variable():
    return VariableModel(

        variable_id=0,

        parameter_name="x",

        attribute_name="dtype",

        values=("fp16", "fp32")

    )


class TestVariableRegistry:

    def test_register_variable(self):
        registry = VariableRegistry()

        vid = registry.register(
            create_variable()
        )

        assert vid == 0

    def test_query_by_name(self):
        registry = VariableRegistry()

        registry.register(
            create_variable()
        )

        variable = registry.get_by_name(
            "x.dtype"
        )

        assert variable.attribute_name == "dtype"

    def test_register_performance(self):

        t_start = time.time()

        for _ in range(10000):
            registry = VariableRegistry()
            registry.register(create_variable())

        t_end = time.time()

        assert t_end - t_start < 1

