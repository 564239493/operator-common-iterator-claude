import pytest

from agent.generators.operator_param_combine.combination_result_generator.constraint import FunctionRegistry, \
    FunctionAlreadyRegisteredError
from agent.generators.operator_param_combine.combination_result_generator.constraint import FunctionNotRegisteredError


class TestFunctionRegistry:

    def test_default_functions_exist(self):
        registry = FunctionRegistry()
        funcs = registry.list_functions()
        for name in ("len", "abs", "any", "all", "min", "max", "int", "float", "str", "bool"):
            assert name in funcs, f"{name} should be registered by default"

    def test_get_default_function(self):
        registry = FunctionRegistry()
        assert registry.get("len") is len
        assert registry.get("abs") is abs
        assert registry.get("int") is int

    def test_get_default_function_usable(self):
        registry = FunctionRegistry()
        assert registry.get("len")([1, 2, 3]) == 3
        assert registry.get("abs")(-5) == 5
        assert registry.get("bool")(1) is True

    def test_register_new_function(self):
        registry = FunctionRegistry()
        registry.register("custom_func", lambda x: x * 2)
        assert registry.exists("custom_func")
        assert registry.get("custom_func")(5) == 10

    def test_duplicate_default_raises(self):
        registry = FunctionRegistry()
        with pytest.raises(FunctionAlreadyRegisteredError):
            registry.register("len", len)

    def test_overwrite_default_allowed(self):
        registry = FunctionRegistry()
        registry.register("len", lambda: 42, allow_duplicate=True)
        assert registry.get("len")() == 42

    def test_function_not_found(self):
        registry = FunctionRegistry()
        with pytest.raises(FunctionNotRegisteredError):
            registry.get("unknown")


