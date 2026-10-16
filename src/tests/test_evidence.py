"""evidence 装配测试：inputs 三态、契约有效性、合法空值不算 broken。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.adapters import evidence as ev


class TestSummarizeInputs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _inputs(self):
        return self.root / "inputs"

    def _write(self, name, content):
        self._write_bytes(name, content.encode("utf-8"))

    def _write_bytes(self, name, data):
        self._inputs().mkdir(parents=True, exist_ok=True)
        (self._inputs() / name).write_bytes(data)

    def test_decisions_contract(self):
        """decisions 须为数组：合法空数组计 0 条；类型错误 → broken，不按空文件处理。"""
        self._write("prompt_update_decisions.json", json.dumps({"decisions": []}))
        self.assertEqual(ev.summarize_inputs(self.root)["prompt_update_decisions.json"],
                         {"status": "ok", "size": 17, "count": 0})
        self._write("prompt_update_decisions.json", json.dumps({"decisions": {"a": 1}}))
        self.assertEqual(ev.summarize_inputs(self.root)["prompt_update_decisions.json"]["status"], "broken")

    def test_decisions_invalid_json_broken(self):
        self._write("prompt_update_decisions.json", "{ not json")
        self.assertEqual(ev.summarize_inputs(self.root)["prompt_update_decisions.json"]["status"], "broken")

    def test_scene_scan_contract(self):
        """has_scenarios=false + 空设备列表是合法完成；缺关键字段 → broken。"""
        self._write("scene_scan.json",
                    json.dumps({"has_scenarios": False, "device_types": []}))
        entry = ev.summarize_inputs(self.root)["scene_scan.json"]
        self.assertEqual(entry["status"], "ok")
        self.assertFalse(entry["has_scenarios"])
        self._write("scene_scan.json", json.dumps({"operator": "x"}))
        self.assertEqual(ev.summarize_inputs(self.root)["scene_scan.json"]["status"], "broken")

    def test_text_files_empty_is_ok(self):
        """允许为空的文本文档（空 md）不属于 broken。"""
        self._write("supplementary-doc.md", "")
        entry = ev.summarize_inputs(self.root)["supplementary-doc.md"]
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["size"], 0)

    def test_missing_files_absent_from_summary(self):
        self.assertEqual(ev.summarize_inputs(self.root), {})

    def test_broken_json_generic_contract(self):
        self._write("selection.json", json.dumps("字符串不是对象"))
        self.assertEqual(ev.summarize_inputs(self.root)["selection.json"]["status"], "broken")

    def test_unicode_broken_json(self):
        self._write_bytes("selection.json", b"\xff\xfe{}")
        self.assertEqual(ev.summarize_inputs(self.root)["selection.json"]["status"], "broken")


if __name__ == "__main__":
    unittest.main()
