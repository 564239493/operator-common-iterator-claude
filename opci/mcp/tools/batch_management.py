"""batch_management MCP tools: init_batch, batch_claim, batch_attach_run, batch_complete, batch_show."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opci.config import (
    config_error_payload,
    find_latest_operator_prompt,
    get_project_root,
    resolve_input_path,
    validate_server_config,
)


SUCCESS_STATES = {"SUCCESS"}
FAILURE_STATES = {"BLOCKED", "MAX_ITERATIONS", "STOP_GENERATOR_BUG", "STOP_EXECUTOR_BUG"}
TERMINAL_STATES = SUCCESS_STATES | FAILURE_STATES


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(f"{path.suffix}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _load_batch(batch_dir: Path) -> dict[str, Any]:
    payload = json.loads((batch_dir / "batch_state.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("operators"), list):
        raise ValueError("batch_state.json structure invalid")
    return payload


def _save_batch(batch_dir: Path, batch: dict[str, Any]) -> None:
    _refresh_batch(batch)
    atomic_write_json(batch_dir / "batch_state.json", batch)
    atomic_write_json(batch_dir / "batch_summary.json", _summary_for(batch))


def _refresh_batch(batch: dict[str, Any]) -> None:
    counts = _counts_for(batch)
    batch["counts"] = counts
    batch["current_index"] = next(
        (item["index"] for item in batch["operators"] if item["status"] == "RUNNING"),
        None,
    )
    if counts["completed"] == counts["total"]:
        batch["state"] = "COMPLETED"
        batch["completed_at"] = batch.get("completed_at") or utc_now()
    elif batch.get("state") != "STOPPED":
        batch["state"] = "RUNNING"
        batch["completed_at"] = None
    batch["updated_at"] = utc_now()


def _counts_for(batch: dict[str, Any]) -> dict[str, int]:
    operators = batch["operators"]
    completed = [item for item in operators if item["status"] == "COMPLETED"]
    return {
        "total": len(operators),
        "pending": sum(item["status"] == "PENDING" for item in operators),
        "running": sum(item["status"] == "RUNNING" for item in operators),
        "completed": len(completed),
        "success": sum(item.get("terminal_state") in SUCCESS_STATES for item in completed),
        "failed": sum(item.get("terminal_state") in FAILURE_STATES for item in completed),
    }


def _summary_for(batch: dict[str, Any]) -> dict[str, Any]:
    return {
        "batch_id": batch["batch_id"],
        "state": batch["state"],
        "source_directory": batch["source_directory"],
        "counts": batch["counts"],
        "created_at": batch["created_at"],
        "updated_at": batch["updated_at"],
        "completed_at": batch.get("completed_at"),
        "operators": [
            {
                "index": item["index"],
                "operator_doc_source": item["operator_doc_source"],
                "status": item["status"],
                "terminal_state": item.get("terminal_state"),
                "run_id": item.get("run_id"),
                "run_dir": item.get("run_dir"),
                "message": item.get("message", ""),
            }
            for item in batch["operators"]
        ],
    }


def init_batch(
    directory: str,
    glob: str = "*.md",
    recursive: bool = False,
    prompt: str | None = None,
    max_iterations: int = 5,
    case_count: int = 10,
    mode: str = "real",
    server_config: str = "servers.json",
    continue_on_error: bool = True,
) -> dict[str, Any]:
    """Scan operator doc directory and create a resumable batch."""
    project_root = get_project_root()
    directory_path = resolve_input_path(directory, project_root)
    prompt_path = (
        resolve_input_path(prompt, project_root)
        if prompt
        else find_latest_operator_prompt()
    )

    if not directory_path.is_dir():
        return _error("OPERATOR_DIRECTORY_NOT_FOUND", "算子文档目录不存在。", directory=str(directory_path))

    server_config_path: Path | None = None
    if mode == "real":
        server_config_path, config_errors = validate_server_config(server_config, project_root)
        if config_errors:
            return config_error_payload(server_config_path, config_errors)

    try:
        iterator = directory_path.rglob(glob) if recursive else directory_path.glob(glob)
        documents = sorted(
            (path.resolve() for path in iterator if path.is_file()),
            key=lambda p: str(p.relative_to(directory_path)).casefold(),
        )
    except (OSError, ValueError) as exc:
        return _error("INVALID_GLOB", f"无法使用该 glob 扫描目录: {exc}")

    if not documents:
        return _error("NO_OPERATOR_DOCUMENTS", "目录中没有匹配的算子文档。",
                       directory=str(directory_path), glob=glob)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    batch_id = f"{directory_path.name or 'operators'}-{stamp}"
    batch_dir = project_root / "runs" / "batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)

    now = utc_now()
    batch = {
        "batch_id": batch_id,
        "source_directory": str(directory_path),
        "glob": glob,
        "recursive": recursive,
        "prompt": str(prompt_path) if prompt_path else "",
        "max_iterations": max_iterations,
        "case_count": case_count,
        "mode": mode,
        "server_config": str(server_config_path) if server_config_path else "",
        "continue_on_error": continue_on_error,
        "state": "RUNNING",
        "current_index": None,
        "counts": {},
        "operators": [
            {
                "index": index,
                "relative_path": str(path.relative_to(directory_path)),
                "operator_doc_source": str(path),
                "status": "PENDING",
                "terminal_state": None,
                "run_id": None,
                "run_dir": None,
                "run_state": None,
                "message": "",
                "started_at": None,
                "completed_at": None,
            }
            for index, path in enumerate(documents, start=1)
        ],
        "history": [{"event": "BATCH_CREATED", "at": now}],
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    _save_batch(batch_dir, batch)
    return {
        "ok": True,
        "batch_id": batch_id,
        "batch_dir": str(batch_dir),
        "total": len(documents),
        "continue_on_error": continue_on_error,
    }


def batch_claim(batch_dir: str) -> dict[str, Any]:
    """Claim the next operator in the batch."""
    batch_dir_path = resolve_input_path(batch_dir)
    if not (batch_dir_path / "batch_state.json").is_file():
        return _error("BATCH_NOT_FOUND", "批次目录缺少 batch_state.json", batch_dir=str(batch_dir_path))
    batch = _load_batch(batch_dir_path)
    current = next(
        (item for item in batch["operators"] if item["status"] == "RUNNING"),
        None,
    )
    if current is not None:
        return {"ok": True, "action": "resume", "batch_dir": str(batch_dir_path), "operator": current}
    if batch["state"] == "STOPPED":
        return {"ok": False, "code": "BATCH_STOPPED", "message": "批次已按 fail-fast 策略停止。", "batch_dir": str(batch_dir_path)}
    pending = next(
        (item for item in batch["operators"] if item["status"] == "PENDING"),
        None,
    )
    if pending is None:
        _save_batch(batch_dir_path, batch)
        return {"ok": True, "action": "complete", "batch_dir": str(batch_dir_path), "counts": batch["counts"]}
    now = utc_now()
    pending["status"] = "RUNNING"
    pending["started_at"] = now
    batch["history"].append({"event": "OPERATOR_CLAIMED", "index": pending["index"], "at": now})
    _save_batch(batch_dir_path, batch)
    return {"ok": True, "action": "start", "batch_dir": str(batch_dir_path), "operator": pending}


def batch_attach_run(batch_dir: str, run_dir: str) -> dict[str, Any]:
    """Attach a run to the current batch operator."""
    batch_dir_path = resolve_input_path(batch_dir)
    run_dir_path = resolve_input_path(run_dir)
    batch = _load_batch(batch_dir_path)
    current = next((item for item in batch["operators"] if item["status"] == "RUNNING"), None)
    if current is None:
        raise ValueError("当前没有 RUNNING 算子，不能关联 run")
    run_state_path = run_dir_path / "run_state.json"
    if not run_state_path.is_file():
        raise ValueError(f"run 目录缺少 run_state.json: {run_state_path}")
    run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
    current["run_id"] = run_state["run_id"]
    current["run_dir"] = str(run_dir_path)
    current["run_state"] = run_state.get("state")
    current["updated_at"] = utc_now()
    _save_batch(batch_dir_path, batch)
    return {"ok": True, "batch_dir": str(batch_dir_path), "operator": current}


def batch_complete(batch_dir: str, terminal_state: str | None = None, message: str = "") -> dict[str, Any]:
    """Complete the current batch operator."""
    batch_dir_path = resolve_input_path(batch_dir)
    batch = _load_batch(batch_dir_path)
    current = next((item for item in batch["operators"] if item["status"] == "RUNNING"), None)
    if current is None:
        raise ValueError("当前没有 RUNNING 算子，不能完成")

    state = terminal_state
    if current.get("run_dir"):
        run_state_path = Path(current["run_dir"]) / "run_state.json"
        if run_state_path.is_file():
            run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
            state = run_state.get("state")

    if state not in TERMINAL_STATES:
        raise ValueError(f"算子尚未进入可记录终态: {state!r}")

    now = utc_now()
    current["status"] = "COMPLETED"
    current["terminal_state"] = state
    current["run_state"] = state
    current["message"] = message
    current["completed_at"] = now

    if state in FAILURE_STATES and not batch["continue_on_error"]:
        batch["state"] = "STOPPED"
    _save_batch(batch_dir_path, batch)
    return {"ok": True, "batch_dir": str(batch_dir_path), "operator": current, "batch_state": batch["state"], "counts": batch["counts"]}


def batch_show(batch_dir: str) -> dict[str, Any]:
    """Show current batch state."""
    batch_dir_path = resolve_input_path(batch_dir)
    if not (batch_dir_path / "batch_state.json").is_file():
        return _error("BATCH_NOT_FOUND", "批次目录缺少 batch_state.json", batch_dir=str(batch_dir_path))
    batch = _load_batch(batch_dir_path)
    _refresh_batch(batch)
    return {"ok": True, "batch_dir": str(batch_dir_path), "batch": batch}


def _error(code: str, message: str, **details: object) -> dict[str, Any]:
    return {"ok": False, "requires_user_action": True, "code": code, "message": message, **details}
