#!/usr/bin/env python3
"""Show and persist Claude Code session/subagent scheduling events."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def project_dir(payload: dict) -> Path:
    configured = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(configured or payload.get("cwd") or ".").resolve()


def registry_summary(root: Path) -> str:
    skills = sorted(p.parent.name for p in (root / ".claude" / "skills").glob("*/SKILL.md"))
    agents = sorted(p.stem for p in (root / ".claude" / "agents").glob("*.md"))
    return (
        f"[WORKFORCE] skills={len(skills)} [{', '.join(skills)}] | "
        f"agents={len(agents)} [{', '.join(agents)}] | "
        "commands=/show-workforce /iterate-operator /agents /hooks"
    )


def message_for(payload: dict, root: Path) -> str:
    event = payload.get("hook_event_name", "Unknown")
    if event == "SessionStart":
        return registry_summary(root)
    agent = payload.get("agent_type", "unknown")
    agent_id = payload.get("agent_id", "-")
    if event == "SubagentStart":
        return f"[SCHEDULER] START agent={agent} id={agent_id}"
    if event == "SubagentStop":
        return f"[SCHEDULER] STOP  agent={agent} id={agent_id}"
    return f"[SCHEDULER] {event}"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {"hook_event_name": "InvalidHookInput", "raw": ""}

    root = project_dir(payload)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": payload.get("hook_event_name", "Unknown"),
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id"),
        "agent_type": payload.get("agent_type"),
        "message": message_for(payload, root),
    }
    runtime = root / ".claude" / "runtime"
    try:
        runtime.mkdir(parents=True, exist_ok=True)
        with (runtime / "schedule.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as exc:
        # Observability must never block the workflow in a read-only/restricted
        # shell. The terminal message remains available even if audit logging
        # cannot be persisted.
        event["message"] += f" | audit-log-unavailable={exc.__class__.__name__}"

    print(json.dumps({"systemMessage": event["message"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
