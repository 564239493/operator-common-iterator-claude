"""run_management MCP tools: init_run, find_latest_operator_prompt, validate_server_config, update_run_state, read_operator_prompt, write_operator_prompt."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opci.config import (
    config_error_payload,
    find_latest_operator_prompt,
    get_project_root,
    prompt_directory,
    resolve_input_path,
    validate_server_config,
)

from fastmcp import FastMCP

# We'll import mcp from server.py at registration time
# Tool functions are standalone and decorated when imported by server.py


def init_run(
    doc: str,
    prompt: str | None = None,
    max_iterations: int = 5,
    case_count: int = 10,
    mode: str = "real",
    server_config: str = "servers.json",
) -> dict[str, Any]:
    """Create a run directory and initial workflow state."""
    project_root = get_project_root()
    doc_path = resolve_input_path(doc, project_root)
    prompt_path = (
        resolve_input_path(prompt, project_root)
        if prompt
        else find_latest_operator_prompt()
    )

    if not doc_path.is_file():
        return {
            "ok": False,
            "requires_user_action": True,
            "code": "OPERATOR_DOC_NOT_FOUND",
            "message": "算子文档不存在，请提供绝对路径、项目相对路径或包含 .. 的相对路径。",
            "operator_doc": str(doc_path),
        }
    if prompt_path is None or not prompt_path.is_file():
        return {
            "ok": False,
            "requires_user_action": True,
            "code": "PROMPT_NOT_FOUND",
            "message": "约束提取提示词不存在。请通过 --prompt 指定文件，或在 prompts 目录提供 operator_constraints_extract_vN.md。",
            "prompt": str(prompt_path) if prompt_path else "",
        }
    if max_iterations < 1 or case_count < 1:
        return {"ok": False, "message": "max-iterations and case-count must be positive"}

    server_config_path: Path | None = None
    if mode == "real":
        server_config_path, config_errors = validate_server_config(server_config, project_root)
        if config_errors:
            return config_error_payload(server_config_path, config_errors)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_id = f"{doc_path.stem}-{stamp}"
    run_dir = project_root / "runs" / run_id
    input_dir = run_dir / "inputs"
    (run_dir / "iter_001").mkdir(parents=True, exist_ok=False)
    input_dir.mkdir(parents=True, exist_ok=False)

    doc_snapshot = input_dir / doc_path.name
    prompt_snapshot = input_dir / "prompt_v1.md"
    shutil.copy2(doc_path, doc_snapshot)
    shutil.copy2(prompt_path, prompt_snapshot)

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "run_id": run_id,
        "operator_doc_source": str(doc_path),
        "operator_doc": str(doc_snapshot),
        "current_prompt_source": str(prompt_path),
        "current_prompt": str(prompt_snapshot),
        "mode": mode,
        "server_config": str(server_config_path) if server_config_path else "",
        "max_iterations": max_iterations,
        "case_count": case_count,
        "current_iteration": 1,
        "state": "PLAN",
        "history": [{"state": "PLAN", "at": now}],
        "created_at": now,
        "updated_at": now,
    }
    (run_dir / "run_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "ok": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "operator_doc_source": str(doc_path),
        "operator_doc_snapshot": str(doc_snapshot),
        "prompt_snapshot": str(prompt_snapshot),
        "mode": mode,
        "server_config": str(server_config_path) if server_config_path else "",
    }


def update_run_state(
    run_dir: str,
    state: str,
    iteration: int | None = None,
) -> dict[str, Any]:
    """Update run_state.json state and iteration."""
    run_dir_path = resolve_input_path(run_dir)
    state_path = run_dir_path / "run_state.json"
    if not state_path.is_file():
        return {"ok": False, "message": f"run_state.json not found: {state_path}"}

    run_state: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    run_state["state"] = state
    run_state["updated_at"] = now
    if iteration is not None:
        run_state["current_iteration"] = iteration
    run_state["history"].append({"state": state, "at": now})

    state_path.write_text(
        json.dumps(run_state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "run_id": run_state["run_id"], "state": state}


def read_operator_prompt(run_dir: str) -> dict[str, Any]:
    """Read the current operator prompt content from run inputs."""
    run_dir_path = resolve_input_path(run_dir)
    # Find the prompt file in inputs/
    prompt_files = sorted((run_dir_path / "inputs").glob("prompt_v*.md"))
    if not prompt_files:
        return {"ok": False, "message": "No prompt file found in run inputs/"}
    latest = prompt_files[-1]
    content = latest.read_text(encoding="utf-8")
    return {
        "ok": True,
        "path": str(latest),
        "version": latest.stem,  # e.g. "prompt_v1"
        "content": content,
    }


def write_operator_prompt(
    run_dir: str,
    iter_dir: str,
    content: str,
    version: int,
) -> dict[str, Any]:
    """Write an optimized prompt to both iter/ snapshot and project prompts/ directory."""
    project_root = get_project_root()
    run_dir_path = resolve_input_path(run_dir)
    iter_dir_path = resolve_input_path(iter_dir)

    # Write to iter/ snapshot
    iter_prompt = iter_dir_path / f"prompt_v{version}.md"
    iter_prompt.write_text(content, encoding="utf-8")

    # Write to project prompts/ directory
    project_prompt = project_root / "prompts" / f"operator_constraints_extract_v{version}.md"
    (project_root / "prompts").mkdir(exist_ok=True)
    project_prompt.write_text(content, encoding="utf-8")

    return {
        "ok": True,
        "iter_prompt_path": str(iter_prompt),
        "project_prompt_path": str(project_prompt),
        "version": version,
    }
