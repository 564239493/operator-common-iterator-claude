import pytest

from generator.cache import TestCaseCache
from generator.model import TestCase, TestValue


def make_testcase(param_dtype_value: str) -> TestCase:
    case = TestCase()
    case.add_value(TestValue(parameter="x", attribute="dtype", value=param_dtype_value))
    case.add_value(TestValue(parameter="x", attribute="dimension", value=2))
    case.add_value(TestValue(parameter="weight", attribute="dtype", value="int8"))
    return case


class TestTestCaseCache:

    def test_cache_init(self):
        cache = TestCaseCache()
        assert cache.size() == 0

    def test_add_new(self):
        cache = TestCaseCache()
        case = make_testcase("fp16")
        result = cache.add(case)
        assert result is True
        assert cache.size() == 1

    def test_add_duplicate(self):
        cache = TestCaseCache()
        case = make_testcase("fp16")
        cache.add(case)
        result = cache.add(case)
        assert result is False
        assert cache.size() == 1

    def test_add_same_values_duplicate(self):
        cache = TestCaseCache()
        case1 = make_testcase("fp16")
        case2 = make_testcase("fp16")
        cache.add(case1)
        result = cache.add(case2)
        assert result is False
        assert cache.size() == 1

    def test_contains_present(self):
        cache = TestCaseCache()
        case = make_testcase("fp16")
        cache.add(case)
        assert cache.contains(case) is True

    def test_contains_absent(self):
        cache = TestCaseCache()
        case = make_testcase("fp16")
        assert cache.contains(case) is False

    def test_contains_same_values(self):
        cache = TestCaseCache()
        case1 = make_testcase("fp16")
        case2 = make_testcase("fp16")
        cache.add(case1)
        assert cache.contains(case2) is True

    def test_add_different(self):
        cache = TestCaseCache()
        case1 = make_testcase("fp16")
        case2 = make_testcase("fp32")
        cache.add(case1)
        result = cache.add(case2)
        assert result is True
        assert cache.size() == 2

    def test_clear(self):
        cache = TestCaseCache()
        cache.add(make_testcase("fp16"))
        cache.add(make_testcase("fp32"))
        assert cache.size() == 2
        cache.clear()
        assert cache.size() == 0

    def test_clear_then_add(self):
        cache = TestCaseCache()
        cache.add(make_testcase("fp16"))
        cache.clear()
        cache.add(make_testcase("fp16"))
        assert cache.size() == 1

    def test_add_many(self):
        cache = TestCaseCache()
        for i in range(100):
            case = TestCase()
            case.add_value(TestValue(parameter="x", attribute="dtype", value=f"v{i}"))
            cache.add(case)
        assert cache.size() == 100

    def test_empty_testcase(self):
        cache = TestCaseCache()
        case1 = TestCase()
        case2 = TestCase()
        assert cache.add(case1) is True
        assert cache.add(case2) is False
        assert cache.size() == 1

    def test_different_structure(self):
        cache = TestCaseCache()
        case1 = TestCase()
        case1.add_value(TestValue(parameter="x", attribute="dtype", value="fp16"))

        case2 = TestCase()
        case2.add_value(TestValue(parameter="x", attribute="dtype", value="fp16"))
        case2.add_value(TestValue(parameter="x", attribute="dimension", value=2))

        cache.add(case1)
        assert cache.add(case2) is True
        assert cache.size() == 2

    def test_multiple_parameters(self):
        cache = TestCaseCache()
        case = TestCase()
        case.add_value(TestValue(parameter="x", attribute="dtype", value="fp16"))
        case.add_value(TestValue(parameter="weight", attribute="dtype", value="int8"))
        cache.add(case)
        assert cache.size() == 1
        assert cache.contains(case) is True

    def test_contains_on_empty_cache(self):
        cache = TestCaseCache()
        case = make_testcase("fp16")
        assert cache.contains(case) is False

    def test_duplicate_add_returns_false(self):
        cache = TestCaseCache()
        case = make_testcase("fp16")
        assert cache.add(case) is True
        assert cache.add(case) is False
        assert cache.add(case) is False

    def test_clear_on_empty(self):
        cache = TestCaseCache()
        cache.clear()
        assert cache.size() == 0

    def test_order_independence(self):
        cache = TestCaseCache()
        case1 = TestCase()
        case1.add_value(TestValue(parameter="x", attribute="dtype", value="fp16"))
        case1.add_value(TestValue(parameter="weight", attribute="dtype", value="int8"))

        case2 = TestCase()
        case2.add_value(TestValue(parameter="weight", attribute="dtype", value="int8"))
        case2.add_value(TestValue(parameter="x", attribute="dtype", value="fp16"))

        cache.add(case1)
        assert cache.add(case2) is False
