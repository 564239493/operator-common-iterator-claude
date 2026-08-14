from agent.generators.operator_param_combine.combination_result_generator.coverage import Pair, PairUniverse
from agent.generators.operator_param_combine.combination_result_generator.coverage.coverage import CoverageTracker
from agent.generators.operator_param_combine.combination_result_generator.coverage.parameter import Factor
from agent.generators.operator_param_combine.combination_result_generator.coverage.value import FactorValue

from agent.generators.operator_param_combine.combination_result_generator.generator.pair_seed_generator import (
    PairSeedGenerator,
)



def create_pair() -> Pair:
    left = FactorValue(
        factor=Factor(
            parameter="x",
            attribute="dtype",
        ),
        value="fp16",
    )

    right = FactorValue(
        factor=Factor(
            parameter="weight",
            attribute="dtype",
        ),
        value="int8",
    )

    return Pair(
        left=left,
        right=right,
    )

def create_generator() -> PairSeedGenerator:
    pair = create_pair()
    universe = PairUniverse(
        [pair]
    )

    coverage = CoverageTracker(
        universe
    )

    return PairSeedGenerator(
        universe,
        coverage,
    )

class TestPairSeedGenerator:
    
    def test_pair_to_seed(self):
    
        generator = create_generator()
    
        pair = create_pair()
    
        seed = generator.pair_to_seed(
            pair
        )
    
        assert seed == {
            "x": {
                "dtype": "fp16"
            },
            "weight": {
                "dtype": "int8"
            }
        }
    
    
    def test_next_pair(self):
    
        generator = create_generator()
    
        pair = generator.next_pair()
    
        assert pair is not None
    
        assert (
            pair.left.value == "int8"
        )
    
        assert (
            pair.right.value == "fp16"
        )
    
    
    def test_next_seed(self):
    
        generator = create_generator()
    
        seed = generator.next_seed()
    
        assert seed == {
            "x": {
                "dtype": "fp16"
            },
            "weight": {
                "dtype": "int8"
            }
        }
    
    
    def test_next_pair_empty(self):
    
        pair = create_pair()
    
        universe = PairUniverse(
            [pair]
        )
    
        coverage = CoverageTracker(
            universe
        )
    
        coverage.mark_covered(
            pair
        )
    
        generator = PairSeedGenerator(
            universe,
            coverage,
        )
    
        assert (
            generator.next_pair()
            is None
        )
    
    
    def test_next_seed_empty(self):
    
        pair = create_pair()
    
        universe = PairUniverse(
            [pair]
        )
    
        coverage = CoverageTracker(
            universe
        )
    
        coverage.mark_covered(
            pair
        )
    
        generator = PairSeedGenerator(
            universe,
            coverage,
        )
    
        assert (
            generator.next_seed()
            is None
        )