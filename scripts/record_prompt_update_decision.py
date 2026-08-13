#!/usr/bin/env python3
"""Record an explicit user decision without changing canonical files."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.validate_prompt_update_proposal import DESTINATIONS, ROOT, inside, validate
except ModuleNotFoundError:
    from validate_prompt_update_proposal import DESTINATIONS, ROOT, inside, validate


def main() -> int:
    parser = argparse.ArgumentParser(description="Record user decision for a prompt update proposal.")
    parser.add_argument("--proposal", required=True)
    parser.add_argument(
        "--proposal-id", default="",
        help="Proposal id when --proposal uses the historical collection schema",
    )
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--decision", required=True, choices=("approve", "reject", "defer"))
    parser.add_argument("--confirmed-by-user", action="store_true")
    parser.add_argument("--user-note", default="")
    args = parser.parse_args()
    if not args.confirmed_by_user:
        parser.error("--confirmed-by-user is required; decisions may not be inferred or automated")
    proposal_path = Path(args.proposal).resolve()
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    collection_mode = isinstance(proposal.get("proposals"), list)
    if collection_mode:
        if not args.proposal_id:
            parser.error("--proposal-id is required for a collection proposal file")
        selected = next(
            (item for item in proposal["proposals"] if item.get("id") == args.proposal_id),
            None,
        )
        if selected is None:
            parser.error(f"proposal id not found in collection: {args.proposal_id}")
        proposal_id = selected["id"]
        destination = selected.get("destination", "")
        target_path = selected.get("canonical_target", "").split(" §", 1)[0]
        expected_root = DESTINATIONS.get(destination)
        resolved_target = (ROOT / target_path).resolve() if target_path else None
        if destination not in DESTINATIONS or expected_root is None:
            parser.error(f"collection proposal has invalid canonical destination: {destination}")
        if resolved_target is None or not inside(resolved_target, expected_root):
            parser.error("collection proposal target is outside its canonical destination")
        summary = selected.get("change_summary", "")
        run_dir = proposal_path.parent.parent
        if selected.get("status") not in {"pending_user", "deferred"}:
            parser.error("collection proposal status must be pending_user or deferred")
    else:
        checked = validate(proposal_path)
        if not checked["ok"]:
            print(json.dumps(checked, ensure_ascii=False, indent=2))
            return 2
        selected = proposal
        proposal_id = proposal["proposal_id"]
        destination = proposal["classification"]["destination"]
        target_path = proposal.get("target", {}).get("path", "")
        summary = proposal.get("change", {}).get("summary", "")
        run_dir = Path(proposal["run_dir"]).resolve()
    if args.decision == "approve" and destination in {"run_only", "no_update"}:
        parser.error(f"{destination} proposals cannot be approved for canonical promotion")
    if not collection_mode and proposal.get("status") != "pending_user":
        parser.error("proposal.status must be pending_user before recording a user decision")
    decisions_path = Path(args.decisions).resolve()
    expected = run_dir / "inputs" / "prompt_update_decisions.json"
    if decisions_path != expected.resolve():
        parser.error(f"decisions must be the proposal run's decision file: {expected}")
    try:
        payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {"schema_version": "1.0", "decisions": []}
    if payload.get("schema_version") != "1.0" or not isinstance(payload.get("decisions"), list):
        parser.error("decisions file does not satisfy schema_version 1.0")
    if any(item.get("proposal_id") == proposal_id for item in payload["decisions"]):
        parser.error("a decision for this proposal_id is already recorded")
    decided_at = datetime.now(timezone.utc).isoformat()
    payload["decisions"].append({
        "proposal_id": proposal_id, "decision": args.decision,
        "decision_source": "explicit_user_confirmation", "decided_at": decided_at,
        "destination": destination, "target_path": target_path,
        "summary": summary, "user_note": args.user_note,
        "canonical_applied": False,
    })
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    selected["status"] = {"approve": "approved", "reject": "rejected", "defer": "deferred"}[args.decision]
    selected["user_decision"] = {"decision": args.decision, "decided_at": decided_at, "decisions_path": str(decisions_path)}
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True, "proposal_id": proposal_id, "decision": args.decision,
        "canonical_applied": False, "requires_application": args.decision == "approve",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

