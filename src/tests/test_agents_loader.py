"""智能体定义加载测试：opencode 优先 / 空目录不回退 / 损坏不伪装 / Claude 回退。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.adapters import agents as agents_adapter

_OPENCODE_DEF = """---
description: 执行生成的测试用例并规范化执行结果
mode: subagent
color: "#e6a23c"
permission:
  task: deny
  webfetch: deny
---

开工第一步：立即用 skill 工具加载 `execute-cases` 技能。
"""

_CLAUDE_DEF = """---
name: case-executor
description: 执行测试用例
tools: Read, Bash
model: inherit
skills:
  - execute-cases
color: orange
---
"""


class TestAgentsLoader(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _get(self, name):
        return {d["name"]: d for d in agents_adapter.load_agent_defs(self.root)}[name]

    def test_opencode_preferred_with_nested_permission_and_skill_extract(self):
        self._write(".opencode/agents/case-executor.md", _OPENCODE_DEF)
        self._write(".claude/agents/case-executor.md", _CLAUDE_DEF)
        entry = self._get("case-executor")
        self.assertEqual(entry["definition_found"], True)
        self.assertIsNone(entry["load_error"])
        self.assertEqual(entry["source"], ".opencode/agents")
        self.assertEqual(entry["mode"], "subagent")
        self.assertEqual(entry["permission"], {"task": "deny", "webfetch": "deny"})
        self.assertEqual(entry["skills"], ["execute-cases"])  # 正文句式提取
        self.assertNotEqual(entry["description"], "执行测试用例")  # 用的是 opencode 版

    def test_opencode_empty_dir_no_fallback(self):
        """opencode 目录存在但为空 → 不回退，definition_found=False。"""
        (self.root / ".opencode" / "agents").mkdir(parents=True)
        self._write(".claude/agents/case-executor.md", _CLAUDE_DEF)
        defs = agents_adapter.load_agent_defs(self.root)
        entry = {d["name"]: d for d in defs}["case-executor"]
        self.assertEqual(entry["definition_found"], False)
        self.assertIsNone(entry["load_error"])
        self.assertEqual(entry["source"], None)

    def test_broken_opencode_file_reports_load_error(self):
        """单文件损坏（frontmatter 未闭合）→ definition_found=True + load_error，
        不伪装成功、不静默用 Claude 版。"""
        self._write(".opencode/agents/case-executor.md", "---\ncolor: blue\n未闭合")
        self._write(".claude/agents/case-executor.md", _CLAUDE_DEF)
        entry = self._get("case-executor")
        self.assertEqual(entry["definition_found"], True)
        self.assertIn("未闭合", entry["load_error"])
        self.assertEqual(entry["source"], ".opencode/agents")

    def test_claude_fallback_when_opencode_missing(self):
        """opencode 目录不存在 → 回退 Claude，字段按 Claude 规则读取。"""
        self._write(".claude/agents/case-executor.md", _CLAUDE_DEF)
        entry = self._get("case-executor")
        self.assertEqual(entry["source"], ".claude/agents")
        self.assertEqual(entry["skills"], ["execute-cases"])
        self.assertEqual(entry["tools"], "Read, Bash")
        self.assertEqual(entry["color"], "orange")
        self.assertEqual(entry["definition_found"], True)

    def test_missing_everything_fixed_roles_marked(self):
        """两个目录都没有 → 12 个流程角色仍在，但 definition_found=False。"""
        defs = agents_adapter.load_agent_defs(self.root)
        self.assertEqual(len(defs), 12)
        self.assertTrue(all(d["definition_found"] is False for d in defs))
        self.assertEqual(defs[0]["name"], "scene-scanner")
        self.assertEqual(defs[0]["role"], "场景扫描")

    def test_unicode_broken_file_reports_load_error(self):
        """非 UTF-8 文件由读取层处理 → load_error，不崩溃。"""
        path = self.root / ".opencode" / "agents" / "case-executor.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"---\ndescription: \xff\xfe\xff\n---\n")
        entry = self._get("case-executor")
        self.assertEqual(entry["definition_found"], True)
        self.assertIn("读取失败", entry["load_error"])


if __name__ == "__main__":
    unittest.main()
