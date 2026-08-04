#!/usr/bin/env python3
"""Enforce protected-source and per-run access boundaries for Claude tools.

The Claude sandbox is the OS-level boundary on Linux/macOS/WSL2.  This hook is
also required because native Windows has no Claude sandbox and because the
active ``runs/<run-id>`` directory is dynamic.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PROTECTED_WRITE_DIRS = ("executer", "agent/generators", ".git")
PROTECTED_WRITE_FILES = ("servers.json",)

# 维护期豁免前缀：非空时精确豁免指定文件/目录的写保护（如维护任务临时使用），
# 当前为空：问题8修复已完成，恢复原只读保护语义。
WRITE_EXEMPT_PREFIXES: tuple[str, ...] = ()

TERMINAL_STATES = {
    "SUCCESS",
    "BLOCKED",
    "MAX_ITERATIONS",
    "STOP_GENERATOR_BUG",
    "STOP_EXECUTOR_BUG",
}
FILE_READ_TOOLS = {"Read", "Glob", "Grep"}
FILE_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

WRITE_OR_DELETE = re.compile(
    r"""(?ix)
    (?<![\w-])
    (
      remove-item|del(?:ete)?|erase|rm|rmdir|
      move-item|move|mv|
      copy-item|copy|cp|
      set-content|add-content|out-file|tee|
      new-item|mkdir|(?<!\.)md|touch
    )
    (?![\w-])
    """
)
EXTERNAL_PATH = re.compile(
    r"""(?x)
    "(?P<double>[^"]+)" |
    '(?P<single>[^']+)' |
    (?P<bare>
      [A-Za-z]:[\\/][^\s;&|<>]+ |
      \\\\[^\s;&|<>]+ |
      \.\.[\\/][^\s;&|<>]+ |
      ~[\\/][^\s;&|<>]+ |
      (?<![\w.])/[^\s;&|<>]+
    )
    """
)
REDIRECTION = re.compile(
    r"""(?x)(?:^|[\s\d])(?:>>?|2>>?)\s*
    (?:"(?P<double>[^"]+)"|'(?P<single>[^']+)'|(?P<bare>[^\s;&|]+))
    """
)
RUN_REFERENCE = re.compile(
    r"(?i)(?:^|[\s\"'=:(\\/])(?:\./|\.\\)?runs[\\/](?!batches(?:[\\/]|$))"
    r"(?P<run_id>[^\\/\s\"';&|,)]+)"
)
RUN_TRAVERSAL = re.compile(
    r"(?i)(?:^|[\s\"'=:(\\/])(?:\./|\.\\)?runs[\\/]"
    r"[^\\/\s\"';&|,)]+[\\/]\.\.(?:[\\/]|$)"
)
PROTECTED_SHELL_REFERENCE = re.compile(
    r"(?i)(?<![\w.-])(?:executer|agent/generators|servers\.json|\.git)"
    r"(?=$|[/\s\"';&|,)])"
)
def _shell_refs_exempt(segment: str) -> bool:
    # 维护期临时豁免：受保护引用全部落在豁免前缀内时放行；
    # 豁免前缀为空（常态）时恒为 False，保持原保护语义。
    for match in PROTECTED_SHELL_REFERENCE.finditer(segment):
        rest = segment[match.end() :].split(None, 1)
        ref = match.group(0) + (rest[0] if rest else "")
        if not any(
            ref == prefix.lower() or ref.startswith(prefix.lower() + "/")
            for prefix in WRITE_EXEMPT_PREFIXES
        ):
            return False
    return True



INLINE_PYTHON = re.compile(
    r"(?i)(?<![\w.-])(?:python(?:3(?:\.\d+)?)?|python\.exe)"
    r"\s+(?:-c(?:\s|$)|-(?:\s|$))"
)
# Match Python only when it is the executable at the start of a shell segment.
# Merely listing `.venv/Scripts/python.exe` must not be treated as execution.
PYTHON_COMMAND = re.compile(
    r'''(?ix)
    (?:^|&&|\|\||[;|\n])\s*
    (?:
      "[^"]*[\\/]python(?:3(?:\.\d+)?)?(?:\.exe)?" |
      '[^']*[\\/]python(?:3(?:\.\d+)?)?(?:\.exe)?' |
      [^\s;&|]*(?:python(?:3(?:\.\d+)?)?|python\.exe)
    )
    (?=\s)
    '''
)
DESTRUCTIVE_COMMAND = re.compile(
    r"(?ix)(?<![\w-])(?:remove-item|del(?:ete)?|erase|rm|rmdir|"
    r"move-item|move|mv)(?![\w-])"
)
HIGH_RISK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)(?<![\w-])(?:pip(?:3)?\s+(?:install|uninstall)|"
            r"python(?:3)?(?:\.exe)?\s+-m\s+(?:pip|venv)|"
            r"uv\s+(?:add|remove|sync|pip)|npm\s+(?:install|uninstall)|"
            r"apt(?:-get)?\s+|dnf\s+|yum\s+|winget\s+|choco\s+)"
        ),
        "依赖或环境变更",
    ),
    (
        re.compile(
            r"(?i)(?<![\w-])(?:source|eval|invoke-expression|iex|"
            r"bash\s+-c|sh\s+-c|powershell(?:\.exe)?\s+-encodedcommand)"
            r"(?![\w-])"
        ),
        "Shell 求值",
    ),
    (
        re.compile(r"(?i)(?<![\w-])(?:curl|wget)(?![\w-])"),
        "外部内容下载",
    ),
    (
        re.compile(
            r"(?i)(?<![\w-])git\s+(?:add|commit|push|pull|merge|rebase|"
            r"reset|checkout|switch|clean|stash|tag)(?:\s|$)"
        ),
        "Git 状态变更",
    ),
    (
        re.compile(
            r"(?i)(?<![\w-])(?:sudo|su|chmod|chown|kill|killall|taskkill|"
            r"stop-process|setx|reg\s+(?:add|delete))(?![\w-])"
        ),
        "系统或进程变更",
    ),
)


def project_root(payload: dict[str, Any]) -> Path:
    value = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or "."
    return Path(value).resolve()


def is_inside(path_text: str, root: Path) -> bool:
    """Return True when a possibly relative path resolves under ``root``."""
    expanded = os.path.expandvars(os.path.expanduser(path_text.strip()))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate.resolve(strict=False).relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def resolved_path(path_text: str, root: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(path_text.strip()))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve(strict=False)


def is_null_sink(path_text: str) -> bool:
    """Return True for platform null devices, which never persist output."""
    normalized = path_text.strip().strip("\"'").replace("\\", "/").lower()
    return normalized in {"/dev/null", "nul", "nul:", "$null"}


def relative_parts(path: Path, root: Path) -> tuple[str, ...] | None:
    try:
        return path.relative_to(root).parts
    except (OSError, ValueError):
        return None


def run_id_for_path(path: Path, root: Path) -> str | None:
    parts = relative_parts(path, root)
    if not parts or parts[0].lower() != "runs" or len(parts) < 2:
        return None
    if parts[1].lower() == "batches":
        return None
    return parts[1]


def run_key(run_id: str) -> str:
    """Apply the host filesystem's case-normalization rules to a run id."""
    return os.path.normcase(run_id)


def is_runs_container(path: Path, root: Path) -> bool:
    parts = relative_parts(path, root)
    return parts == () or parts == ("runs",)


def is_protected_write(path: Path, root: Path) -> bool:
    parts = relative_parts(path, root)
    if not parts:
        return False
    lowered = tuple(part.lower() for part in parts)
    if lowered[0] in {name.lower() for name in PROTECTED_WRITE_FILES}:
        return True
    joined = "/".join(lowered)
    if any(
        joined == name.lower() or joined.startswith(name.lower() + "/")
        for name in WRITE_EXEMPT_PREFIXES
    ):
        return False

    return any(
        joined == name.lower() or joined.startswith(name.lower() + "/")
        for name in PROTECTED_WRITE_DIRS
    )


def scope_file(payload: dict[str, Any], root: Path) -> Path:
    session = re.sub(r"[^A-Za-z0-9_.-]", "_", str(payload.get("session_id") or "unknown"))
    # All subagents in one Claude session must inherit the same run boundary.
    return root / ".claude" / "runtime" / "task_scopes" / f"{session}.json"


def read_scope(payload: dict[str, Any], root: Path) -> str | None:
    path = scope_file(payload, root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return str(value["run_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_scope(payload: dict[str, Any], root: Path, run_id: str) -> None:
    target = scope_file(payload, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"run_id": run_id}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)


def run_is_terminal(root: Path, run_id: str) -> bool:
    try:
        state = json.loads(
            (root / "runs" / run_id / "run_state.json").read_text(encoding="utf-8")
        )
        return state.get("state") in TERMINAL_STATES
    except (OSError, ValueError, TypeError):
        return False


def bind_scope(
    payload: dict[str, Any], root: Path, run_id: str
) -> tuple[bool, str | None]:
    current = read_scope(payload, root)
    if current and run_key(current) == run_key(run_id):
        return True, None
    if current and not run_is_terminal(root, current):
        return False, f"当前任务已绑定 runs/{current}，禁止切换或访问 runs/{run_id}"
    write_scope(payload, root, run_id)
    return True, None


def extracted_paths(text: str, pattern: re.Pattern[str]) -> list[str]:
    paths: list[str] = []
    for match in pattern.finditer(text):
        value = match.groupdict().get("double") or match.groupdict().get("single")
        value = value or match.groupdict().get("bare")
        if value:
            paths.append(value.rstrip(",)"))
    return paths


def tool_paths(tool_name: str, tool_input: dict[str, Any], root: Path) -> list[Path]:
    if tool_name in {"Read", "Edit", "Write"}:
        value = tool_input.get("file_path")
    elif tool_name == "NotebookEdit":
        value = tool_input.get("notebook_path")
    elif tool_name in {"Glob", "Grep"}:
        value = tool_input.get("path") or str(root)
    else:
        value = None
    return [resolved_path(str(value), root)] if value else []


def guard_file_tool(payload: dict[str, Any], root: Path) -> str | None:
    tool_name = str(payload.get("tool_name") or "")
    paths = tool_paths(tool_name, payload.get("tool_input") or {}, root)
    current = read_scope(payload, root)

    for path in paths:
        if tool_name in FILE_WRITE_TOOLS and is_protected_write(path, root):
            return f"受保护路径只允许读取/执行，禁止修改: {path}"

        candidate = run_id_for_path(path, root)
        if current:
            if is_runs_container(path, root) and tool_name in {"Glob", "Grep"}:
                return (
                    f"当前任务只允许读取 runs/{current}；"
                    "请把 Glob/Grep 的 path 缩小到当前任务或非 runs 目录"
                )
            if candidate and run_key(candidate) != run_key(current):
                return f"当前任务只允许访问 runs/{current}，禁止访问 runs/{candidate}"
            if tool_name in FILE_WRITE_TOOLS and (
                candidate is None or run_key(candidate) != run_key(current)
            ):
                return f"任务执行期间只允许写入 runs/{current}: {path}"
        elif candidate:
            ok, reason = bind_scope(payload, root, candidate)
            if not ok:
                return reason
    return None


def guard_shell(payload: dict[str, Any], root: Path) -> str | None:
    command = str((payload.get("tool_input") or {}).get("command") or "")
    current = read_scope(payload, root)

    if INLINE_PYTHON.search(command):
        return (
            "禁止 python -c、python - 和 heredoc 内联代码；"
            "请运行现有 scripts/*.py，或使用 Read/Glob/Grep 检查文件"
        )

    if RUN_TRAVERSAL.search(command):
        return "禁止通过 runs/<run-id>/../ 跨任务访问"

    referenced_runs = {match.group("run_id") for match in RUN_REFERENCE.finditer(command)}
    referenced_by_key = {run_key(run_id): run_id for run_id in referenced_runs}
    if not current and len(referenced_by_key) > 1:
        return f"单条命令禁止访问多个任务: {sorted(referenced_runs)}"
    if not current and len(referenced_by_key) == 1:
        run_id = next(iter(referenced_by_key.values()))
        ok, reason = bind_scope(payload, root, run_id)
        if not ok:
            return reason
        current = run_id
    if current and any(key != run_key(current) for key in referenced_by_key):
        other = sorted(
            run_id
            for key, run_id in referenced_by_key.items()
            if key != run_key(current)
        )
        return f"当前任务只允许访问 runs/{current}，命令引用了其他任务: {other}"

    for target in extracted_paths(command, REDIRECTION):
        if is_null_sink(target):
            continue
        path = resolved_path(target, root)
        if not is_inside(target, root):
            return f"禁止向项目目录外重定向写入: {target}"
        if is_protected_write(path, root):
            return f"受保护路径只允许读取/执行，禁止重定向写入: {target}"
        candidate = run_id_for_path(path, root)
        if current and (
            candidate is None or run_key(candidate) != run_key(current)
        ):
            return f"任务执行期间只允许写入 runs/{current}: {target}"

    for match in WRITE_OR_DELETE.finditer(command):
        segment = re.split(r"(?:&&|\|\||[;&|\n])", command[match.end() :], maxsplit=1)[0]
        normalized_segment = segment.replace("\\", "/").lower()
        if PROTECTED_SHELL_REFERENCE.search(normalized_segment) and not _shell_refs_exempt(normalized_segment):
            return f"受保护路径只允许读取/执行，禁止 {match.group(1)}"
        if current and f"runs/{current.lower()}" not in normalized_segment:
            return (
                f"任务执行期间 {match.group(1)} 的写入目标必须明确位于 "
                f"runs/{current}"
            )
        if re.search(r"[$%][A-Za-z_{]", segment):
            return (
                f"禁止使用未解析变量执行写入/删除命令 {match.group(1)}；"
                "请改用明确路径"
            )
        for target in extracted_paths(segment, EXTERNAL_PATH):
            path = resolved_path(target, root)
            if not is_inside(target, root):
                return f"禁止 {match.group(1)} 操作项目目录外路径: {target}"
            if is_protected_write(path, root):
                return f"受保护路径只允许读取/执行，禁止 {match.group(1)}: {target}"
            candidate = run_id_for_path(path, root)
            if current and (
                candidate is None or run_key(candidate) != run_key(current)
            ):
                return f"任务执行期间只允许写入 runs/{current}: {target}"
    return None


def all_python_entries_are_project_files(command: str, root: Path) -> bool:
    """Allow Python file entry points anywhere inside this repository."""
    matches = list(PYTHON_COMMAND.finditer(command))
    if not matches:
        return True
    for match in matches:
        segment = re.split(
            r"(?:&&|\|\||[;&|\n])", command[match.end() :], maxsplit=1
        )[0]
        token_match = re.match(
            r'''\s*(?:"(?P<double>[^"]+)"|'(?P<single>[^']+)'|(?P<bare>\S+))''',
            segment,
        )
        if not token_match:
            return False
        entry = (
            token_match.group("double")
            or token_match.group("single")
            or token_match.group("bare")
            or ""
        )
        if not entry.lower().endswith(".py") or not is_inside(entry, root):
            return False
    return True


def shell_approval_reason(
    command: str, current: str | None, root: Path
) -> str | None:
    """Return a reason for operations which are valid but still need consent."""
    if DESTRUCTIVE_COMMAND.search(command):
        return "删除或移动操作需要用户确认"
    for pattern, label in HIGH_RISK_PATTERNS:
        if pattern.search(command):
            return f"{label}需要用户确认"
    if PYTHON_COMMAND.search(command) and not all_python_entries_are_project_files(
        command, root
    ):
        return "仅项目目录内的 Python 文件入口自动信任"
    persistent_redirection = any(
        not is_null_sink(target) for target in extracted_paths(command, REDIRECTION)
    )
    if current is None and (WRITE_OR_DELETE.search(command) or persistent_redirection):
        return "尚未绑定当前 run，Shell 写入需要用户确认"
    return None


def decision_json(decision: str, reason: str) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        },
        ensure_ascii=False,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("Permission hook received invalid JSON input; blocked.", file=sys.stderr)
        return 2

    root = project_root(payload)
    tool_name = str(payload.get("tool_name") or "")

    reason: str | None = None
    decision: str | None = None
    if tool_name in FILE_READ_TOOLS | FILE_WRITE_TOOLS:
        reason = guard_file_tool(payload, root)
        if reason:
            decision = "deny"
        elif tool_name in FILE_READ_TOOLS:
            decision = "allow"
        else:
            paths = tool_paths(tool_name, payload.get("tool_input") or {}, root)
            current = read_scope(payload, root)
            if current and paths and all(
                run_id_for_path(path, root) is not None
                and run_key(run_id_for_path(path, root) or "") == run_key(current)
                for path in paths
            ):
                decision = "allow"
    elif tool_name in SHELL_TOOLS:
        reason = guard_shell(payload, root)
        if reason:
            decision = "deny"
        else:
            command = str((payload.get("tool_input") or {}).get("command") or "")
            approval_reason = shell_approval_reason(
                command, read_scope(payload, root), root
            )
            if approval_reason:
                reason = approval_reason
                decision = "ask"
            else:
                reason = "命令已通过项目边界和高风险分类检查"
                decision = "allow"

    if decision:
        print(decision_json(decision, reason or "项目权限策略"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
