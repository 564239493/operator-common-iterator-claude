#!/usr/bin/env python3
"""Validate a run-local proposal for prompt/knowledge learning."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESTINATIONS = {
    "base_prompt": ROOT / "prompts" / "operator_constraints",
    "knowledge_common": ROOT / "knowledge" / "aclnn" / "common",
    "knowledge_feature": ROOT / "knowledge" / "aclnn" / "features",
    "knowledge_operator": ROOT / "knowledge" / "aclnn" / "operators",
    "torch_npu": ROOT / "knowledge" / "torch_npu",
    "no_update": None,
    "run_only": None,
}
PROMOTION_GATES = {"on_success", "must_update", "never"}
TRIAL_STATUSES = {"pending", "passed", "failed", "inconclusive"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate(
    proposal_path: Path,
    verify_hashes: bool = True,
    allow_applied_create_target: bool = False,
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "proposal": str(proposal_path), "errors": [f"invalid JSON: {exc}"]}
    for field in ("proposal_id", "run_id", "iteration", "status", "trigger", "classification", "target", "change", "evidence", "trial"):
        if field not in proposal:
            errors.append(f"missing field: {field}")
    if proposal.get("schema_version") != "1.0":
        errors.append(f"unsupported schema_version: {proposal.get('schema_version')}")
    if not proposal.get("proposal_id") or not proposal.get("run_id"):
        errors.append("proposal_id and run_id must not be empty")
    if proposal.get("status") not in {"draft", "trial_pending", "pending_user", "approved", "rejected", "deferred", "applied"}:
        errors.append(f"invalid status: {proposal.get('status')}")
    if not isinstance(proposal.get("iteration"), int) or proposal.get("iteration", 0) < 1:
        errors.append("iteration must be a positive integer")
    trigger = proposal.get("trigger", {})
    if trigger.get("root_cause") != "constraint_extraction":
        errors.append("only constraint_extraction may create a prompt update proposal")
    if not trigger.get("specific_issues"):
        errors.append("trigger.specific_issues must not be empty")
    classification = proposal.get("classification", {})
    destination = classification.get("destination")
    if destination not in DESTINATIONS:
        errors.append(f"invalid classification.destination: {destination}")
    if classification.get("promotion_gate") not in PROMOTION_GATES:
        errors.append(f"invalid promotion_gate: {classification.get('promotion_gate')}")
    if classification.get("confidence") not in {"low", "medium", "high"}:
        errors.append(f"invalid confidence: {classification.get('confidence')}")
    if not classification.get("rationale"):
        errors.append("classification.rationale must not be empty")
    run_dir_raw = proposal.get("run_dir", "")
    run_dir = Path(run_dir_raw).resolve() if run_dir_raw else None
    if run_dir is None or not run_dir.is_dir():
        errors.append(f"invalid run_dir: {run_dir_raw}")
    elif not inside(proposal_path, run_dir):
        errors.append("proposal must be stored inside its run_dir")
    target = proposal.get("target", {})
    target_raw, action = target.get("path", ""), target.get("action", "")
    if destination in {"no_update", "run_only"}:
        if target_raw or action != "none":
            errors.append(f"{destination} proposal must use target.path='' and target.action='none'")
        if classification.get("promotion_gate") != "never":
            errors.append(f"{destination} proposal must use promotion_gate='never'")
        if proposal.get("status") in {"pending_user", "approved", "applied"}:
            errors.append(f"{destination} proposal cannot enter user approval or canonical application states")
    elif destination in DESTINATIONS:
        expected_root = DESTINATIONS[destination]
        target_path = (ROOT / target_raw).resolve() if target_raw else None
        if target_path is None or expected_root is None or not inside(target_path, expected_root):
            errors.append(f"target path is outside destination {destination}: {target_raw}")
        elif target_path.suffix.lower() != ".md":
            errors.append("canonical target must be a Markdown file")
        elif destination == "base_prompt" and target_path != (expected_root / "base.md").resolve():
            errors.append("base_prompt destination may only target prompts/operator_constraints/base.md")
        if action not in {"update", "create"}:
            errors.append("canonical target action must be update or create")
        elif target_path is not None:
            if not target.get("section"):
                errors.append("canonical target.section must identify the intended edit location")
            exists = target_path.is_file()
            if action == "update" and not exists:
                errors.append(f"update target does not exist: {target_path}")
            if action == "create" and exists and not allow_applied_create_target:
                errors.append(f"create target already exists: {target_path}")
            if verify_hashes and action == "update" and exists and target.get("sha256_before", "") != sha256(target_path):
                errors.append("target.sha256_before does not match current canonical file")
    for label in ("candidate_prompt", "change_artifact"):
        item = proposal.get(label, {})
        path = Path(item.get("path", "")).resolve() if item.get("path") else None
        if path is None or not path.is_file():
            errors.append(f"{label} missing: {item.get('path', '')}")
        else:
            if run_dir is not None and not inside(path, run_dir):
                errors.append(f"{label} must stay inside run_dir")
            if verify_hashes and item.get("sha256", "") != sha256(path):
                errors.append(f"{label}.sha256 mismatch")
    change = proposal.get("change", {})
    if not change.get("summary") or not change.get("content"):
        errors.append("change requires non-empty summary and exact proposed content")
    evidence = proposal.get("evidence", [])
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence must be a non-empty list")
    else:
        for index, item in enumerate(evidence):
            if not item.get("kind") or not item.get("source_path") or not item.get("summary"):
                errors.append(f"evidence[{index}] requires kind, source_path and summary")
                continue
            raw = Path(item["source_path"])
            candidates = [raw.resolve()] if raw.is_absolute() else [(ROOT / raw).resolve(), (run_dir / raw).resolve() if run_dir else ROOT / "__invalid__"]
            if not any(candidate.is_file() for candidate in candidates):
                errors.append(f"evidence[{index}].source_path does not identify an existing file")
    trial = proposal.get("trial", {})
    if trial.get("status") not in TRIAL_STATUSES:
        errors.append(f"invalid trial.status: {trial.get('status')}")
    if classification.get("promotion_gate") == "on_success" and proposal.get("status") == "pending_user" and trial.get("status") != "passed":
        errors.append("on_success proposal may become pending_user only after trial.status='passed'")
    if destination == "base_prompt" and not (classification.get("project_contract_evidence") or classification.get("cross_operator_evidence")):
        errors.append("base_prompt proposal requires project_contract_evidence or cross_operator_evidence")
    if destination in {"knowledge_common", "knowledge_feature"} and not (classification.get("authoritative_common_evidence") or classification.get("cross_operator_evidence")):
        warnings.append("reusable knowledge lacks common/cross-operator evidence; prefer knowledge_operator or no_update")
    return {
        "ok": not errors, "proposal": str(proposal_path.resolve()),
        "proposal_id": proposal.get("proposal_id", ""), "destination": destination,
        "target": target_raw, "promotion_gate": classification.get("promotion_gate", ""),
        "trial_status": trial.get("status", ""), "warnings": warnings, "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate prompt/knowledge update proposal.")
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--skip-hashes", action="store_true")
    args = parser.parse_args()
    result = validate(Path(args.proposal).resolve(), not args.skip_hashes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
