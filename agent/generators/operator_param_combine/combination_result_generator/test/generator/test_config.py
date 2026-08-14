from agent.generators.operator_param_combine.combination_result_generator.generator.generator_options import (
    GeneratorOptions,
)

class TestConfig:
    
    def test_default_config(self):

        config = GeneratorOptions()

        assert config.strength == 2

        assert config.target_coverage == 1.0

        assert config.max_iterations == 10000

        assert config.random_seed is None


    def test_custom_config(self):

        config = GeneratorOptions(
            strength=3,
            target_coverage=0.95,
            max_iterations=5000,
            random_seed=123,
        )

        assert config.strength == 3

        assert config.target_coverage == 0.95

        assert config.max_iterations == 5000

        assert config.random_seed == 123


    def test_config_equality(self):

        config1 = GeneratorOptions(
            random_seed=1,
        )

        config2 = GeneratorOptions(
            random_seed=1,
        )

        assert config1 == config2