#!/usr/bin/env python3
"""Record an explicit user decision without changing canonical files."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.validate_prompt_update_proposal import validate
except ModuleNotFoundError:
    from validate_prompt_update_proposal import validate


def main() -> int:
    parser = argparse.ArgumentParser(description="Record user decision for a prompt update proposal.")
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--decision", required=True, choices=("approve", "reject", "defer"))
    parser.add_argument("--confirmed-by-user", action="store_true")
    parser.add_argument("--user-note", default="")
    args = parser.parse_args()
    if not args.confirmed_by_user:
        parser.error("--confirmed-by-user is required; decisions may not be inferred or automated")
    proposal_path = Path(args.proposal).resolve()
    checked = validate(proposal_path)
    if not checked["ok"]:
        print(json.dumps(checked, ensure_ascii=False, indent=2))
        return 2
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    destination = proposal["classification"]["destination"]
    if args.decision == "approve" and destination in {"run_only", "no_update"}:
        parser.error(f"{destination} proposals cannot be approved for canonical promotion")
    if proposal.get("status") != "pending_user":
        parser.error("proposal.status must be pending_user before recording a user decision")
    decisions_path = Path(args.decisions).resolve()
    expected = Path(proposal["run_dir"]).resolve() / "inputs" / "prompt_update_decisions.json"
    if decisions_path != expected.resolve():
        parser.error(f"decisions must be the proposal run's decision file: {expected}")
    try:
        payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {"schema_version": "1.0", "decisions": []}
    if payload.get("schema_version") != "1.0" or not isinstance(payload.get("decisions"), list):
        parser.error("decisions file does not satisfy schema_version 1.0")
    if any(item.get("proposal_id") == proposal["proposal_id"] for item in payload["decisions"]):
        parser.error("a decision for this proposal_id is already recorded")
    decided_at = datetime.now(timezone.utc).isoformat()
    payload["decisions"].append({
        "proposal_id": proposal["proposal_id"], "decision": args.decision,
        "decision_source": "explicit_user_confirmation", "decided_at": decided_at,
        "destination": destination, "target_path": proposal.get("target", {}).get("path", ""),
        "summary": proposal.get("change", {}).get("summary", ""), "user_note": args.user_note,
        "canonical_applied": False,
    })
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    proposal["status"] = {"approve": "approved", "reject": "rejected", "defer": "deferred"}[args.decision]
    proposal["user_decision"] = {"decision": args.decision, "decided_at": decided_at, "decisions_path": str(decisions_path)}
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True, "proposal_id": proposal["proposal_id"], "decision": args.decision,
        "canonical_applied": False, "requires_application": args.decision == "approve",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

