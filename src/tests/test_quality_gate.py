"""quality_gate 多形态归一化测试（对应计划边界#1）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.adapters.quality_gate import normalize_check, normalize_gate, normalize_value


class TestNormalizeValue(unittest.TestCase):
    def test_bool(self):
        self.assertEqual(normalize_value(True), "pass")
        self.assertEqual(normalize_value(False), "fail")

    def test_words(self):
        self.assertEqual(normalize_value("passed"), "pass")
        self.assertEqual(normalize_value("PASS"), "pass")
        self.assertEqual(normalize_value("failed"), "fail")
        self.assertEqual(normalize_value("blocked"), "fail")
        self.assertEqual(normalize_value("warning"), "warn")

    def test_unknown_never_pass(self):
        self.assertEqual(normalize_value("whatever"), "unknown")
        self.assertEqual(normalize_value(None), "unknown")
        self.assertEqual(normalize_value(42), "unknown")


class TestNormalizeCheck(unittest.TestCase):
    def test_iter001_shape_result_evidence(self):
        el = {"name": "constraints_semantics", "result": "pass", "evidence": "Pydantic 校验通过"}
        out = normalize_check(el)
        self.assertEqual(out["result"], "pass")
        self.assertEqual(out["detail"], "Pydantic 校验通过")
        self.assertEqual(out["raw_key"], "result")

    def test_iter004_shape_status_detail(self):
        el = {"name": "cases_contract", "status": "passed", "detail": "cases 满足契约"}
        out = normalize_check(el)
        self.assertEqual(out["result"], "pass")
        self.assertEqual(out["detail"], "cases 满足契约")
        self.assertEqual(out["raw_key"], "status")

    def test_shape_passed_bool(self):
        el = {"name": "x", "passed": False, "detail": "失败"}
        out = normalize_check(el)
        self.assertEqual(out["result"], "fail")
        self.assertEqual(out["raw_key"], "passed")

    def test_unrecognized_keys_unknown(self):
        el = {"name": "x", "score": 99}
        out = normalize_check(el)
        self.assertEqual(out["result"], "unknown")
        self.assertIsNone(out["raw_key"])


class TestNormalizeGate(unittest.TestCase):
    def test_summary_from_checks_summary(self):
        raw = {
            "status": "passed",
            "checks": [{"name": "a", "result": "pass", "evidence": ""}],
            "checks_summary": {"total": 1, "passed": 1, "failed": 0},
            "blocking_issues": [],
            "next_state": "DIAGNOSE",
        }
        out = normalize_gate(raw)
        self.assertEqual(out["status"], "pass")
        self.assertEqual(out["summary"]["raw_key"], "checks_summary")
        self.assertFalse(out["summary"]["summary_derived"])
        self.assertEqual(out["next_state"], "DIAGNOSE")

    def test_summary_derived_when_missing(self):
        raw = {
            "checks": [
                {"name": "a", "status": "passed", "detail": ""},
                {"name": "b", "status": "failed", "detail": "x"},
                {"name": "c", "score": 1},
            ],
            "blocking_issues": ["b 失败"],
            "next_state": "DIAGNOSE",
        }
        out = normalize_gate(raw)
        summary = out["summary"]
        self.assertTrue(summary["summary_derived"])
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["unknown"], 1)
        self.assertEqual(len(out["blocking_issues"]), 1)

    def test_summary_key_alias(self):
        raw = {"status": "passed", "checks": [], "summary": {"total": 0, "passed": 0, "failed": 0}}
        out = normalize_gate(raw)
        self.assertEqual(out["summary"]["raw_key"], "summary")


if __name__ == "__main__":
    unittest.main()
