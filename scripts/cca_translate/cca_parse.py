"""cca 行为划分解析器。

消费 cca_analysis_result 的 fn-*.md：把“## 行为划分”节里按缩进嵌套的
  - guard: <text>
    outcome: <类型>(<详情>, total|partial)
    来源: <source> · 可信度: <confidence>
解析成结构化分支树 IR。guard 文本原样保留（翻译是后续步骤）。

只解析结构，不翻译语义；解析异常抛出不吞。
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

log = logging.getLogger("cca_translate.cca_parse")

_GUARD_RE = re.compile(r"^( *)- guard:\s?(.*)$")
_OUTCOME_RE = re.compile(r"^ +outcome:\s?(.*)$")
_SOURCE_RE = re.compile(r"^ +来源:\s?(.*)$")

# outcome 行首词：成功 / 定义错误 / UB / 其它
_OUTCOME_KINDS = ("成功", "定义错误", "UB")


@dataclass
class Branch:
    level: int
    guard: str
    outcome_text: str | None = None  # outcome 行原文
    outcome_kind: str | None = None  # 成功/定义错误/UB
    total_partial: str | None = None  # total/partial
    source: str | None = None
    confidence: str | None = None
    children: list["Branch"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _strip_backticks(s: str) -> str:
    """去掉 markdown 反引号（IR 文本不需要格式标记）。"""
    return s.replace("`", "")


def _split_source_line(text: str) -> tuple[str | None, str | None]:
    """来源行常含 '· 可信度: 高'，拆出 source 与 confidence。"""
    source = text.strip()
    confidence = None
    m = re.search(r"可信度[:：]\s*([^·\n]+?)\s*$", source)
    if m:
        confidence = m.group(1).strip()
        source = re.sub(r"[·\s]*可信度[:：].*$", "", source).strip()
    return source or None, confidence


def _parse_outcome(text: str) -> tuple[str | None, str | None]:
    """从 outcome 原文抽 kind 与 total/partial。"""
    t = text.strip()
    kind = next((k for k in _OUTCOME_KINDS if t.startswith(k)), None)
    tp = "total" if "total" in t else ("partial" if "partial" in t else None)
    return kind, tp


def _extract_behavior_section(md: str) -> str:
    """截取 '## 行为划分' 到下一个二级标题之间的文本。"""
    lines = md.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("## 行为划分"):
            start = i + 1
            break
    if start is None:
        raise ValueError("未找到 '## 行为划分' 节")
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


def parse_behavior_partition(md: str) -> list[Branch]:
    """解析 fn-*.md 的行为划分节，返回顶层分支列表（含嵌套子分支）。"""
    section = _extract_behavior_section(md)
    roots: list[Branch] = []
    stack: list[Branch] = []  # 按 level 维护当前祖先栈

    for raw in section.splitlines():
        gm = _GUARD_RE.match(raw)
        if gm:
            indent = len(gm.group(1))
            level = indent // 2
            br = Branch(level=level, guard=_strip_backticks(gm.group(2).strip()))
            # 弹栈到比当前 level 浅的父
            while stack and stack[-1].level >= level:
                stack.pop()
            if stack:
                stack[-1].children.append(br)
            else:
                roots.append(br)
            stack.append(br)
            continue
        if not stack:
            continue
        cur = stack[-1]
        om = _OUTCOME_RE.match(raw)
        if om:
            text = _strip_backticks(om.group(1))
            cur.outcome_text = text.strip() or None
            kind, tp = _parse_outcome(text)
            cur.outcome_kind = kind
            cur.total_partial = tp
            continue
        sm = _SOURCE_RE.match(raw)
        if sm:
            src, conf = _split_source_line(sm.group(1))
            cur.source = src
            cur.confidence = conf
            continue
    log.info("解析行为划分：顶层 %d 分支", len(roots))
    return roots


def count_branches(roots: list[Branch]) -> int:
    n = 0
    for b in roots:
        n += 1 + count_branches(b.children)
    return n
