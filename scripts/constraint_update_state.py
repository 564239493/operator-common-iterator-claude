#!/usr/bin/env python3
"""Prepare and finalize an execution-feedback constraint update iteration."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from validate_artifacts import (
    validate_analysis,
    validate_constraint_update,
    validate_constraints,
    validate_execution,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path, label: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def prepare(
    source: Path,
    target: Path,
    analysis_path: Path,
    execution_path: Path,
) -> dict[str, object]:
    if not source.is_file():
        raise ValueError(f"source constraints not found: {source}")
    analysis = _load_object(analysis_path, "analysis")
    errors = validate_analysis(analysis)
    if errors:
        raise ValueError("analysis validation failed: " + "; ".join(errors))
    if analysis.get("overall_action") != "UPDATE_CONSTRAINTS":
        raise ValueError("analysis.overall_action must be UPDATE_CONSTRAINTS")
    findings = analysis.get("constraint_findings", [])
    if not findings:
        raise ValueError("UPDATE_CONSTRAINTS requires constraint_findings")
    execution = _load_object(execution_path, "execution result")
    execution_errors = validate_execution(execution)
    if execution_errors:
        raise ValueError("execution result validation failed: " + "; ".join(execution_errors))
    consumed = execution.get("input_artifacts", {}).get("constraints")
    if not isinstance(consumed, dict):
        raise ValueError("execution result does not fingerprint consumed constraints")
    consumed_path = Path(str(consumed.get("path", ""))).resolve()
    if consumed_path != source.resolve():
        raise ValueError("source is not the constraints file consumed by execution")
    source_constraints = _load_object(source, "source constraints")
    constraint_errors = validate_constraints(source_constraints)
    if constraint_errors:
        raise ValueError("source constraints validation failed: " + "; ".join(constraint_errors))
    base_hash = _sha256(source)
    if consumed.get("sha256") != base_hash:
        raise ValueError("source hash does not match execution input fingerprint")

    target.parent.mkdir(parents=True, exist_ok=True)
    report_path = target.parent / "constraint_update.json"
    backup_path = target.with_name(target.stem + ".json.pre_update")
    if target.exists() or report_path.exists() or backup_path.exists():
        raise ValueError(
            "target iteration already contains constraint update artifacts; "
            "resume the existing update instead of overwriting it"
        )
    shutil.copy2(source, target)
    shutil.copy2(source, backup_path)
    report = {
        "schema_version": "1.0",
        "status": "pending",
        "source_constraints": str(source),
        "target_constraints": str(target),
        "pre_update_constraints": str(backup_path),
        "analysis_file": str(analysis_path),
        "execution_result": str(execution_path),
        "base_sha256": base_hash,
        "result_sha256": base_hash,
        "finding_ids": [item["id"] for item in findings],
        "changes": [],
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "prepared": True,
        "target_constraints": str(target),
        "backup": str(backup_path),
        "report": str(report_path),
        "base_sha256": base_hash,
    }


def finalize(report_path: Path) -> dict[str, object]:
    report = _load_object(report_path, "constraint_update")
    pending_errors = validate_constraint_update(report)
    if pending_errors:
        raise ValueError("constraint update validation failed: " + "; ".join(pending_errors))
    target = Path(str(report.get("target_constraints", "")))
    source = Path(str(report.get("source_constraints", "")))
    backup = Path(str(report.get("pre_update_constraints", "")))
    analysis_path = Path(str(report.get("analysis_file", "")))
    execution_path = Path(str(report.get("execution_result", "")))
    if not target.is_file():
        raise ValueError(f"target constraints not found: {target}")
    analysis = _load_object(analysis_path, "analysis")
    analysis_errors = validate_analysis(analysis)
    if analysis_errors:
        raise ValueError("analysis validation failed: " + "; ".join(analysis_errors))
    if analysis.get("overall_action") != "UPDATE_CONSTRAINTS":
        raise ValueError("analysis.overall_action must be UPDATE_CONSTRAINTS")
    execution = _load_object(execution_path, "execution result")
    execution_errors = validate_execution(execution)
    if execution_errors:
        raise ValueError("execution result validation failed: " + "; ".join(execution_errors))
    consumed = execution.get("input_artifacts", {}).get("constraints", {})
    expected = {item["id"] for item in analysis.get("constraint_findings", [])}
    if set(report.get("finding_ids", [])) != expected:
        raise ValueError("constraint_update.finding_ids no longer match analysis")
    base_hash = str(report.get("base_sha256", ""))
    if _sha256(source) != base_hash or _sha256(backup) != base_hash:
        raise ValueError("source or pre-update constraints changed after prepare")
    if (
        Path(str(consumed.get("path", ""))).resolve() != source.resolve()
        or consumed.get("sha256") != base_hash
    ):
        raise ValueError("execution input fingerprint no longer matches update baseline")
    changes = report.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("constraint_update.changes must be non-empty before finalize")
    covered: set[str] = set()
    for change in changes:
        if isinstance(change, dict):
            covered.update(change.get("finding_ids", []))
    missing = sorted(expected - covered)
    unknown = sorted(covered - expected)
    if missing:
        raise ValueError("constraint findings not covered: " + ", ".join(missing))
    if unknown:
        raise ValueError("unknown finding ids: " + ", ".join(unknown))
    result_hash = _sha256(target)
    if result_hash == report.get("base_sha256"):
        raise ValueError("constraint update is noop: constraints hash did not change")
    target_constraints = _load_object(target, "target constraints")
    constraint_errors = validate_constraints(target_constraints)
    if constraint_errors:
        raise ValueError("target constraints validation failed: " + "; ".join(constraint_errors))
    report["status"] = "updated"
    report["result_sha256"] = result_hash
    final_errors = validate_constraint_update(report)
    if final_errors:
        raise ValueError("constraint update validation failed: " + "; ".join(final_errors))
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "finalized": True,
        "result_sha256": result_hash,
        "change_count": len(changes),
        "finding_count": len(expected),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="管理执行反馈约束更新的复制与完成门禁。")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--source", required=True)
    prepare_parser.add_argument("--target", required=True)
    prepare_parser.add_argument("--analysis", required=True)
    prepare_parser.add_argument("--execution-result", required=True)
    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--report", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            payload = prepare(
                Path(args.source).resolve(),
                Path(args.target).resolve(),
                Path(args.analysis).resolve(),
                Path(args.execution_result).resolve(),
            )
        else:
            payload = finalize(Path(args.report).resolve())
        print(json.dumps({"ok": True, **payload}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "ok": False,
            "code": "CONSTRAINT_UPDATE_STATE_FAILED",
            "error": str(exc),
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
