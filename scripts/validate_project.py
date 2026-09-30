#!/usr/bin/env python3
"""Static validation for the opencode native project scaffold.

校验 `.opencode` 原生布局（agents/commands/hooks/skills）与运行护栏：
- 桥接插件已移除（`opencode.json` 不再引用 `@sjawhar/opencode-claude-bridge`）；
- 插件脚本引用 `.opencode/hooks/*.py`，hooks 持久化到 `.opencode/runtime`；
- 必需 agents / skills / commands 齐全，frontmatter 合法（skill name == 目录名）；
- CPU golden 指南存在且只引用当前 run 快照。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENCODE = ROOT / ".opencode"
BRIDGE_PLUGIN = "@sjawhar/opencode-claude-bridge"
CPU_GOLDEN_GUIDES = (
    OPENCODE / "skills" / "atc-cpu-golden-derivation" / "SKILL.md",
    ROOT / "executer" / "resources" / "aclnn-cpu-golden-derivation.md",
)
RUN_DOC_SNAPSHOT = "runs/<current-run>/inputs/<operator-doc>.md"
WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Z0-9])(?:[A-Z]:[\\/]|\\\\[^\\\s]+[\\/])"
)
REQUIRED_AGENTS = {
    "case-executor", "case-generator", "constraint-checker",
    "constraint-extractor", "constraint-repairer", "constraint-supplementer",
    "constraint-updater",
    "failure-analyst", "prompt-optimizer", "quality-reviewer",
    "scene-scanner", "source-analyst",
}
REQUIRED_SKILLS = {
    "analyze-source", "atc-cpu-golden-derivation", "check-constraints",
    "collect-operator-source", "derive-ttk-golden", "diagnose-failure",
    "execute-cases", "extract-constraints", "generate-cases",
    "iterate-directory", "iterate-operator", "optimize-prompt",
    "repair-constraints", "scan-scenes", "show-workforce",
    "supplement-constraints", "update-constraints", "validate-run",
}
REQUIRED_COMMANDS = {"iterate-directory", "iterate-operator", "show-workforce"}


def frontmatter_block(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    return match.group(1) if match else None


def has_frontmatter(path: Path, required: tuple[str, ...]) -> list[str]:
    block = frontmatter_block(path)
    if block is None:
        return [f"{path}: missing YAML frontmatter"]
    return [f"{path}: missing {key}" for key in required if not re.search(rf"^{key}:", block, re.M)]


def skill_name_matches_dir(path: Path) -> list[str]:
    block = frontmatter_block(path)
    if block is None:
        return []
    match = re.search(r"^name:\s*(.+)$", block, re.M)
    if not match:
        return [f"{path}: missing name（opencode 要求 name == 目录名）"]
    name = match.group(1).strip().strip("'\"")
    if name != path.parent.name:
        return [
            f"{path}: frontmatter name '{name}' != directory '{path.parent.name}'"
            "（opencode 要求一致）"
        ]
    return []


def check_opencode_json(errors: list[str]) -> None:
    config = OPENCODE / "opencode.json"
    if not config.is_file():
        errors.append("missing .opencode/opencode.json")
        return
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"invalid .opencode/opencode.json: {exc}")
        return
    plugins = data.get("plugin") or []
    if isinstance(plugins, str):
        plugins = [plugins]
    if BRIDGE_PLUGIN in plugins:
        errors.append(f"opencode.json must not reference the removed bridge plugin: {BRIDGE_PLUGIN}")


def main() -> int:
    errors: list[str] = []
    check_opencode_json(errors)

    agents = sorted((OPENCODE / "agents").glob("*.md"))
    skills = sorted((OPENCODE / "skills").glob("*/SKILL.md"))
    commands = sorted((OPENCODE / "commands").glob("*.md"))

    missing_agents = REQUIRED_AGENTS - {path.stem for path in agents}
    missing_skills = REQUIRED_SKILLS - {path.parent.name for path in skills}
    missing_commands = REQUIRED_COMMANDS - {path.stem for path in commands}
    if missing_agents:
        errors.append(f"missing required project agents: {sorted(missing_agents)}")
    if missing_skills:
        errors.append(f"missing required project skills: {sorted(missing_skills)}")
    if missing_commands:
        errors.append(f"missing required project commands: {sorted(missing_commands)}")

    for path in agents:
        errors.extend(has_frontmatter(path, ("name", "description")))
    for path in skills:
        errors.extend(has_frontmatter(path, ("name", "description")))
        errors.extend(skill_name_matches_dir(path))
    for path in commands:
        errors.extend(has_frontmatter(path, ("description",)))

    for hook in ("guard_project_writes.py", "trace_hook.py"):
        if not (OPENCODE / "hooks" / hook).is_file():
            errors.append(f"missing hook: .opencode/hooks/{hook}")

    for plugin in ("guard-project-writes.js", "trace-hook.js"):
        path = OPENCODE / "plugins" / plugin
        if not path.is_file():
            errors.append(f"missing plugin: .opencode/plugins/{plugin}")
        elif '".opencode"' not in path.read_text(encoding="utf-8"):
            errors.append(f"{path}: plugin must reference the .opencode hooks path")

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
        {"valid": not errors, "agents": len(agents), "skills": len(skills),
         "commands": len(commands), "errors": errors},
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
