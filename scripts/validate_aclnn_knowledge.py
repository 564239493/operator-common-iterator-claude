#!/usr/bin/env python3
"""Validate the first-version ACLNN knowledge manifest and module boundaries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from scripts.route_aclnn_knowledge import DEFAULT_KNOWLEDGE, _load_modules
except ModuleNotFoundError:
    from route_aclnn_knowledge import DEFAULT_KNOWLEDGE, _load_modules


EXPECTED_DEFAULTS = {
    "official_basics", "dimensions", "allowed_range", "implicit_parameters",
    "platform_dtype", "expression_language",
}

VALID_TRIGGER_KINDS = {
    "operator_name_eq", "operator_name_regex", "name_contains",
    "doc_contains", "format_any",
}


def _trigger_key(trigger: dict) -> tuple:
    value = trigger.get("value")
    if isinstance(value, list):
        value = tuple(value)
    return (trigger.get("kind"), value)


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        manifest, modules = _load_modules(root)
    except Exception as exc:
        return [str(exc)]
    if manifest.get("family") != "aclnn":
        errors.append("manifest.family must be aclnn")
    defaults = {module_id for module_id, item in modules.items() if item.get("default_load")}
    if defaults != EXPECTED_DEFAULTS:
        errors.append(f"default module set mismatch: {sorted(defaults)}")
    for module_id, item in modules.items():
        path = item["path_obj"].resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            errors.append(f"module escapes ACLNN root: {module_id}: {path}")
        if "knowledge/torch_npu" in item["raw"].replace("\\", "/"):
            errors.append(f"cross-family reference in ACLNN module: {module_id}")
        for dependency in item.get("depends_on", []):
            if dependency not in modules:
                errors.append(f"unknown dependency: {module_id} -> {dependency}")
        if item["scope"] in {"operator", "source_analysis"}:
            exact = [trigger for trigger in item.get("triggers", []) if trigger.get("kind") == "operator_name_eq"]
            if len(exact) != 1:
                errors.append(
                    f"{item['scope']} module must have exactly one "
                    f"operator_name_eq trigger: {module_id}"
                )
        if item["scope"] == "source_analysis" and item.get("default_load"):
            errors.append(f"source_analysis module must not default_load: {module_id}")
        positive_keys = {_trigger_key(t) for t in item.get("triggers", [])}
        for trigger in item.get("reject_on", []):
            kind = trigger.get("kind")
            if kind not in VALID_TRIGGER_KINDS:
                errors.append(f"reject_on has unknown trigger kind: {module_id}: {kind}")
                continue
            if "value" not in trigger:
                errors.append(f"reject_on trigger missing value: {module_id}: {kind}")
            if _trigger_key(trigger) in positive_keys:
                errors.append(
                    f"reject_on contradicts a positive trigger (same kind+value): "
                    f"{module_id}: {kind}={trigger.get('value')}"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ACLNN knowledge manifest.")
    parser.add_argument("--knowledge", default=str(DEFAULT_KNOWLEDGE))
    args = parser.parse_args()
    errors = validate(Path(args.knowledge).resolve())
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
