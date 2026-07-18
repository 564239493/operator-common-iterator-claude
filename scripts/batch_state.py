#!/usr/bin/env python3
"""Deterministic lifecycle management for directory-level operator batches."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_config import ROOT, resolve_input_path

SUCCESS_STATES = {"SUCCESS"}
FAILURE_STATES = {
    "BLOCKED",
    "MAX_ITERATIONS",
    "STOP_GENERATOR_BUG",
    "STOP_EXECUTOR_BUG",
}
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


def resolve_batch_dir(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not (path / "batch_state.json").is_file():
        raise ValueError(f"批次目录缺少 batch_state.json: {path}")
    return path


def load_batch(batch_dir: Path) -> dict[str, Any]:
    payload = json.loads((batch_dir / "batch_state.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("operators"), list):
        raise ValueError("batch_state.json 结构不合法")
    return payload


def counts_for(batch: dict[str, Any]) -> dict[str, int]:
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


def refresh_batch(batch: dict[str, Any]) -> None:
    counts = counts_for(batch)
    batch["counts"] = counts
    batch["current_index"] = next(
        (
            item["index"]
            for item in batch["operators"]
            if item["status"] == "RUNNING"
        ),
        None,
    )
    if counts["completed"] == counts["total"]:
        batch["state"] = "COMPLETED"
        batch["completed_at"] = batch.get("completed_at") or utc_now()
    elif batch.get("state") != "STOPPED":
        batch["state"] = "RUNNING"
        batch["completed_at"] = None
    batch["updated_at"] = utc_now()


def summary_for(batch: dict[str, Any]) -> dict[str, Any]:
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


def save_batch(batch_dir: Path, batch: dict[str, Any]) -> None:
    refresh_batch(batch)
    atomic_write_json(batch_dir / "batch_state.json", batch)
    atomic_write_json(batch_dir / "batch_summary.json", summary_for(batch))


def running_operator(batch: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (item for item in batch["operators"] if item["status"] == "RUNNING"),
        None,
    )


def run_options_for(batch: dict[str, Any]) -> dict[str, Any]:
    """Options the coordinator must forward to the per-document init_run."""
    return {
        "prompt": batch.get("prompt", "") if batch.get("prompt_explicit") else "",
        "prompt_explicit": bool(batch.get("prompt_explicit")),
        "operator_family": batch.get("operator_family", "auto"),
        "test_framework": batch.get("test_framework", "auto"),
        "supplement_constraints": batch.get("supplement_constraints", ""),
    }


def command_claim(batch_dir: Path, batch: dict[str, Any]) -> dict[str, Any]:
    current = running_operator(batch)
    if current is not None:
        return {
            "ok": True,
            "action": "resume",
            "batch_dir": str(batch_dir),
            "operator": current,
            **run_options_for(batch),
        }
    if batch["state"] == "STOPPED":
        return {
            "ok": False,
            "code": "BATCH_STOPPED",
            "message": "批次已按 fail-fast 策略停止。",
            "batch_dir": str(batch_dir),
        }
    pending = next(
        (item for item in batch["operators"] if item["status"] == "PENDING"),
        None,
    )
    if pending is None:
        save_batch(batch_dir, batch)
        return {
            "ok": True,
            "action": "complete",
            "batch_dir": str(batch_dir),
            "summary": str(batch_dir / "batch_summary.json"),
            "counts": batch["counts"],
        }
    now = utc_now()
    pending["status"] = "RUNNING"
    pending["started_at"] = now
    batch["history"].append(
        {"event": "OPERATOR_CLAIMED", "index": pending["index"], "at": now}
    )
    save_batch(batch_dir, batch)
    return {
        "ok": True,
        "action": "start",
        "batch_dir": str(batch_dir),
        "operator": pending,
        **run_options_for(batch),
    }


def command_attach_run(
    batch_dir: Path,
    batch: dict[str, Any],
    run_dir_value: str,
) -> dict[str, Any]:
    current = running_operator(batch)
    if current is None:
        raise ValueError("当前没有 RUNNING 算子，不能关联 run")
    run_dir = resolve_input_path(run_dir_value)
    run_state_path = run_dir / "run_state.json"
    if not run_state_path.is_file():
        raise ValueError(f"run 目录缺少 run_state.json: {run_dir}")
    run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
    source = str(Path(run_state["operator_doc_source"]).resolve())
    if source.casefold() != current["operator_doc_source"].casefold():
        raise ValueError(
            "run 的 operator_doc_source 与当前批次算子不一致: "
            f"{source} != {current['operator_doc_source']}"
        )
    current["run_id"] = run_state["run_id"]
    current["run_dir"] = str(run_dir)
    current["run_state"] = run_state.get("state")
    current["updated_at"] = utc_now()
    batch["history"].append(
        {
            "event": "RUN_ATTACHED",
            "index": current["index"],
            "run_id": current["run_id"],
            "at": current["updated_at"],
        }
    )
    save_batch(batch_dir, batch)
    return {
        "ok": True,
        "batch_dir": str(batch_dir),
        "operator": current,
    }


def command_complete(
    batch_dir: Path,
    batch: dict[str, Any],
    terminal_state: str | None,
    message: str,
) -> dict[str, Any]:
    current = running_operator(batch)
    if current is None:
        raise ValueError("当前没有 RUNNING 算子，不能完成")

    state = terminal_state
    if current.get("run_dir"):
        run_state_path = Path(current["run_dir"]) / "run_state.json"
        if not run_state_path.is_file():
            raise ValueError(f"关联的 run 缺少 run_state.json: {run_state_path}")
        run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
        actual_state = run_state.get("state")
        if state is not None and state != actual_state:
            raise ValueError(
                f"指定终态 {state} 与 run_state.json 中的 {actual_state} 不一致"
            )
        state = actual_state
    if state not in TERMINAL_STATES:
        raise ValueError(
            f"算子尚未进入可记录终态: {state!r}; "
            f"允许值: {', '.join(sorted(TERMINAL_STATES))}"
        )

    now = utc_now()
    current["status"] = "COMPLETED"
    current["terminal_state"] = state
    current["run_state"] = state
    current["message"] = message
    current["completed_at"] = now
    batch["history"].append(
        {
            "event": "OPERATOR_COMPLETED",
            "index": current["index"],
            "terminal_state": state,
            "at": now,
        }
    )
    if state in FAILURE_STATES and not batch["continue_on_error"]:
        batch["state"] = "STOPPED"
        batch["history"].append(
            {
                "event": "BATCH_STOPPED",
                "reason": "fail_fast",
                "index": current["index"],
                "at": now,
            }
        )
    save_batch(batch_dir, batch)
    return {
        "ok": True,
        "batch_dir": str(batch_dir),
        "operator": current,
        "batch_state": batch["state"],
        "counts": batch["counts"],
        "summary": str(batch_dir / "batch_summary.json"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="更新并查询目录级算子批次状态。")
    parser.add_argument("--batch-dir", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("claim", help="认领下一个算子；有未完成项时返回恢复信息")
    attach = subparsers.add_parser("attach-run", help="把当前算子关联到单算子 run")
    attach.add_argument("--run-dir", required=True)
    complete = subparsers.add_parser("complete", help="记录当前算子的终态")
    complete.add_argument("--terminal-state", choices=sorted(TERMINAL_STATES))
    complete.add_argument("--message", default="")
    subparsers.add_parser("show", help="展示当前批次状态")
    args = parser.parse_args()

    try:
        batch_dir = resolve_batch_dir(args.batch_dir)
        batch = load_batch(batch_dir)
        if args.command == "claim":
            result = command_claim(batch_dir, batch)
        elif args.command == "attach-run":
            result = command_attach_run(batch_dir, batch, args.run_dir)
        elif args.command == "complete":
            result = command_complete(
                batch_dir,
                batch,
                args.terminal_state,
                args.message,
            )
        else:
            refresh_batch(batch)
            result = {
                "ok": True,
                "batch_dir": str(batch_dir),
                "batch": batch,
                "summary": str(batch_dir / "batch_summary.json"),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps(
            {"ok": False, "code": "BATCH_STATE_ERROR", "message": str(exc)},
            ensure_ascii=False,
            indent=2,
        ))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
