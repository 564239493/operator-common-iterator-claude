#!/usr/bin/env python3
"""Prepare/finalize a value-constraints update iteration (Metadata/TTK).

``constraint_update_state.py`` versions ``constraints.json`` (shape/presence).
Value-level findings (first==0, non-decreasing, seqused<=cu, cmpResidual<ratio)
must version ``value_constraints.json`` instead.  This script mirrors the
prepare/finalize protocol for that file.

Usage:
    python scripts/value_constraints_update_state.py prepare \
        --source <prev>/value_constraints.json \
        --target <next>/value_constraints.json \
        --analysis <prev>/analysis.json
    python scripts/value_constraints_update_state.py finalize \
        --report <next>/value_constraints_update.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from validate_artifacts import (  # noqa: E402
    validate_analysis,
    validate_value_constraints,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, label: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _entries(path: Path) -> list:
    payload = _load(path, "value constraints")
    if isinstance(payload, dict):
        payload = payload.get("value_constraints") or []
    return payload if isinstance(payload, list) else []


def prepare(source: Path, target: Path, analysis_path: Path) -> dict:
    source = Path(source)
    target = Path(target)
    if not source.is_file():
        raise ValueError(f"source value_constraints not found: {source}")
    analysis = _load(analysis_path, "analysis")
    errors = validate_analysis(analysis)
    if errors:
        raise ValueError("analysis validation failed: " + "; ".join(errors))
    findings = analysis.get("constraint_findings") or []
    if not findings:
        raise ValueError("value update requires constraint_findings")
    source_errors = validate_value_constraints(
        {"value_constraints": _entries(source)}
    )
    if source_errors:
        raise ValueError("source value_constraints invalid: " + "; ".join(source_errors))
    target.parent.mkdir(parents=True, exist_ok=True)
    report_path = target.parent / "value_constraints_update.json"
    backup_path = target.with_name("value_constraints.json.pre_update")
    if target.exists() or report_path.exists() or backup_path.exists():
        raise ValueError(
            "target iteration already contains value-constraints update artifacts"
        )
    base_hash = _sha256(source)
    shutil.copy2(source, target)
    shutil.copy2(source, backup_path)
    report = {
        "schema_version": "1.0",
        "status": "pending",
        "source_value_constraints": str(source),
        "target_value_constraints": str(target),
        "pre_update_value_constraints": str(backup_path),
        "analysis_file": str(analysis_path),
        "base_sha256": base_hash,
        "result_sha256": base_hash,
        "finding_ids": [item.get("id") for item in findings if isinstance(item, dict)],
        "changes": [],
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "prepared": True,
        "target_value_constraints": str(target),
        "backup": str(backup_path),
        "report": str(report_path),
        "base_sha256": base_hash,
    }


def _validate_report(report: dict) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != "1.0":
        errors.append("schema_version must be '1.0'")
    if report.get("status") not in {"pending", "updated"}:
        errors.append("status must be pending or updated")
    for field in (
        "source_value_constraints", "target_value_constraints",
        "pre_update_value_constraints", "analysis_file",
    ):
        text = report.get(field)
        if not isinstance(text, str) or not text or not Path(text).is_file():
            errors.append(f"{field} must reference an existing file")
    for field in ("base_sha256", "result_sha256"):
        digest = report.get(field)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest.lower())
        ):
            errors.append(f"{field} must be a sha256 hex string")
    findings = report.get("finding_ids")
    if not isinstance(findings, list) or not findings:
        errors.append("finding_ids must be a non-empty array")
        findings = []
    changes = report.get("changes")
    if not isinstance(changes, list):
        errors.append("changes must be an array")
        changes = []
    covered: set[str] = set()
    seen: set[str] = set()
    for index, change in enumerate(changes):
        prefix = f"changes[{index}]"
        if not isinstance(change, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not isinstance(change.get("id"), str) or not change["id"].strip():
            errors.append(f"{prefix}.id must be non-empty")
        elif change["id"] in seen:
            errors.append(f"{prefix}.id duplicated")
        else:
            seen.add(change["id"])
        if not isinstance(change.get("op"), str) or not change["op"].strip():
            errors.append(f"{prefix}.op must be non-empty")
        for field in ("target", "basis", "expected_effect"):
            if not isinstance(change.get(field), str) or not change[field].strip():
                errors.append(f"{prefix}.{field} must be non-empty")
        refs = change.get("finding_ids")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{prefix}.finding_ids must be non-empty")
        else:
            covered.update(str(ref) for ref in refs)
        if "before" not in change or "after" not in change:
            errors.append(f"{prefix} requires before/after")
        elif change.get("before") == change.get("after"):
            errors.append(f"{prefix} must change the value")
    if report.get("status") == "updated":
        if not changes:
            errors.append("updated report requires changes")
        missing = sorted(set(findings) - covered)
        if missing:
            errors.append("value findings not covered: " + ", ".join(missing))
        if report.get("base_sha256") == report.get("result_sha256"):
            errors.append("updated value update must change sha256")
        target = Path(str(report.get("target_value_constraints", "")))
        if target.is_file() and _sha256(target) != report.get("result_sha256"):
            errors.append("result_sha256 does not match target file")
    return errors


def finalize(report_path: Path) -> dict:
    report = _load(report_path, "value_constraints_update")
    errors = _validate_report(report)
    if errors:
        raise ValueError("value update validation failed: " + "; ".join(errors))
    target = Path(str(report.get("target_value_constraints", "")))
    source = Path(str(report.get("source_value_constraints", "")))
    backup = Path(str(report.get("pre_update_value_constraints", "")))
    base = str(report.get("base_sha256", ""))
    if _sha256(source) != base or _sha256(backup) != base:
        raise ValueError("source or pre-update value constraints changed after prepare")
    target_errors = validate_value_constraints(
        {"value_constraints": _entries(target)}
    )
    if target_errors:
        raise ValueError("target value constraints invalid: " + "; ".join(target_errors))
    result = _sha256(target)
    if result == base:
        raise ValueError("value constraints update is noop")
    report["status"] = "updated"
    report["result_sha256"] = result
    final_errors = _validate_report(report)
    if final_errors:
        raise ValueError("value update validation failed: " + "; ".join(final_errors))
    Path(report_path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "finalized": True,
        "target_value_constraints": str(target),
        "result_sha256": result,
        "report": str(report_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Value constraints update state")
    sub = parser.add_subparsers(dest="command", required=True)
    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--source", required=True)
    p_prepare.add_argument("--target", required=True)
    p_prepare.add_argument("--analysis", required=True)
    p_finalize = sub.add_parser("finalize")
    p_finalize.add_argument("--report", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(
                Path(args.source), Path(args.target), Path(args.analysis)
            )
        else:
            result = finalize(Path(args.report))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
