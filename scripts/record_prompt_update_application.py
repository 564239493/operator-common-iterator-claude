#!/usr/bin/env python3
"""Record canonical application after an approved proposal was manually applied."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.validate_prompt_update_proposal import DESTINATIONS, ROOT, inside, validate
except ModuleNotFoundError:
    from validate_prompt_update_proposal import DESTINATIONS, ROOT, inside, validate


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Record approved canonical prompt/knowledge update.")
    parser.add_argument("--proposal", required=True)
    parser.add_argument(
        "--proposal-id", default="",
        help="Proposal id when --proposal uses the historical collection schema",
    )
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--validation", action="append", default=[])
    args = parser.parse_args()
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
        target_raw = selected.get("canonical_target", "").split(" §", 1)[0]
        target_path = (ROOT / target_raw).resolve()
        destination = selected.get("destination", "")
        expected_root = DESTINATIONS.get(destination)
        if destination not in DESTINATIONS or expected_root is None:
            parser.error(f"collection proposal has invalid canonical destination: {destination}")
        if not inside(target_path, expected_root):
            parser.error("collection proposal target is outside its canonical destination")
        run_dir = proposal_path.parent.parent
    else:
        checked = validate(proposal_path, verify_hashes=False, allow_applied_create_target=True)
        if not checked["ok"]:
            print(json.dumps(checked, ensure_ascii=False, indent=2))
            return 2
        selected = proposal
        proposal_id = proposal["proposal_id"]
        target = proposal["target"]
        target_path = (ROOT / target["path"]).resolve()
        run_dir = Path(proposal["run_dir"]).resolve()
    if selected.get("status") != "approved" or selected.get("user_decision", {}).get("decision") != "approve":
        parser.error("proposal must have an explicit recorded approve decision")
    if not target_path.is_file():
        parser.error(f"canonical target does not exist after application: {target_path}")
    after = sha256(target_path)
    if not collection_mode and target.get("action") == "update" and after == target.get("sha256_before"):
        parser.error("canonical target hash did not change")
    if not args.validation:
        parser.error("at least one --validation result is required")
    decisions_path = Path(args.decisions).resolve()
    expected = run_dir / "inputs" / "prompt_update_decisions.json"
    if decisions_path != expected.resolve():
        parser.error(f"decisions must be the proposal run's decision file: {expected}")
    payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    entry = next((item for item in payload.get("decisions", []) if item.get("proposal_id") == proposal_id), None)
    if entry is None or entry.get("decision") != "approve":
        parser.error("matching approved decision not found")
    if entry.get("canonical_applied"):
        parser.error("canonical application already recorded")
    applied_at = datetime.now(timezone.utc).isoformat()
    entry.update({"canonical_applied": True, "applied_at": applied_at, "target_sha256_after": after, "validations": args.validation})
    decisions_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    selected["status"] = "applied"
    selected["application"] = {"applied_at": applied_at, "target_sha256_after": after, "validations": args.validation}
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "proposal_id": proposal_id, "target": str(target_path), "status": "applied"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
