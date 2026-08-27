#!/usr/bin/env python3
"""Validate and persist failure-analysis additions for later SUPPLEMENT rounds.

``supplement_additions.md`` is iteration-local evidence.  The supplementer only
consumes the run-level ``inputs/supplementary-doc.md``, so this script closes the
handoff deterministically and records a content hash to make retries idempotent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from validate_artifacts import validate_analysis


def _result(ok: bool, **payload: object) -> None:
    print(json.dumps({"ok": ok, **payload}, ensure_ascii=False))


def merge_additions(
    analysis_path: Path,
    additions_path: Path,
    supplementary_path: Path,
) -> dict[str, object]:
    if not analysis_path.is_file():
        raise ValueError(f"analysis.json 不存在: {analysis_path}")
    if not additions_path.is_file():
        raise ValueError(f"supplement_additions.md 不存在: {additions_path}")

    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    errors = validate_analysis(analysis)
    if errors:
        raise ValueError("analysis.json 校验失败: " + "; ".join(errors))
    if analysis.get("root_cause") != "constraint_extraction":
        raise ValueError("只有 constraint_extraction 可合入诊断补充")

    decision = analysis.get("supplement_decision", {})
    if decision.get("has_explicit_additions") is not True:
        raise ValueError("supplement_decision.has_explicit_additions 必须为 true")

    additions = additions_path.read_text(encoding="utf-8").strip()
    if not additions:
        raise ValueError("supplement_additions.md 为空")
    findings = analysis.get("constraint_findings", [])
    missing_ids = [
        str(item.get("id", ""))
        for item in findings
        if str(item.get("id", "")).strip() not in additions
    ]
    if missing_ids:
        raise ValueError(
            "supplement_additions.md 未引用 constraint_findings: "
            + ", ".join(missing_ids)
        )

    digest = hashlib.sha256(additions.encode("utf-8")).hexdigest()
    marker = f"<!-- merged-supplement-additions:{digest} -->"
    existing = (
        supplementary_path.read_text(encoding="utf-8")
        if supplementary_path.is_file()
        else ""
    )
    if marker in existing:
        return {
            "merged": False,
            "reason": "already_merged",
            "addition_sha256": digest,
            "supplementary": str(supplementary_path),
        }

    supplementary_path.parent.mkdir(parents=True, exist_ok=True)
    section = (
        f"## 诊断确认补充（{additions_path.parent.name}）\n\n"
        f"{marker}\n\n{additions}\n"
    )
    prefix = existing.rstrip()
    merged = f"{prefix}\n\n{section}" if prefix else f"{section}"
    supplementary_path.write_text(merged, encoding="utf-8")
    return {
        "merged": True,
        "addition_sha256": digest,
        "finding_count": len(findings),
        "supplementary": str(supplementary_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="校验本轮诊断补充并幂等追加到 inputs/supplementary-doc.md。"
    )
    parser.add_argument("analysis", help="当前轮 analysis.json")
    parser.add_argument("additions", help="当前轮 supplement_additions.md")
    parser.add_argument("supplementary", help="run 级 inputs/supplementary-doc.md")
    args = parser.parse_args()

    try:
        payload = merge_additions(
            Path(args.analysis).resolve(),
            Path(args.additions).resolve(),
            Path(args.supplementary).resolve(),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _result(False, code="SUPPLEMENT_ADDITIONS_MERGE_FAILED", error=str(exc))
        return 2
    _result(True, **payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
