"""Shared path resolution and configuration for the opci package.

Two distinct path domains:
- PACKAGE_ROOT: the installed package location (pip site-packages or editable install)
  Used for finding bundled resources (prompts, configs, templates)
- PROJECT_ROOT: the user's working directory (where runs/, servers.json, operator_docs/ live)
  Determined by .opci_project_root marker file (created by opci setup), with
  CLAUDE_PROJECT_DIR fallback. Walking up from cwd/env prevents subagent drift.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent

PROJECT_ROOT_MARKER = ".opci_project_root"


def get_project_root() -> Path:
    """Return the user's project directory.

    Resolution strategy:
    1. Walk up from CLAUDE_PROJECT_DIR (or cwd) looking for .opci_project_root marker.
       The marker file is created by `opci setup` and contains the absolute path
       of the project root. This prevents subagent cwd drift and supports multiple
       projects on the same machine — each project has its own marker.
    2. If a marker is found, read and return the stored absolute path.
    3. Fallback: return CLAUDE_PROJECT_DIR or cwd.

    This approach correctly handles:
    - Multiple opci projects on one machine (each has its own marker)
    - Subagent cwd drift (CLAUDE_PROJECT_DIR pointing to a subdirectory)
    - Different users with different project directories
    """
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    start = Path(env_dir).resolve() if env_dir else Path.cwd().resolve()

    # Walk up from start looking for the marker file
    for candidate in [start] + list(start.parents):
        marker = candidate / PROJECT_ROOT_MARKER
        if marker.is_file():
            stored_root = marker.read_text(encoding="utf-8").strip()
            stored_path = Path(stored_root)
            if stored_path.is_absolute() and stored_path.is_dir():
                return stored_path.resolve()
            # Marker contains invalid path — use the directory containing the marker
            return candidate

    # Fallback: no marker found, use env var or cwd
    return start


def resolve_input_path(value: str | Path, project_root: Path | None = None) -> Path:
    """Resolve project-relative, parent-relative, or absolute user input."""
    root = project_root or get_project_root()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


# ---------------------------------------------------------------------------
# Prompt discovery
# ---------------------------------------------------------------------------

OPERATOR_PROMPT_PATTERN = re.compile(
    r"^operator_constraints_extract_v(?P<version>\d+)\.md$"
)


def prompt_directory(project_root: Path | None = None) -> Path:
    """Prompt lookup: project directory first (evolved versions), then package fallback."""
    root = project_root or get_project_root()
    local = root / "prompts"
    if local.is_dir() and any(local.glob("operator_constraints_extract_v*.md")):
        return local
    bundled = PACKAGE_ROOT / "resources" / "prompts"
    if bundled.is_dir():
        return bundled
    return local  # fallback (may not exist)


def find_latest_operator_prompt(directory: Path | None = None) -> Path | None:
    """Return the highest numerically versioned operator extraction prompt."""
    prompt_dir = (directory or prompt_directory()).resolve()
    candidates: list[tuple[int, Path]] = []
    if not prompt_dir.is_dir():
        return None
    for path in prompt_dir.iterdir():
        if not path.is_file():
            continue
        match = OPERATOR_PROMPT_PATTERN.fullmatch(path.name)
        if match:
            candidates.append((int(match.group("version")), path.resolve()))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


# ---------------------------------------------------------------------------
# Server config validation (exposes structure errors, never credential values)
# ---------------------------------------------------------------------------

def validate_server_config(value: str | Path, project_root: Path | None = None) -> tuple[Path, list[str]]:
    """Validate server config without exposing credential values."""
    path = resolve_input_path(value, project_root)
    if not path.is_file():
        return path, [f"服务器配置文件不存在: {path}"]
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return path, [f"服务器配置不是合法 JSON: {exc}"]
    if not isinstance(payload, dict):
        return path, ["服务器配置根节点必须是 JSON object"]
    servers = payload.get("servers")
    if not isinstance(servers, list) or not servers:
        return path, ["服务器配置必须包含非空 servers 数组"]

    errors: list[str] = []
    required = ("ip", "username", "password")
    for index, server in enumerate(servers):
        if not isinstance(server, dict):
            errors.append(f"servers[{index}] 必须是 object")
            continue
        missing = [key for key in required if not str(server.get(key) or "").strip()]
        if missing:
            errors.append(f"servers[{index}] 缺少字段: {', '.join(missing)}")
        platforms = server.get("platforms")
        if not isinstance(platforms, list) or not platforms:
            errors.append(f"servers[{index}].platforms 必须是非空数组")
    return path, errors


def config_error_payload(path: Path, errors: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "requires_user_action": True,
        "code": "REAL_EXECUTION_CONFIG_REQUIRED",
        "message": (
            "默认使用真实用例执行，但服务器配置缺失或不完整。"
            "请复制 servers.example.json 为 servers.json 并填写连接信息；"
            "如仅需演练流程，请显式传入 --mode mock。"
        ),
        "server_config": str(path),
        "errors": errors,
    }
