import ast

from constraint.cache import ConstraintCache


class TestConstraintCache:
    def test_cache_put_get(self):
        cache = ConstraintCache()

        tree = ast.parse(
            "x.dtype=='fp16'",
            mode="eval"
        )

        cache.put(
            "x.dtype=='fp16'",
            tree
        )

        result = cache.get(
            "x.dtype=='fp16'"
        )

        assert result is tree

    def test_cache_miss(self):
        cache = ConstraintCache()

        assert (
                cache.get(
                    "unknown"
                )
                is None
        )

    def test_remove(self):
        cache = ConstraintCache()

        tree = ast.parse(
            "x==1",
            mode="eval"
        )

        cache.put(
            "x==1",
            tree
        )

        cache.remove(
            "x==1"
        )

        assert not cache.contains(
            "x==1"
        )

    def test_clear(self):
        cache = ConstraintCache()

        cache.put(
            "x==1",
            ast.parse(
                "x==1",
                mode="eval"
            )
        )

        cache.clear()

        assert cache.size() == 0