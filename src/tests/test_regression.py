"""regression_check 分类测试（对应计划边界#2：求值器不支持 ≠ 真实回归）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.adapters.regression import classify


class TestRegressionClassify(unittest.TestCase):
    def test_evaluator_unsupported(self):
        raw = {
            "ok": False,
            "checked_cases": 5,
            "regressions": [
                {"case_id": "case_1", "issues": [
                    "constraint could not be evaluated for 'x': ValueError: unsupported expression node: Slice"]},
                {"case_id": "case_2", "issues": [
                    "constraint could not be evaluated for 'y': ValueError: unsupported expression node: Slice"]},
            ],
        }
        out = classify(raw)
        self.assertEqual(out["kind"], "evaluator_unsupported")
        self.assertEqual(out["regressions_count"], 2)
        self.assertFalse(out["ok"])

    def test_real_regression(self):
        raw = {
            "ok": False,
            "checked_cases": 3,
            "regressions": [
                {"case_id": "case_9", "issues": ["shape 约束违反：dim0 期望 16 实际 8"]},
            ],
        }
        out = classify(raw)
        self.assertEqual(out["kind"], "real_regression")

    def test_mixed(self):
        raw = {
            "ok": False,
            "regressions": [
                {"case_id": "a", "issues": ["constraint could not be evaluated: unsupported expression node: Slice"]},
                {"case_id": "b", "issues": ["dtype 约束违反"]},
            ],
        }
        out = classify(raw)
        self.assertEqual(out["kind"], "mixed")

    def test_ok_none(self):
        self.assertEqual(classify({"ok": True, "regressions": []})["kind"], "none")
        self.assertEqual(classify({"ok": False, "regressions": []})["kind"], "none")

    def test_garbage(self):
        out = classify(None)
        self.assertEqual(out["kind"], "none")


if __name__ == "__main__":
    unittest.main()
