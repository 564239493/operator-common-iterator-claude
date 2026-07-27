"""
Coverage integration test.

Verify:

Constraint

    |

PairExistenceChecker

    |

PairUniverse

    |

CoverageTracker


"""


from typing import Dict, Any


from coverage import (
    Pair,
    PairUniverse,
    CoverageTracker,
    PairExistenceChecker,
)


from constraint.interfaces import (
    ConstraintProtocol,
)
from coverage.parameter import Factor
from coverage.value import FactorValue


class MockConstraint(
    ConstraintProtocol
):


    def evaluate(
        self,
        context: Dict[str, Any],
    ) -> bool:


        return (

            context["x"]["dtype"]
            ==
            "fp16"

        )



def create_pair(
    dtype: str,
) -> Pair:


    x_factor = Factor(
        parameter="x",
        attribute="dtype",
    )


    weight_factor = Factor(
        parameter="weight",
        attribute="dtype",
    )


    return Pair(

        left=FactorValue(
            factor=x_factor,
            value=dtype,
        ),


        right=FactorValue(
            factor=weight_factor,
            value="int8",
        ),

    )

class TestCoverageInterface:

    def test_full_coverage_flow(self):

        """
        Complete M3 workflow.
        """


        constraint = MockConstraint()


        checker = PairExistenceChecker(
            constraint
        )


        candidate_pairs = [

            create_pair(
                "fp16"
            ),

            create_pair(
                "fp32"
            ),

        ]


        valid_pairs = checker.filter_pairs(candidate_pairs)


        universe = PairUniverse(
            valid_pairs
        )


        tracker = CoverageTracker(
            universe
        )


        pairs = universe.get_pairs()


        assert len(
            pairs
        ) == 1


        tracker.mark_covered(
            pairs[0]
        )


        assert (
            tracker.coverage_rate()
            ==
            1.0
        )