import pytest

from agent.generators.operator_param_combine.combination_result_generator.generator import InvalidGeneratorConfigError, GeneratorError
from agent.generators.operator_param_combine.combination_result_generator.generator.model import (
    TestValue,
    TestCase,
    TestSuite,
    GenerationResult,
)

class TestModel:

    def test_testvalue_create(self):

        value = TestValue(
            parameter="x",
            attribute="dtype",
            value="fp16",
        )

        assert value.parameter == "x"
        assert value.attribute == "dtype"
        assert value.value == "fp16"


    def test_testvalue_hashable(self):

        value1 = TestValue(
            parameter="x",
            attribute="dtype",
            value="fp16",
        )

        value2 = TestValue(
            parameter="x",
            attribute="dtype",
            value="fp16",
        )

        value_set = {
            value1,
            value2,
        }

        assert len(value_set) == 1


    def test_testcase_add_value(self):

        case = TestCase()

        case.add_value(
            TestValue(
                parameter="x",
                attribute="dtype",
                value="fp16",
            )
        )

        assert case.values == {
            "x": {
                "dtype": "fp16",
            }
        }


    def test_testcase_add_multiple_attributes(self):

        case = TestCase()

        case.add_value(
            TestValue(
                parameter="x",
                attribute="dtype",
                value="fp16",
            )
        )

        case.add_value(
            TestValue(
                parameter="x",
                attribute="shape_property",
                value="normal",
            )
        )

        assert case.values == {
            "x": {
                "dtype": "fp16",
                "shape_property": "normal",
            }
        }


    def test_testcase_add_multiple_parameters(self):

        case = TestCase()

        case.add_value(
            TestValue(
                parameter="x",
                attribute="dtype",
                value="fp16",
            )
        )

        case.add_value(
            TestValue(
                parameter="weight",
                attribute="dtype",
                value="int8",
            )
        )

        assert case.values == {
            "x": {
                "dtype": "fp16",
            },
            "weight": {
                "dtype": "int8",
            },
        }


    def test_testcase_get_value(self):

        case = TestCase()

        case.add_value(
            TestValue(
                parameter="x",
                attribute="dtype",
                value="fp16",
            )
        )

        assert (
            case.get_value(
                "x",
                "dtype",
            )
            == "fp16"
        )


    def test_testcase_parameters(self):

        case = TestCase()

        case.add_value(
            TestValue(
                parameter="x",
                attribute="dtype",
                value="fp16",
            )
        )

        case.add_value(
            TestValue(
                parameter="weight",
                attribute="dtype",
                value="int8",
            )
        )

        parameters = case.parameters()

        assert set(parameters) == {
            "x",
            "weight",
        }


    def test_testsuite_add(self):

        suite = TestSuite()

        case = TestCase()

        suite.add(case)

        assert suite.size() == 1


    def test_testsuite_multiple_add(self):

        suite = TestSuite()

        suite.add(TestCase())

        suite.add(TestCase())

        suite.add(TestCase())

        assert suite.size() == 3


    def test_testsuite_iterator(self):

        suite = TestSuite()

        suite.add(TestCase())

        suite.add(TestCase())

        count = 0

        for _ in suite:
            count += 1

        assert count == 2


    def test_generation_result(self):

        suite = TestSuite()

        result = GenerationResult(
            suite=suite,
            coverage_rate=1.0,
            iterations=100,
            elapsed_time=1.5,
        )

        assert result.suite is suite
        assert result.coverage_rate == 1.0
        assert result.iterations == 100
        assert result.elapsed_time == 1.5


    def test_testvalue_frozen(self):
        value = TestValue(
            parameter="x",
            attribute="dtype",
            value="fp16",
        )

        with pytest.raises(Exception):
            value.parameter = "y"

    def test_testvalue_none_value(self):
        value = TestValue(
            parameter="x",
            attribute="dtype",
            value=None,
        )

        assert value.value is None

    def test_testvalue_complex_value(self):
        value = TestValue(
            parameter="x",
            attribute="shape",
            value=[1, 2, 3],
        )

        assert value.value == [1, 2, 3]

    def test_testcase_override_value(self):
        case = TestCase()

        case.add_value(
            TestValue(
                "x",
                "dtype",
                "fp16",
            )
        )

        case.add_value(
            TestValue(
                "x",
                "dtype",
                "fp32",
            )
        )

        assert (
                case.get_value(
                    "x",
                    "dtype",
                )
                ==
                "fp32"
        )

    def test_testcase_empty_parameters(self):
        case = TestCase()

        assert case.parameters() == []

    import pytest

    def test_testcase_missing_parameter(self):
        case = TestCase()

        with pytest.raises(
                KeyError
        ):
            case.get_value(
                "x",
                "dtype",
            )

    def test_testcase_missing_attribute(self):
        case = TestCase()

        case.add_value(
            TestValue(
                "x",
                "dtype",
                "fp16",
            )
        )

        with pytest.raises(
                KeyError
        ):
            case.get_value(
                "x",
                "shape",
            )

    def test_testcase_default_metadata(self):
        case = TestCase()

        assert case.metadata == {}

    def test_testcase_metadata_assign(self):
        case = TestCase()

        case.metadata["score"] = 100

        assert (
                case.metadata["score"]
                ==
                100
        )

    def test_testsuite_empty(self):
        suite = TestSuite()

        assert suite.size() == 0

    def test_testsuite_empty_iterator(self):

        suite = TestSuite()

        count = 0

        for _ in suite:
            count += 1

        assert count == 0

    def test_testsuite_order_preserved(self):

        suite = TestSuite()

        case1 = TestCase()
        case2 = TestCase()

        suite.add(case1)
        suite.add(case2)

        cases = list(suite)

        assert cases[0] is case1
        assert cases[1] is case2

    def test_testsuite_large_scale(self):

        suite = TestSuite()

        for _ in range(1000):
            suite.add(
                TestCase()
            )

        assert suite.size() == 1000

    def test_generation_result_equality(self):

        suite = TestSuite()

        result1 = GenerationResult(
            suite=suite,
            coverage_rate=1.0,
            iterations=1,
            elapsed_time=0.1,
        )

        result2 = GenerationResult(
            suite=suite,
            coverage_rate=1.0,
            iterations=1,
            elapsed_time=0.1,
        )

        assert result1 == result2

