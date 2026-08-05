from coverage.coverage import CoverageTracker
from coverage.pair_builder import PairBuilder
from coverage.parameter import Factor
from coverage.universe import PairUniverse
from coverage.value import FactorValue


class TestCoverage:
    def test_coverage_init(self):
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

        y_dimension_3 = FactorValue(
            Factor(
                "y",
                "dimension"
            ),
            "3"
        )

        x_shape_has1 = FactorValue(
            Factor(
                "x",
                "shape"
            ),
            "has1"
        )
        x_is_present_true = FactorValue(
            Factor(
                "x",
                "is_present"
            ),
            "true"
        )

        pairs = builder.build(
            [weight_dtype_int8, x_dtype_fp16, x_dimension_2, x_shape_has1, x_is_present_true, y_dimension_3],
        )
        universe = PairUniverse(
            pairs
        )

        tracker = CoverageTracker(
            universe
        )

        assert (
                tracker.covered_count()
                ==
                0
        )

    def test_mark_covered(self):
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

        y_dimension_3 = FactorValue(
            Factor(
                "y",
                "dimension"
            ),
            "3"
        )

        x_shape_has1 = FactorValue(
            Factor(
                "x",
                "shape"
            ),
            "has1"
        )
        x_is_present_true = FactorValue(
            Factor(
                "x",
                "is_present"
            ),
            "true"
        )

        pairs = builder.build(
            [weight_dtype_int8, x_dtype_fp16, x_dimension_2, x_shape_has1, x_is_present_true, y_dimension_3],
        )
        universe = PairUniverse(
            pairs
        )

        pair = pairs[0]

        tracker = CoverageTracker(
            universe
        )
        tracker.mark_covered(
            pair
        )

        assert (
                tracker.covered_count()
                ==
                1
        )

    def test_coverage_rate(self):
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

        y_dimension_3 = FactorValue(
            Factor(
                "y",
                "dimension"
            ),
            "3"
        )

        x_shape_has1 = FactorValue(
            Factor(
                "x",
                "shape"
            ),
            "has1"
        )
        # x_is_present_true = FactorValue(
        #     Factor(
        #         "x",
        #         "is_present"
        #     ),
        #     "true"
        # )
        #
        # y_is_present_true = FactorValue(
        #     Factor(
        #         "y",
        #         "is_present"
        #     ),
        #     "true"
        # )

        pairs = builder.build(
            [weight_dtype_int8, x_dtype_fp16, x_dimension_2, x_shape_has1, y_dimension_3],
        )
        universe = PairUniverse(
            pairs
        )

        tracker = CoverageTracker(
            universe
        )
        for pair_index in range(5):
            pair = pairs[pair_index]
            tracker.mark_covered(
                pair
            )
        assert (
                tracker.coverage_rate()
                ==
                0.5
        )

    def test_uncoverage(self):
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

        y_dimension_3 = FactorValue(
            Factor(
                "y",
                "dimension"
            ),
            "3"
        )

        x_shape_has1 = FactorValue(
            Factor(
                "x",
                "shape"
            ),
            "has1"
        )
        # x_is_present_true = FactorValue(
        #     Factor(
        #         "x",
        #         "is_present"
        #     ),
        #     "true"
        # )
        #
        # y_is_present_true = FactorValue(
        #     Factor(
        #         "y",
        #         "is_present"
        #     ),
        #     "true"
        # )

        pairs = builder.build(
            [weight_dtype_int8, x_dtype_fp16, x_dimension_2, x_shape_has1, y_dimension_3],
        )
        universe = PairUniverse(
            pairs
        )

        tracker = CoverageTracker(
            universe
        )
        for pair_index in range(5):
            pair = pairs[pair_index]
            tracker.mark_covered(
                pair
            )
        assert (
                tracker.uncovered_count()
                ==
                5
        )
        uncover_pair = pairs[5]

        assert (
                uncover_pair.pair_id in tracker.uncovered_pairs()
        )