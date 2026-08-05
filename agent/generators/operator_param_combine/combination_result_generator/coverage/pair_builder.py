from itertools import combinations
from typing import Iterable, List, Optional

from agent.generators.common_utils.timing import track
from agent.generators.operator_param_combine.combination_result_generator.coverage import Pair
from agent.generators.operator_param_combine.combination_result_generator.coverage import PairExistenceChecker
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue


class PairBuilder:
    """
    Build Pair universe.

    When checker is provided, constraint-violating
    pairs are filtered inline — no Pair object
    is created for invalid combinations.
    """

    @track("PairBuilder.build")
    def build(
        self,
        values: Iterable[FactorValue],
        checker: Optional[PairExistenceChecker] = None,
    ) -> List[Pair]:
        values = list(values)
        pairs: List[Pair] = []

        for left, right in combinations(values, 2):
            if left.factor == right.factor:
                continue

            if checker is not None:
                if not checker.is_valid_combination(left, right):
                    continue

            pairs.append(Pair(left, right))

        return pairs

