#!/usr/bin/env python3
"""Static validation for the opencode native project scaffold."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_EDIT_DENY_FRAGMENTS = (
    "executer/**",
    "agent/generators/**",
    ".git/**",
    "servers.json",
    "run_state.json",
)
REQUIRED_BASH_DENY_PATTERNS = (
    "python -c*", "python3 -c*", "*python -c *", "*python3 -c *",
    "node -e*", "node --eval*",
)
REQUIRED_READ_DENY_PATTERNS = (".env", ".env.*", "**/.env", "**/.env.*")
# 插件层放行、靠静态规则兜底的「兜底契约」：guard.test.js 的放行用例（rm/python /*
# 等「插件不再拦」注释）依赖这些规则存在——删掉后测试与校验仍会绿，防线却消失，
# 故在此钉住（删除或改名任何一条都会让校验变红）。
REQUIRED_BASH_ASK_RULES = (
    "rm *", "rmdir *", "mv *", "move *", "del *", "erase *",
    "python /*", "python3 /*", "py /*",
    "git apply*", "git restore*", "rsync *",
    "curl *", "wget *", "sudo *",
)
CPU_GOLDEN_GUIDES = (
    ROOT / ".opencode" / "skills" / "atc-cpu-golden-derivation" / "SKILL.md",
    ROOT / "executer" / "resources" / "aclnn-cpu-golden-derivation.md",
)
RUN_DOC_SNAPSHOT = "runs/<current-run>/inputs/<operator-doc>.md"
WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Z0-9])(?:[A-Z]:[\\/]|\\\\[^\\\s]+[\\/])"
)
COLOR_PATTERN = re.compile(
    r"(?i)^#[0-9a-f]{6}$|^(?:primary|secondary|accent|success|warning|error|info)$"
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
    "repair-constraints", "scan-scenes",
    "supplement-constraints", "update-constraints", "validate-run",
}
REQUIRED_COMMANDS = {
    "iterate-operator", "iterate-directory", "show-workforce",
}


def has_frontmatter(path: Path, required: tuple[str, ...]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not match:
        return [f"{path}: missing YAML frontmatter"]
    block = match.group(1)
    return [f"{path}: missing {key}" for key in required if not re.search(rf"^{key}:", block, re.M)]


def frontmatter_of(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not match:
        return {}
    data: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        if ":" not in raw or raw.startswith(" "):
            continue
        key, value = raw.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        data[key.strip()] = value
    return data


# 中文语境里的大写工具名 = Claude 时代残留（opencode 工具名全小写：read/write/edit/
# glob/grep/bash/task/skill）。仅检查含 CJK 字符的行：纯英文行里的祈使句动词不算。
# 「Agent」不查——它是概念词（子 Agent/主 Agent），不是工具指名。
_CJK_RE = re.compile(r"[一-鿿]")
_UPPER_TOOL_RE = re.compile(r"\b(Read|Write|Edit|Glob|Grep|Bash|Skill|Task)\b")


def validate_lowercase_tool_names() -> list[str]:
    errors: list[str] = []
    paths = list((ROOT / ".opencode" / "agent").glob("*.md")) + list(
        (ROOT / ".opencode" / "skills").glob("*/SKILL.md")
    )
    for path in paths:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _CJK_RE.search(line):
                for word in _UPPER_TOOL_RE.findall(line):
                    rel = path.relative_to(ROOT)
                    errors.append(f"{rel}:{lineno}: 大写工具名 {word!r}（应为小写 read/edit/bash/...）")
    return errors


def validate_opencode_json() -> list[str]:
    errors: list[str] = []
    config_path = ROOT / ".opencode" / "opencode.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"invalid .opencode/opencode.json: {exc}"]

    permission = data.get("permission", {})
    if not isinstance(permission, dict):
        return ["opencode.json: permission must be an object"]

    edit_rules = permission.get("edit", {})
    if not isinstance(edit_rules, dict):
        errors.append("permission.edit must be an object of pattern->action rules")
        edit_patterns = ""
    else:
        edit_patterns = " ".join(edit_rules.keys())
    for fragment in REQUIRED_EDIT_DENY_FRAGMENTS:
        if fragment.rstrip("*").rstrip("/") not in edit_patterns:
            errors.append(f"permission.edit missing deny pattern for: {fragment}")

    read_rules = permission.get("read", {}) if isinstance(permission.get("read"), dict) else {}
    for pattern in REQUIRED_READ_DENY_PATTERNS:
        if read_rules.get(pattern) != "deny":
            errors.append(f"permission.read missing deny pattern: {pattern}")

    bash_rules = permission.get("bash", {})
    if not isinstance(bash_rules, dict) or bash_rules.get("*") != "allow":
        errors.append("permission.bash must keep '*': 'allow' as the first broad rule")
    elif next(iter(bash_rules), None) != "*":
        errors.append("permission.bash must list '*': 'allow' first (last matching rule wins)")
    for pattern in REQUIRED_BASH_DENY_PATTERNS:
        if bash_rules.get(pattern) != "deny":
            errors.append(f"permission.bash missing deny pattern: {pattern}")
    if bash_rules.get("pip install*") != "ask":
        errors.append("permission.bash should ask for 'pip install*'")
    for pattern in REQUIRED_BASH_ASK_RULES:
        if bash_rules.get(pattern) != "ask":
            errors.append(f"permission.bash missing bottom-line ask rule: {pattern}")

    plugins = ROOT / ".opencode" / "plugins"
    if not (plugins / "guard.js").is_file():
        errors.append("missing .opencode/plugins/guard.js")
    return errors


def validate_guard_loads() -> list[str]:
    """guard.js 必须能被正常加载。

    opencode 对插件加载期失败（语法损坏/依赖缺失）会吞错继续运行——动态守卫
    静默消失且无任何提示（fail-open）。此处在提交闸门上显式校验。
    """
    import shutil

    bun = shutil.which("bun")
    if not bun:
        return ["bun 未安装：无法校验 guard.js 可加载性（opencode 插件运行时即 bun）"]
    guard = ROOT / ".opencode" / "plugins" / "guard.js"
    proc = subprocess.run(
        [bun, "-e", f"await import({str(guard)!r})"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["<无输出>"]
        return [f"guard.js 无法加载（加载期 fail-open 风险）: {detail[0][:200]}"]
    return []


def _py_frozenset_of(text: str, name: str) -> set[str] | None:
    # 兼容 frozenset({...}) 与普通集合字面量 {...} 两种写法。
    m = re.search(rf"{name}\s*=\s*(?:frozenset\(\s*)?\{{(.*?)\}}", text, re.S)
    if not m:
        return None
    return set(re.findall(r'"([A-Z_]+)"', m.group(1)))


def _js_set_of(text: str, name: str) -> set[str] | None:
    m = re.search(rf"{name}\s*=\s*new Set\(\[(.*?)\]\)", text, re.S)
    if not m:
        return None
    return set(re.findall(r'"([A-Z_]+)"', m.group(1)))


def validate_terminal_state_sets() -> list[str]:
    """终态集合在全部定义处必须逐项一致（单点漂移是历史事故源，无交叉校验即静默分叉）。"""
    authority = _py_frozenset_of(
        (ROOT / "scripts" / "flow_control.py").read_text(encoding="utf-8"),
        "TERMINAL_STATES",
    )
    if authority is None:
        return ["flow_control.py: 无法解析 TERMINAL_STATES（权威定义）"]

    sources: dict[str, set[str] | None] = {}
    guard_text = (ROOT / ".opencode" / "plugins" / "guard.js").read_text(encoding="utf-8")
    sources["guard.js"] = _js_set_of(guard_text, "TERMINAL_STATES")
    sources["init_run.py"] = _py_frozenset_of(
        (ROOT / "scripts" / "init_run.py").read_text(encoding="utf-8"),
        "RESUME_TERMINAL_STATES",
    )
    sources["workbench/config.py"] = _py_frozenset_of(
        (ROOT / "src" / "workbench" / "config.py").read_text(encoding="utf-8"),
        "TERMINAL_STATES",
    )
    batch_text = (ROOT / "scripts" / "batch_state.py").read_text(encoding="utf-8")
    batch_parts = [
        _py_frozenset_of(batch_text, name)
        for name in ("SUCCESS_STATES", "FAILURE_STATES", "STOPPED_STATES")
    ]
    sources["batch_state.py"] = (
        set().union(*batch_parts) if all(p is not None for p in batch_parts) else None
    )

    errors = []
    for where, value in sources.items():
        if value is None:
            errors.append(f"{where}: 无法解析终态集合字面量")
        elif value != authority:
            missing = sorted(authority - value)
            extra = sorted(value - authority)
            errors.append(
                f"{where}: 终态集合与 flow_control.py 不一致 "
                f"(缺失={missing} 多出={extra})"
            )
    return errors


def main() -> int:
    errors: list[str] = []
    errors.extend(validate_opencode_json())
    errors.extend(validate_lowercase_tool_names())
    errors.extend(validate_guard_loads())
    errors.extend(validate_terminal_state_sets())

    agents = list((ROOT / ".opencode" / "agent").glob("*.md"))
    skills = list((ROOT / ".opencode" / "skills").glob("*/SKILL.md"))
    commands = list((ROOT / ".opencode" / "command").glob("*.md"))
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
        errors.extend(has_frontmatter(path, ("description", "mode")))
        text = path.read_text(encoding="utf-8")
        if "用 skill 工具加载" not in text:
            errors.append(f"{path}: agent must preload its stage skill via skill tool")
        meta = frontmatter_of(path)
        color = str(meta.get("color") or "")
        if color and not COLOR_PATTERN.fullmatch(color):
            errors.append(
                f"{path}: color must be #RRGGBB hex or theme enum, got: {color}"
            )
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
    for path in ("AGENTS.md", "docs/WORKFLOW.md", "docs/OBSERVABILITY.md", "docs/ARTIFACT_CONTRACTS.md"):
        if not (ROOT / path).is_file():
            errors.append(f"missing {path}")
    # 本机用 Claude Code 做开发时 .claude/settings.local.json 会随时重建（本地权限记忆），
    # 因此这里只检查 git 跟踪状态：分支不得跟踪任何 Claude 资产。
    tracked = subprocess.run(
        ["git", "ls-files", "--", ".claude", "CLAUDE.md"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.split()
    if tracked:
        errors.append(
            "opencode-native 分支不得跟踪 Claude 资产: " + ", ".join(sorted(tracked))
        )

    print(json.dumps(
        {"valid": not errors, "agents": len(agents), "skills": len(skills), "commands": len(commands), "errors": errors},
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
