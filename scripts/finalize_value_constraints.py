#!/usr/bin/env python3
"""Finalize candidate value constraints after review.

The deterministic extractor emits ``origin="doc_candidate"`` entries with
``review_required=true``.  This step:

1. checks each candidate against the plugin contract (``value_attr``/target);
2. keeps only rules compatible with the chosen generator primitive;
3. rebuilds ``src_txt_line`` from the per-rule evidence;
4. with ``--confirm``, flips ``origin`` to ``doc`` and clears ``review_required``
   so generation may consume it.

Without ``--confirm`` the output remains a review file and is still skipped by
``detect_plugin_value_specs``.

Usage:
    python scripts/finalize_value_constraints.py \
        --candidates <run>/inputs/value_constraints.candidates.json \
        --plugin-requirements <run>/inputs/plugin_requirements.json \
        --output <run>/inputs/value_constraints.json --confirm
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GENERATOR_RULES = {
    "prefix_sum_prefix0": {
        "first_zero", "non_negative", "non_decreasing", "length_b_plus_1",
    },
    "within_prefix": {"non_negative", "length_b"},
    "lt_scalar": {"non_negative", "length_b", "lt_ratio"},
    "non_negative_monotonic": {"non_negative", "non_decreasing"},
}


def _load_entries(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("value_constraints") or []
    return [item for item in payload if isinstance(item, dict)]


def finalize(
    candidates: list[dict],
    requirements: dict | None,
    confirm: bool,
) -> tuple[list[dict], list[dict]]:
    required_by_attr: dict[str, dict] = {}
    required_by_param: dict[str, dict] = {}
    if isinstance(requirements, dict):
        for item in requirements.get("required_value_attrs") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            target = str(item.get("target_param") or "")
            if name:
                required_by_attr[name] = item
            if target:
                required_by_param[target] = item

    accepted: list[dict] = []
    dropped: list[dict] = []
    for entry in candidates:
        param = str(entry.get("param") or "")
        value_attr = str(entry.get("value_attr") or "")
        generator = entry.get("generator")
        requirement = required_by_attr.get(value_attr) or required_by_param.get(param)
        reason = ""
        if requirement is None and (required_by_attr or required_by_param):
            reason = "not required by plugin contract"
        elif generator not in GENERATOR_RULES:
            reason = f"unsupported generator {generator!r}"
        rules = [rule for rule in entry.get("rules") or [] if isinstance(rule, str)]
        allowed = GENERATOR_RULES.get(generator, set())
        kept = [rule for rule in rules if rule in allowed]
        if not reason and not kept:
            reason = "no rules compatible with generator"
        if reason:
            dropped.append({
                "param": param,
                "value_attr": value_attr,
                "generator": generator,
                "reason": reason,
                "rules": rules,
            })
            continue
        evidence = entry.get("rule_evidence") or {}
        lines: set[int] = set()
        for rule in kept:
            values = evidence.get(rule)
            if isinstance(values, list):
                lines.update(
                    int(line) for line in values
                    if isinstance(line, int) and not isinstance(line, bool)
                )
        if not lines:
            lines.update(
                int(line) for line in entry.get("src_txt_line") or []
                if isinstance(line, int) and not isinstance(line, bool)
            )
        accepted.append({
            "param": param or (requirement or {}).get("target_param", ""),
            "value_attr": value_attr or (requirement or {}).get("name", ""),
            "rules": kept,
            "generator": generator,
            "generator_options": entry.get("generator_options") or {},
            "src_txt_line": sorted(lines),
            "origin": "doc" if confirm else "doc_candidate",
            "review_required": not confirm,
            "dropped_rules": [rule for rule in rules if rule not in kept],
        })
    return accepted, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize value constraints")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--plugin-requirements", default=None)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--confirm", action="store_true",
        help="mark entries as reviewed (origin=doc, review_required=false)",
    )
    args = parser.parse_args()

    candidates = _load_entries(Path(args.candidates).expanduser())
    requirements = None
    if args.plugin_requirements:
        requirements = json.loads(
            Path(args.plugin_requirements).expanduser().read_text(encoding="utf-8")
        )
    accepted, dropped = finalize(candidates, requirements, args.confirm)
    payload = {
        "ok": True,
        "confirmed": bool(args.confirm),
        "value_constraints": accepted,
        "dropped": dropped,
    }
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "ok": True,
        "output": str(output),
        "accepted": len(accepted),
        "dropped": len(dropped),
        "confirmed": bool(args.confirm),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
