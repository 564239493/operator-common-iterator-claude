"""
PairExistenceChecker tests.

M3-Step5.

Verify:

    Pair
      |
      v
    Context
      |
      v
    ConstraintProtocol.evaluate()
      |
      v
    valid / invalid


Design:

    M3 depends only on ConstraintProtocol.

    No dependency on:
        - ConstraintEvaluator
        - Compiler
        - Parser

"""

from typing import Any, Dict

import pytest

from coverage.pair_existence_checker import (
    PairExistenceChecker,
)

from constraint.interfaces import (
    ConstraintProtocol,
)

from coverage.pair import Pair
from coverage.parameter import Factor
from coverage.value import FactorValue


# ============================================================
# Mock Constraint
# ============================================================


class MockDtypeConstraint(
    ConstraintProtocol
):
    """
    Mock constraint.

    Equivalent expression:

        x.dtype == "fp16"

    """

    def evaluate(
            self,
            context: Dict[str, Any],
    ) -> bool:
        return (
                context["x"]["dtype"]
                ==
                "fp16"
        )


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def constraint(
) -> MockDtypeConstraint:
    return MockDtypeConstraint()


@pytest.fixture
def checker(
        constraint: MockDtypeConstraint,
) -> PairExistenceChecker:
    return PairExistenceChecker(
        constraint
    )


# ============================================================
# Helper
# ============================================================


def create_dtype_pair(
        dtype: str,
) -> Pair:
    """
    Create Pair:

        x.dtype=dtype


    Example:

        x.dtype=fp16

    """

    factor1 = Factor(
        parameter="x",
        attribute="dtype",
    )

    factor_value1 = FactorValue(
        factor=factor1,
        value=dtype,
    )

    factor2 = Factor(
        parameter="y",
        attribute="dtype",
    )

    factor_value2 = FactorValue(
        factor=factor2,
        value=dtype,
    )

    return Pair(
        left=factor_value1,
        right=factor_value2,
    )


# ============================================================
# Test Cases
# ============================================================


class TestPairExistenceChecker:
    def test_filter_pairs(self,
                          checker: PairExistenceChecker,
                          ) -> None:
        """
        Test batch filtering.


        Input:

            fp16
            fp32
            fp16


        Output:

            fp16
            fp16

        """

        pairs = [

            create_dtype_pair(
                "fp16"
            ),

            create_dtype_pair(
                "fp32"
            ),

            create_dtype_pair(
                "fp16"
            ),

        ]

        valid_pairs = (
            checker.filter_pairs(
                pairs
            )
        )

        assert len(
            valid_pairs
        ) == 2


