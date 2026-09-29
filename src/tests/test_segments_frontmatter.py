"""history 分段与 frontmatter 解析测试（对应计划边界#5/#6）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.adapters.run_detail import build_segments
from workbench.parsers.frontmatter import parse_frontmatter


def _ev(state, at, event=None, code=None):
    e = {"state": state, "at": at}
    if event:
        e["event"] = event
    if code:
        e["code"] = code
    return e


class TestBuildSegments(unittest.TestCase):
    def test_restart_segment(self):
        history = [
            _ev("PLAN", "t1"), _ev("EXTRACT", "t2"),
            _ev("STOPPED_BY_USER", "t3", code="SESSION_RESTART"),
            _ev("EXTRACT", "t4", event="restore"),
            _ev("GENERATE", "t5"),
        ]
        segments = build_segments(history)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["kind"], "normal")
        self.assertEqual(segments[1]["kind"], "restart")
        self.assertEqual(segments[1]["events"][0]["state"], "EXTRACT")

    def test_continuation_segment(self):
        history = [
            _ev("GATE", "t1"),
            _ev("MAX_ITERATIONS", "t2"),
            _ev("UPDATE_CONSTRAINTS", "t3", event="用户授权继续"),
            _ev("GENERATE", "t4"),
            _ev("MAX_ITERATIONS", "t5"),
        ]
        segments = build_segments(history)
        kinds = [s["kind"] for s in segments]
        self.assertEqual(kinds, ["normal", "continuation"])
        self.assertEqual(segments[1]["events"][0]["state"], "UPDATE_CONSTRAINTS")

    def test_plain_single_segment(self):
        history = [_ev("PLAN", "t1"), _ev("EXTRACT", "t2"), _ev("SUCCESS", "t3")]
        segments = build_segments(history)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["kind"], "normal")

    def test_empty(self):
        self.assertEqual(build_segments([]), [])


_SAMPLE_FRONTMATTER = """---
name: constraint-extractor
description: 从 CANN 算子 Markdown 文档提取并校验结构化约束。仅在迭代流程的 EXTRACT 阶段使用。
tools: Read, Write, Edit, Glob, Grep, Bash, Skill, Agent
model: inherit
skills:
  - extract-constraints
color: blue
---

# 正文
"""


class TestFrontmatter(unittest.TestCase):
    def test_parse_agent(self):
        meta = parse_frontmatter(_SAMPLE_FRONTMATTER)
        self.assertEqual(meta["name"], "constraint-extractor")
        self.assertEqual(meta["skills"], ["extract-constraints"])
        self.assertEqual(meta["color"], "blue")
        self.assertIn("EXTRACT", meta["description"])

    def test_no_frontmatter(self):
        self.assertEqual(parse_frontmatter("# 只有正文"), {})

    def test_opencode_nested_permission(self):
        """opencode 嵌套 permission：子键归入嵌套 dict，不污染顶层。"""
        text = """---
description: 执行测试用例
mode: subagent
color: "#e6a23c"
permission:
  task: deny
  webfetch: deny
  tools.*: allow
---

正文
"""
        meta = parse_frontmatter(text)
        self.assertEqual(meta["description"], "执行测试用例")
        self.assertEqual(meta["mode"], "subagent")
        self.assertEqual(meta["color"], "#e6a23c")
        self.assertEqual(meta["permission"], {"task": "deny", "webfetch": "deny", "tools.*": "allow"})
        # 子键不得污染顶层
        self.assertNotIn("task", meta)
        self.assertNotIn("webfetch", meta)

    def test_multilevel_nested_and_inline_list(self):
        text = """---
top:
  middle:
    leaf: 1
tools: [read, "write", bash]
---
x
"""
        meta = parse_frontmatter(text)
        self.assertEqual(meta["top"], {"middle": {"leaf": "1"}})
        self.assertEqual(meta["tools"], ["read", "write", "bash"])

    def test_unclosed_frontmatter_error(self):
        """未闭合 frontmatter → 结构错误标记，非半成品。"""
        meta = parse_frontmatter("---\nkey: value\n没有闭合")
        self.assertIn("_error", meta)

    def test_quoted_key_with_colon(self):
        text = '---\n"weird:key": value\nnormal: "含: 冒号的值"\n---\n'
        meta = parse_frontmatter(text)
        self.assertEqual(meta["weird:key"], "value")
        self.assertEqual(meta["normal"], "含: 冒号的值")


if __name__ == "__main__":
    unittest.main()
