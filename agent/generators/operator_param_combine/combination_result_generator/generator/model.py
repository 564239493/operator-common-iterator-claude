from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List


@dataclass(
    frozen=True
)
class TestValue:
    """
    Single test value.
    """
    parameter: str
    attribute: str
    value: Any


@dataclass
class TestCase:
    """
    One generated test case.
    """
    values: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_value(self, value: TestValue, ) -> None:
        """
        Add test value.
        """
        if value.parameter not in self.values:
            self.values[value.parameter] = {}

        self.values[value.parameter][value.attribute] = value.value

    def get_value(self, parameter: str, attribute: str) -> Any:
        """
        Get attribute value.
        """
        return self.values[parameter][attribute]

    def parameters(self) -> List[str]:
        """
        Return parameters.
        """
        return list(self.values.keys())


@dataclass
class TestSuite:
    """
    Collection of test cases.
    """
    cases: List[TestCase] = field(default_factory=list)

    def add(self,case: TestCase,) -> None:
        self.cases.append(case)

    def size(self) -> int:
        return len(self.cases)

    def __iter__(self,) -> Iterator[TestCase]:
        return iter(self.cases)


@dataclass
class GenerationResult:
    """
    Generator output.
    """
    suite: TestSuite
    coverage_rate: float
    iterations: int
    elapsed_time: float
