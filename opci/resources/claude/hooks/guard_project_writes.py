#!/usr/bin/env python3
"""Block Bash writes/deletes outside the Claude project directory.

Claude Code's OS sandbox is the primary boundary. This hook is a portable
fallback for native Windows and also gives a clear denial reason.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

WRITE_OR_DELETE = re.compile(
    r"""(?ix)
    (?<![\w-])
    (
      remove-item|del(?:ete)?|erase|rm|rmdir|
      move-item|move|mv|
      copy-item|copy|cp|
      set-content|add-content|out-file|tee|
      new-item|mkdir|md|touch
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
      /[^\s;&|<>]+
    )
    """
)

REDIRECTION = re.compile(
    r"""(?x)(?:^|[\s\d])(?:>>?|2>>?)\s*
    (?:"(?P<double>[^"]+)"|'(?P<single>[^']+)'|(?P<bare>[^\s;&|]+))
    """
)


def project_root(payload: dict) -> Path:
    value = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or "."
    return Path(value).resolve()


def is_inside(path_text: str, root: Path) -> bool:
    expanded = os.path.expandvars(os.path.expanduser(path_text.strip()))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate.resolve(strict=False).relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def extracted_paths(text: str, pattern: re.Pattern[str]) -> list[str]:
    paths: list[str] = []
    for match in pattern.finditer(text):
        value = match.groupdict().get("double") or match.groupdict().get("single")
        value = value or match.groupdict().get("bare")
        if value:
            paths.append(value.rstrip(",)"))
    return paths


def deny(reason: str) -> int:
    print(reason, file=sys.stderr)
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return deny("无法解析 Bash 权限输入，已拒绝执行。")

    command = str((payload.get("tool_input") or {}).get("command") or "")
    root = project_root(payload)

    # Shell redirection is a write even when the command itself looks read-only.
    for target in extracted_paths(command, REDIRECTION):
        if not is_inside(target, root):
            return deny(f"禁止向项目目录外重定向写入: {target}")

    for match in WRITE_OR_DELETE.finditer(command):
        # Inspect this command segment only, up to the next shell separator.
        segment = re.split(r"(?:&&|\|\||[;&|\n])", command[match.end() :], maxsplit=1)[0]
        if re.search(r"[$%][A-Za-z_{]", segment):
            return deny(
                f"禁止使用未解析变量执行写入/删除命令 {match.group(1)}；"
                "请改用项目目录内的明确路径。"
            )
        for target in extracted_paths(segment, EXTERNAL_PATH):
            if not is_inside(target, root):
                return deny(f"禁止 {match.group(1)} 操作项目目录外路径: {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

