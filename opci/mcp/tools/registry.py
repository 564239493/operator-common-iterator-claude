"""registry MCP tool: show_workforce."""

from __future__ import annotations

import re
from pathlib import Path

from opci.config import get_project_root


def show_workforce() -> str:
    """Display available skills, agents, preload relations and dispatch topology."""
    project_root = get_project_root()
    skills_dir = project_root / ".claude" / "skills"
    agents_dir = project_root / ".claude" / "agents"

    output_lines = ["=== Claude Code Workforce ===\n"]

    output_lines.append("Skills")
    if skills_dir.is_dir():
        for path in sorted(skills_dir.glob("*/SKILL.md")):
            meta = _frontmatter(path)
            desc = _clipped(meta.get("description"))
            output_lines.append(f"  /{path.parent.name:<22} {desc}")

    output_lines.append("\nAgents")
    if agents_dir.is_dir():
        for path in sorted(agents_dir.glob("*.md")):
            meta = _frontmatter(path)
            name = str(meta.get("name") or path.stem)
            skills = str(meta.get("skills") or "-")
            desc = _clipped(meta.get("description"), 44)
            output_lines.append(f"  @{name:<22} skill={skills:<22} {desc}")

    output_lines.append("\nDispatch")
    output_lines.append("  PLAN -> constraint-extractor -> case-generator -> case-executor")
    output_lines.append("       -> quality-reviewer -> SUCCESS")
    output_lines.append("       -> failure-analyst -> prompt-optimizer -> next iteration")
    output_lines.append("       -> generator_bug | executor_bug -> STOP")
    output_lines.append(
        "\nLive views: /agents | /hooks | /iterate-operator | /iterate-directory"
    )
    return "\n".join(output_lines)


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not match:
        return {}
    data: dict[str, object] = {}
    for raw in match.group(1).splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def _clipped(value: object, width: int = 52) -> str:
    text = str(value or "-")
    return text if len(text) <= width else text[:width - 1] + "…"
