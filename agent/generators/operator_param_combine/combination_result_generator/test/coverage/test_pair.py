from agent.generators.operator_param_combine.combination_result_generator.coverage.pair import Pair
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue


class TestPair:

    def test_create_pair(self):
        factor1 = Factor(
            "x",
            "dtype"
        )

        factor2 = Factor(
            "weight",
            "dimension"
        )

        pair = Pair(

            FactorValue(
                factor1,
                "fp16"
            ),

            FactorValue(
                factor2,
                2
            )
        )

        assert (
                pair.left.factor.name
                ==
                "weight.dimension"
                or
                pair.right.factor.name
                ==
                "weight.dimension"
        )

    def test_pair_normalization(self):
        f1 = Factor(
            "x",
            "dtype"
        )

        f2 = Factor(
            "weight",
            "dtype"
        )

        p1 = Pair(
            FactorValue(
                f1,
                "fp16"
            ),
            FactorValue(
                f2,
                "int8"
            )
        )

        p2 = Pair(
            FactorValue(
                f2,
                "int8"
            ),
            FactorValue(
                f1,
                "fp16"
            )
        )

        assert p1 == p2

    def test_pair_id_same(self):
        f1 = Factor(
            "x",
            "dtype"
        )

        f2 = Factor(
            "weight",
            "dtype"
        )

        p1 = Pair(
            FactorValue(
                f1,
                "fp16"
            ),
            FactorValue(
                f2,
                "int8"
            )
        )

        p2 = Pair(
            FactorValue(
                f2,
                "int8"
            ),
            FactorValue(
                f1,
                "fp16"
            )
        )

        assert (
                p1.pair_id
                ==
                p2.pair_id
        )