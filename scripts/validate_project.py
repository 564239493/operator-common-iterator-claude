#!/usr/bin/env python3
"""Static validation for the Claude Code native project scaffold."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def has_frontmatter(path: Path, required: tuple[str, ...]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not match:
        return [f"{path}: missing YAML frontmatter"]
    block = match.group(1)
    return [f"{path}: missing {key}" for key in required if not re.search(rf"^{key}:", block, re.M)]


def main() -> int:
    errors: list[str] = []
    settings = ROOT / ".claude" / "settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
        for event in ("PreToolUse", "SessionStart", "SubagentStart", "SubagentStop"):
            if event not in data.get("hooks", {}):
                errors.append(f"settings missing hook: {event}")
        permissions = data.get("permissions", {})
        if permissions.get("defaultMode") != "dontAsk":
            errors.append("permissions.defaultMode must be dontAsk")
        for rule in ("Read", "Bash", "Edit(/**)", "Write(/**)", "Agent", "Skill"):
            if rule not in permissions.get("allow", []):
                errors.append(f"permissions missing allow rule: {rule}")
        if not data.get("sandbox", {}).get("enabled"):
            errors.append("sandbox must be enabled")
    except Exception as exc:
        errors.append(f"invalid settings.json: {exc}")

    agents = list((ROOT / ".claude" / "agents").glob("*.md"))
    skills = list((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
    if len(agents) < 6:
        errors.append("expected at least 6 project agents")
    if len(skills) < 8:
        errors.append("expected at least 8 project skills")
    for path in agents:
        errors.extend(has_frontmatter(path, ("name", "description")))
    for path in skills:
        errors.extend(has_frontmatter(path, ("description",)))
    for path in ("CLAUDE.md", "docs/WORKFLOW.md", "docs/OBSERVABILITY.md", "docs/ARTIFACT_CONTRACTS.md"):
        if not (ROOT / path).is_file():
            errors.append(f"missing {path}")

    print(json.dumps(
        {"valid": not errors, "agents": len(agents), "skills": len(skills), "errors": errors},
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
