"""Shared path resolution and real-execution configuration validation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIRECTORY = ROOT / "prompts"
OPERATOR_PROMPT_PATTERN = re.compile(
    r"^operator_constraints_extract_v(?P<version>\d+)\.md$"
)


def resolve_input_path(value: str | Path) -> Path:
    """Resolve project-relative, parent-relative, or absolute user input."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def find_latest_operator_prompt(directory: Path | None = None) -> Path | None:
    """Return the highest numerically versioned operator extraction prompt."""
    prompt_dir = (directory or PROMPT_DIRECTORY).resolve()
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


def validate_server_config(value: str | Path) -> tuple[Path, list[str]]:
    """Validate server config without exposing credential values."""
    path = resolve_input_path(value)
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

    valid_transfer_modes = {"auto", "scp", "sftp", ""}
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
        # Optional: validate transfer_mode
        tm = server.get("transfer_mode")
        if tm is not None and str(tm).strip().lower() not in valid_transfer_modes:
            errors.append(
                f"servers[{index}].transfer_mode 必须是 auto / scp / sftp 之一"
            )
        # Optional: validate remote_paths structure
        rp = server.get("remote_paths")
        if rp is not None and not isinstance(rp, dict):
            errors.append(f"servers[{index}].remote_paths 必须是 object")
        # Optional: validate fusion config (supports_fusion + fusion_devices)
        supports_fusion = server.get("supports_fusion")
        if supports_fusion is not None and not isinstance(supports_fusion, bool):
            errors.append(f"servers[{index}].supports_fusion 必须是 bool")
        if supports_fusion:
            fusion_devices = server.get("fusion_devices")
            if not isinstance(fusion_devices, list) or len(fusion_devices) != 2 or not all(
                isinstance(d, int) and d >= 0 for d in fusion_devices
            ):
                errors.append(
                    f"servers[{index}].fusion_devices 必须是长度 2 的非负整数数组 (card_1, card_2)；supports_fusion=true 时必填"
                )
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
