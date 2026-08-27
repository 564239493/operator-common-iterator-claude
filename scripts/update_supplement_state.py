#!/usr/bin/env python3
"""Persist run-level supplement revision/hash independently of conversation memory."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def combined_hash(paths: list[Path]) -> str:
    contents: list[tuple[str, bytes]] = []
    for path in paths:
        if not path.is_file():
            continue
        content = path.read_bytes()
        if not content.strip():
            continue
        contents.append((path.name, content))
    if not contents:
        return ""
    if len(contents) == 1:
        return hashlib.sha256(contents[0][1]).hexdigest()
    digest = hashlib.sha256()
    for name, content in contents:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def update_state(
    run_state_path: Path,
    supplement_paths: list[Path],
    iteration: int,
    consume: bool = False,
) -> dict[str, object]:
    state = json.loads(run_state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("run_state must be a JSON object")
    current_hash = combined_hash(supplement_paths)
    previous_hash = str(state.get("supplement_hash", ""))
    changed = current_hash != previous_hash
    if changed:
        state["supplement_revision"] = int(state.get("supplement_revision", 0)) + 1
        state["supplement_hash"] = current_hash
        state["supplement_updated_iteration"] = iteration
    else:
        state.setdefault("supplement_revision", 0)
        state.setdefault("supplement_hash", current_hash)
        state.setdefault("supplement_updated_iteration", 0)
    if consume:
        state["last_consumed_supplement_hash"] = current_hash
    else:
        state.setdefault("last_consumed_supplement_hash", "")
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    run_state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "changed": changed,
        "consumed": consume,
        "supplement_revision": state["supplement_revision"],
        "supplement_hash": current_hash,
        "last_consumed_supplement_hash": state["last_consumed_supplement_hash"],
        "has_unconsumed_supplement": (
            bool(current_hash)
            and current_hash != state["last_consumed_supplement_hash"]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="刷新或消费 run_state 中的补充事实 revision/hash。"
    )
    parser.add_argument("run_state")
    parser.add_argument("--supplementary", default="")
    parser.add_argument("--human", default="")
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--consume", action="store_true")
    args = parser.parse_args()
    paths = [Path(value).resolve() for value in (args.supplementary, args.human) if value]
    try:
        payload = update_state(
            Path(args.run_state).resolve(), paths, args.iteration, consume=args.consume
        )
        print(json.dumps({"ok": True, **payload}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "ok": False,
            "code": "SUPPLEMENT_STATE_UPDATE_FAILED",
            "error": str(exc),
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
