#!/usr/bin/env python3
"""Validate a frozen ACLNN prompt assembly record and all recorded hashes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(record_path: Path) -> list[str]:
    errors: list[str] = []
    record = json.loads(record_path.read_text(encoding="utf-8"))
    mode = record.get("mode")
    family = record.get("family")
    if (mode, family) not in {
        ("aclnn_routed_knowledge", "aclnn"),
        ("torch_npu_routed_knowledge", "torch_npu"),
    }:
        errors.append("record is not a routed-knowledge assembly for a known family")
    checks = [
        (record.get("source_document", {}), "source_document"),
        (record.get("base_prompt", {}), "base_prompt"),
    ]
    knowledge = record.get("knowledge", {})
    checks.append(({"path": knowledge.get("manifest_path"), "sha256": knowledge.get("manifest_sha256")}, "manifest"))
    preanalysis = record.get("preanalysis_artifact", {})
    if preanalysis.get("path"):
        checks.append((preanalysis, "preanalysis"))
    assembly = record.get("assembly", {})
    checks.append(({"path": assembly.get("output_path"), "sha256": assembly.get("output_sha256")}, "output"))
    for component in assembly.get("knowledge_components", []):
        checks.append((component, f"module:{component.get('module_id', '?')}"))
    for item, label in checks:
        path_text, expected = item.get("path"), item.get("sha256")
        if not path_text or not expected:
            errors.append(f"{label}: missing path/hash")
            continue
        path = Path(path_text)
        if not path.is_file():
            errors.append(f"{label}: missing file: {path}")
        elif _sha256(path) != expected:
            errors.append(f"{label}: sha256 mismatch: {path}")
    ids = assembly.get("module_ids", [])
    components = assembly.get("knowledge_components", [])
    if ids != [item.get("module_id") for item in components]:
        errors.append("module order does not match knowledge_components")
    selected = record.get("applicability", {}).get("selected_modules", [])
    if ids != [item.get("module_id") for item in selected]:
        errors.append("module order does not match applicability decisions")
    output = Path(assembly.get("output_path", ""))
    if output.is_file():
        text = output.read_text(encoding="utf-8")
        if text.count("assembled-knowledge-begin") != 1 or text.count("assembled-knowledge-end") != 1:
            errors.append("assembled prompt knowledge boundary markers are invalid")
        for module_id in ids:
            if text.count(f"knowledge-module: {module_id} -->") != 1:
                errors.append(f"assembled prompt missing/duplicates module marker: {module_id}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ACLNN prompt assembly record.")
    parser.add_argument("--record", required=True)
    args = parser.parse_args()
    errors = validate(Path(args.record).resolve())
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
