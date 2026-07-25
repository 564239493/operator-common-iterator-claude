import pytest

from constraint.interfaces import ConstraintProtocol

from generator.backtracking import BacktrackingGenerator

from model.generator_config import GeneratorConfig
from model.parameter_model import ParameterModel


def create_config():
    x = ParameterModel(name="x", dtype=("fp16", "fp32"), dimension=(2, 4))
    weight = ParameterModel(name="weight", dtype=("int8", "int16"))
    return GeneratorConfig(parameters={"x": x, "weight": weight})


class AlwaysTrueConstraint:

    def evaluate(self, context) -> bool:
        return True


class AlwaysFalseConstraint:

    def evaluate(self, context) -> bool:
        return False


class XDtypeFp16Constraint:

    def evaluate(self, context) -> bool:
        return context["x"]["dtype"] == "fp16"


class TestBacktrackingGenerator:

    def test_init(self):
        config = create_config()
        gen = BacktrackingGenerator(config)
        assert gen is not None

    def test_init_with_constraint(self):
        config = create_config()
        gen = BacktrackingGenerator(config, constraint=AlwaysTrueConstraint())
        assert gen is not None

    def test_init_none_config_raises(self):
        with pytest.raises(ValueError):
            BacktrackingGenerator(None)

    def test_generate_no_constraint(self):
        config = create_config()
        gen = BacktrackingGenerator(config)
        result = gen.generate()
        assert result is not None
        assert "x" in result
        assert "weight" in result
        assert result["x"]["dtype"] in ("fp16", "fp32")
        assert result["x"]["dimension"] in (2, 4)
        assert result["weight"]["dtype"] in ("int8", "int16")

    def test_generate_with_always_true(self):
        config = create_config()
        gen = BacktrackingGenerator(config, constraint=AlwaysTrueConstraint())
        result = gen.generate()
        assert result is not None

    def test_generate_with_always_false(self):
        config = create_config()
        gen = BacktrackingGenerator(config, constraint=AlwaysFalseConstraint())
        result = gen.generate()
        assert result is None

    def test_generate_satisfies_constraint(self):
        config = create_config()
        gen = BacktrackingGenerator(config, constraint=XDtypeFp16Constraint())
        result = gen.generate()
        assert result is not None
        assert result["x"]["dtype"] == "fp16"

    def test_generate_returns_dict(self):
        config = create_config()
        gen = BacktrackingGenerator(config)
        result = gen.generate()
        assert isinstance(result, dict)

    def test_generate_result_has_all_parameters(self):
        config = create_config()
        gen = BacktrackingGenerator(config)
        result = gen.generate()
        assert "x" in result
        assert "weight" in result

    def test_generate_result_has_all_attributes(self):
        config = create_config()
        gen = BacktrackingGenerator(config)
        result = gen.generate()
        assert "dtype" in result["x"]
        assert "dimension" in result["x"]
        assert "dtype" in result["weight"]

    def test_generate_with_fixed_values(self):
        config = create_config()
        gen = BacktrackingGenerator(config)
        result = gen.generate(fixed_values={"x": {"dtype": "fp16"}})
        assert result is not None
        assert result["x"]["dtype"] == "fp16"

    def test_generate_with_all_fixed(self):
        config = create_config()
        gen = BacktrackingGenerator(config)
        fixed = {"x": {"dtype": "fp32", "dimension": 2}, "weight": {"dtype": "int16"}}
        result = gen.generate(fixed_values=fixed)
        assert result is not None
        assert result["x"]["dtype"] == "fp32"
        assert result["x"]["dimension"] == 2
        assert result["weight"]["dtype"] == "int16"

    def test_generate_single_parameter(self):
        config = GeneratorConfig(
            parameters={"x": ParameterModel(name="x", dtype=("fp16",))}
        )
        gen = BacktrackingGenerator(config)
        result = gen.generate()
        assert result is not None
        assert result["x"]["dtype"] == "fp16"

    def test_generate_empty_config_raises(self):
        with pytest.raises(Exception):
            GeneratorConfig(parameters={})

    def test_generate_with_max_depth_hit(self):
        config = create_config()
        gen = BacktrackingGenerator(config, constraint=AlwaysFalseConstraint(), max_depth=10)
        result = gen.generate()
        assert result is None

    def test_generate_deterministic(self):
        config = create_config()
        gen1 = BacktrackingGenerator(config)
        gen2 = BacktrackingGenerator(config)
        result1 = gen1.generate()
        result2 = gen2.generate()
        assert result1 == result2

    def test_generate_respects_domain(self):
        config = create_config()
        gen = BacktrackingGenerator(config)
        result = gen.generate()
        assert result["x"]["dtype"] in ("fp16", "fp32")
        assert result["x"]["dimension"] in (2, 4)
        assert result["weight"]["dtype"] in ("int8", "int16")

    def test_generate_constraint_satisfies_all(self):
        config = create_config()

        class PartialConstraint:
            def evaluate(self, context) -> bool:
                return context.get("x", {}).get("dtype") == "fp16"

        gen = BacktrackingGenerator(config, constraint=PartialConstraint())
        result = gen.generate()
        assert result is not None
        assert result["x"]["dtype"] == "fp16"

    def test_generate_constraint_false_no_solution(self):
        config = create_config()
        gen = BacktrackingGenerator(config, constraint=AlwaysFalseConstraint())
        result = gen.generate()
        assert result is None
