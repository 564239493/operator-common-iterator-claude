from __future__ import annotations

from typing import Set

from agent.generators.operator_param_combine.combination_result_generator.generator.model import TestCase


class TestCaseCache:

    def __init__(self) -> None:
        self._signatures: Set[int] = set()

    def add(self, testcase: TestCase) -> bool:
        sig = self._compute_signature(testcase)
        if sig in self._signatures:
            return False
        self._signatures.add(sig)
        return True

    def contains(self, testcase: TestCase) -> bool:
        return self._compute_signature(testcase) in self._signatures

    def clear(self) -> None:
        self._signatures.clear()

    def size(self) -> int:
        return len(self._signatures)

    @staticmethod
    def _compute_signature(testcase: TestCase) -> int:
        items = []
        for param in sorted(testcase.values.keys()):
            attrs = testcase.values[param]
            for attr in sorted(attrs.keys()):
                val = attrs[attr]
                items.append((param, attr, str(val)))
        return hash(tuple(items))
