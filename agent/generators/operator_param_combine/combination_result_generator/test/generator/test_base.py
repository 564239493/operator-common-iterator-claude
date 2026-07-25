import pytest

from generator.base import (
    BaseGenerator,
)

from generator.model import (
    GenerationResult,
    TestSuite,
)

from generator.generator_options import (
    GeneratorOptions,
)


class MockCoverage:

    def coverage_rate(
        self,
    ) -> float:

        return 0.75


class DummyGenerator(
    BaseGenerator
):

    def __init__(
        self,
    ):

        self.calls = []

        super().__init__(
            universe=None,
            coverage_tracker=MockCoverage(),
            constraint=None,
            config=GeneratorOptions(),
        )

    def initialize(
        self,
    ) -> None:

        self.calls.append(
            "initialize"
        )

    def build(
        self,
    ) -> GenerationResult:

        self.calls.append(
            "build"
        )

        return GenerationResult(
            suite=TestSuite(),
            coverage_rate=1.0,
            iterations=1,
            elapsed_time=0.0,
        )

    def finalize(
        self,
    ) -> None:

        self.calls.append(
            "finalize"
        )

class TestBaseGenerator:
    def test_generate_order(self):

        generator = DummyGenerator()

        generator.generate()

        assert generator.calls == [
            "initialize",
            "build",
            "finalize",
        ]


    def test_coverage_rate(self):

        generator = DummyGenerator()

        assert (
            generator.coverage_rate()
            ==
            0.75
        )


    def test_abstract_class(self):

        with pytest.raises(
            TypeError
        ):
            BaseGenerator(
                universe=None,
                coverage_tracker=None,
                constraint=None,
                config=GeneratorOptions(),
            )