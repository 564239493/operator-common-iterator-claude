#!/usr/bin/env python3
"""Static validation for the Claude Code native project scaffold."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ALLOW_RULES = {
    "Read",
    "Glob",
    "Grep",
    "Edit(/runs/**)",
    "Agent",
    "Skill",
}
CPU_GOLDEN_GUIDES = (
    ROOT / ".claude" / "skills" / "atc-cpu-golden-derivation" / "SKILL.md",
    ROOT / "executer" / "resources" / "aclnn-cpu-golden-derivation.md",
)
RUN_DOC_SNAPSHOT = "runs/<current-run>/inputs/<operator-doc>.md"
WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Z0-9])(?:[A-Z]:[\\/]|\\\\[^\\\s]+[\\/])"
)


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
        if permissions.get("defaultMode") != "default":
            errors.append("permissions.defaultMode must be default")
        allow_rules = permissions.get("allow", [])
        missing_allow = REQUIRED_ALLOW_RULES - set(allow_rules)
        if missing_allow:
            errors.append(f"permissions missing required allow rules: {sorted(missing_allow)}")
        for forbidden in ("Bash", "Bash(*)", "PowerShell", "PowerShell(*)"):
            if forbidden in allow_rules:
                errors.append(f"permissions has overly broad allow rule: {forbidden}")
        if permissions.get("ask"):
            errors.append("permissions.ask should be omitted; default mode handles non-runtime commands")
        for rule in (
            "Edit(/executer/**)",
            "Edit(/agent/generators/**)",
            "Edit(/servers.json)",
        ):
            if rule not in permissions.get("deny", []):
                errors.append(f"permissions missing deny rule: {rule}")
        pre_hooks = data.get("hooks", {}).get("PreToolUse", [])
        if "guard_project_writes.py" not in json.dumps(pre_hooks):
            errors.append("PreToolUse must invoke guard_project_writes.py")
        hook_config = json.dumps(data.get("hooks", {}))
        if "python -X utf8" not in hook_config:
            errors.append("hooks must invoke Python directly in UTF-8 mode")
        if "run_python_hook" in hook_config or '"command": "bash ' in hook_config:
            errors.append("hooks must not depend on a Node/Bash launcher")
        if not data.get("sandbox", {}).get("enabled"):
            errors.append("sandbox must be enabled")
    except Exception as exc:
        errors.append(f"invalid settings.json: {exc}")

    agents = list((ROOT / ".claude" / "agents").glob("*.md"))
    skills = list((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
    if len(agents) != 9:
        errors.append(f"expected exactly 9 project agents, found {len(agents)}")
    if len(skills) != 14:
        errors.append(f"expected exactly 14 project skills, found {len(skills)}")
    for path in agents:
        errors.extend(has_frontmatter(path, ("name", "description")))
    for path in skills:
        errors.extend(has_frontmatter(path, ("description",)))
    for path in CPU_GOLDEN_GUIDES:
        if not path.is_file():
            errors.append(f"missing CPU golden guide: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        if RUN_DOC_SNAPSHOT not in text:
            errors.append(
                f"{path}: CPU golden documentation must use the current run snapshot "
                f"{RUN_DOC_SNAPSHOT}"
            )
        absolute_path = WINDOWS_ABSOLUTE_PATH.search(text)
        if absolute_path:
            errors.append(
                f"{path}: runtime guide contains a project-external absolute path: "
                f"{absolute_path.group(0)}"
            )
        if "CANN-aclnn-api-reference" in text:
            errors.append(f"{path}: contains the retired external documentation location")
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
