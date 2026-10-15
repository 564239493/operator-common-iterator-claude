"""路径防逃逸与 artifact 白名单测试（对应计划边界#10 只读红线）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench import paths
from workbench.adapters import artifacts


class TestResolveWithin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "iter_001").mkdir()
        (self.root / "iter_001" / "quality_gate.json").write_text(
            json.dumps({"status": "passed"}), encoding="utf-8")
        (self.root / "iter_001" / "evil.txt").write_text("x", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_normal_path(self):
        out = paths.resolve_within(self.root, "iter_001/quality_gate.json")
        self.assertTrue(out.is_file())

    def test_dotdot_rejected(self):
        with self.assertRaises(paths.PathEscapeError):
            paths.resolve_within(self.root, "../../CLAUDE.md")
        with self.assertRaises(paths.PathEscapeError):
            paths.resolve_within(self.root, "iter_001/../../../etc/passwd")

    def test_absolute_rejected(self):
        with self.assertRaises(paths.PathEscapeError):
            paths.resolve_within(self.root, "/etc/passwd")

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            paths.resolve_within(self.root, "iter_001/nope.json")


class TestArtifactWhitelist(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "iter_001").mkdir()
        (self.root / "iter_001" / "quality_gate.json").write_text(
            json.dumps({"status": "passed"}), encoding="utf-8")
        (self.root / "iter_001" / "evil.txt").write_text("x", encoding="utf-8")
        (self.root / "iter_001" / "cases_Atlas A2.json").write_text("[]", encoding="utf-8")
        big = {"records": [{"id": i} for i in range(100)], "log_content": "z" * 30000}
        (self.root / "iter_001" / "execution_result.json").write_text(
            json.dumps(big), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_whitelisted(self):
        out = artifacts.get_artifact(self.root, "iter_001/quality_gate.json")
        self.assertEqual(out["json"]["status"], "passed")
        self.assertFalse(out["truncated"])
        self.assertEqual(len(out["sha256"]), 64)

    def test_non_json_rejected(self):
        with self.assertRaises(artifacts.ArtifactRejected):
            artifacts.get_artifact(self.root, "iter_001/evil.txt")

    def test_escape_rejected(self):
        # 白名单与防逃逸都映射为 403；两类异常均可接受
        with self.assertRaises((paths.PathEscapeError, artifacts.ArtifactRejected)):
            artifacts.get_artifact(self.root, "../../CLAUDE.md")

    def test_cases_platform_prefix_allowed(self):
        out = artifacts.get_artifact(self.root, "iter_001/cases_Atlas A2.json")
        self.assertEqual(out["json"], [])

    def test_truncation(self):
        # 人为把截断阈值调小来测结构截断
        from workbench import config
        original = config.ARTIFACT_TRUNCATE_BYTES
        config.ARTIFACT_TRUNCATE_BYTES = 10
        try:
            out = artifacts.get_artifact(self.root, "iter_001/execution_result.json")
        finally:
            config.ARTIFACT_TRUNCATE_BYTES = original
        self.assertTrue(out["truncated"])
        records = out["json"]["records"]
        self.assertTrue(records["_truncated"])
        self.assertEqual(records["count"], 100)
        self.assertEqual(len(records["head"]), 3)
        self.assertIn("字符串截断", out["json"]["log_content"])


if __name__ == "__main__":
    unittest.main()
