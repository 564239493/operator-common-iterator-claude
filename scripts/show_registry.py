#!/usr/bin/env python3
"""Print project Skills, Agents, their preload relations, and dispatch flow."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not match:
        return {}
    data: dict[str, object] = {}
    current_list: str | None = None
    for raw in match.group(1).splitlines():
        if raw.startswith("  - ") and current_list:
            value = str(data.get(current_list, ""))
            item = raw[4:].strip()
            data[current_list] = f"{value}, {item}".strip(", ")
            continue
        current_list = None
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key, value = key.strip(), value.strip()
        data[key] = value
        if not value:
            current_list = key
    return data


def clipped(value: object, width: int = 52) -> str:
    text = str(value or "-")
    return text if len(text) <= width else text[: width - 1] + "…"


def main() -> int:
    print("=== Claude Code Workforce ===")
    print("\nSkills")
    for path in sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")):
        meta = frontmatter(path)
        print(f"  /{path.parent.name:<22} {clipped(meta.get('description'))}")

    print("\nAgents")
    for path in sorted((ROOT / ".claude" / "agents").glob("*.md")):
        meta = frontmatter(path)
        name = str(meta.get("name") or path.stem)
        skills = str(meta.get("skills") or "-")
        print(f"  @{name:<22} skill={skills:<22} {clipped(meta.get('description'), 44)}")

    print("\nDispatch")
    print("  PLAN -> constraint-extractor -> case-generator -> case-executor")
    print("       -> quality-reviewer -> SUCCESS")
    print("       -> failure-analyst -> prompt-optimizer -> next iteration")
    print("       -> generator_bug | executor_bug -> STOP")
    print(
        "\nLive views: /agents (instances) | /hooks (lifecycle) | "
        "/iterate-operator (run) | /iterate-directory (batch)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
