from agent.generators.operator_param_combine.combination_result_generator.coverage.pair_builder import PairBuilder
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.universe import PairUniverse
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue


class TestPairBuilder:
    def test_pair_builder_count(self):
        builder = PairBuilder()
        weight_dtype_int8 = FactorValue(
            Factor(
                "weight",
                "dtype"
            ),
            "int8"
        )
        x_dtype_fp16 = FactorValue(
            Factor(
                "x",
                "dtype"
            ),
            "fp16"
        )

        x_dimension_2 = FactorValue(
            Factor(
                "x",
                "dimension"
            ),
            "2"
        )

        pairs = builder.build(
            [weight_dtype_int8, x_dtype_fp16, x_dimension_2],
        )

        assert len(pairs) == 3

    def test_pair_duplicate(self):
        universe = PairUniverse()
        builder = PairBuilder()
        weight_dtype_int8 = FactorValue(
            Factor(
                "weight",
                "dtype"
            ),
            "int8"
        )
        x_dtype_fp16 = FactorValue(
            Factor(
                "x",
                "dtype"
            ),
            "fp16"
        )

        x_dimension_2 = FactorValue(
            Factor(
                "x",
                "dimension"
            ),
            "2"
        )

        pairs = builder.build(
            [weight_dtype_int8, x_dtype_fp16, x_dimension_2],
        )
        pair1 = pairs[0]
        pair2 = pairs[0]

        universe.add(
            pair1
        )

        universe.add(
            pair2
        )

        assert (
                universe.size()
                ==
                1
        )

    def test_skip_same_factor_values(self):
        factor = Factor(
            parameter="x",
            attribute="dtype",
        )

        values = [
            FactorValue(
                factor=factor,
                value="fp16",
            ),
            FactorValue(
                factor=factor,
                value="fp32",
            ),
        ]

        pairs = PairBuilder().build(values)

        assert len(pairs) == 0

    def test_build_legal_pair(self):
        values = [
            FactorValue(
                Factor("x", "dtype"),
                "fp16",
            ),
            FactorValue(
                Factor("weight", "dtype"),
                "int8",
            ),
        ]

        pairs = PairBuilder().build(values)

        assert len(pairs) == 1

