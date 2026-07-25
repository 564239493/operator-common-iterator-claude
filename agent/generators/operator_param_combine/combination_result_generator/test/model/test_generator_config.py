from model.generator_config import (
    GeneratorConfig
)

from model.parameter_model import (
    ParameterModel
)


class TestGeneratorConfig:

    def test_config(self):

        config = GeneratorConfig(
            parameters={
                "x":
                ParameterModel(
                    name="x",
                    dtype=("fp16",)
                )
            },
            constraints=("x.dtype=='fp16'",)
        )


        assert (
            config.parameter_names() == ("x",)
        )