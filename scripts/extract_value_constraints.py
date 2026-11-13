#!/usr/bin/env python3
"""Candidate value-constraint extraction from operator documents.

This is a deterministic *candidate* extractor.  It reads the primary and
companion docs plus the plugin contract (``plugin_requirements.json``) and
emits value-level rules for the tensors the plugin expects.  Entries are
marked ``origin="doc_candidate"`` and ``review_required=true``; the EXTRACT /
CHECK stage must confirm them (and flip to ``origin="doc"``) before generation
is allowed to consume them.

No operator name is hardcoded: the target tensors come from the plugin
contract, and the rules come from the document text.

Usage:
    python scripts/extract_value_constraints.py \
        --doc <run>/inputs/<op>.md \
        --companion-doc <run>/inputs/<op>Metadata.md \
        --plugin-requirements <run>/inputs/plugin_requirements.json \
        --output <run>/inputs/value_constraints.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RULE_PATTERNS = {
    "first_zero": re.compile(r"(?:首个?元素|第一个元素|首元素).{0,24}?0|固定为\s*0"),
    "non_decreasing": re.compile(
        r"非递减|单调不减|大于等于前一个|后一个元素.{0,16}?大于等于"
    ),
    "non_negative": re.compile(r"不能出现负值|非负|大于等于\s*0"),
    "length_b_plus_1": re.compile(r"B\s*\+\s*1|长度为\s*B\s*\+\s*1|shape.{0,8}B\s*\+\s*1"),
    "length_b": re.compile(r"长度为\s*B\b|shape.{0,8}\(B\b"),
    "lt_ratio": re.compile(r"小于.{0,16}?cmpRatio|cmpRatio.{0,16}?小于|< ?cmpRatio"),
    "capacity": re.compile(r"block_num|block_size|block\s*总数|容量"),
}
RULE_WINDOW = 900


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _find_param_offsets(text: str, names: list[str]) -> list[int]:
    offsets: set[int] = set()
    for name in names:
        if not name:
            continue
        for match in re.finditer(re.escape(name), text, re.IGNORECASE):
            offsets.add(match.start())
    return sorted(offsets)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _rules_near(text: str, offset: int) -> dict[str, list[int]]:
    start = max(0, offset - RULE_WINDOW // 2)
    end = min(len(text), offset + RULE_WINDOW // 2)
    window = text[start:end]
    evidence: dict[str, list[int]] = {}
    for rule, pattern in RULE_PATTERNS.items():
        for match in pattern.finditer(window):
            evidence.setdefault(rule, []).append(
                _line_of(text, start + match.start())
            )
    return {rule: sorted(set(lines)) for rule, lines in evidence.items()}


def _generator_for(param: str, base: str, rules: list[str]) -> dict:
    lowered = f"{param} {base}".casefold()
    if "cuseqlens" in lowered:
        is_key = base.casefold().endswith("_k") or "k" in base.casefold().split("_")[-1:]
        offset = 1 if "length_b_plus_1" in rules else 0
        return {
            "generator": "prefix_sum_prefix0",
            "generator_options": {
                "length_from": param,
                "total_from": "k" if is_key else "q",
                "layout_param": "layoutKOptional" if is_key else "layoutQOptional",
                "length_offset": offset,
            },
        }
    if "seqused" in lowered:
        is_key = base.casefold().endswith("_k")
        options = {
            "length_from": param,
            "ref_attr": (
                "cu_seqlens_k_values" if is_key else "cu_seqlens_q_values"
            ),
            "default_value": 1,
        }
        if is_key:
            # Paged key capacity: block_table/block_num must cover
            # sum(ceil(seqused_k[b] / block_size)).  The doc states the
            # block-table capacity rule; cap generated values so the pytest
            # golden's "key block num" check is satisfiable.
            options.update({
                "block_budget_from": "k",
                "block_layout_param": "layoutKOptional",
                "block_layout_value": "PA_BBND",
                "block_num_dim": 0,
                "block_size_dim": 1,
            })
        return {
            "generator": "within_prefix",
            "generator_options": options,
        }
    if "cmpresidual" in lowered:
        return {
            "generator": "lt_scalar",
            "generator_options": {
                "length_from": param,
                "ratio_attr": "cmpRatio",
                "when_attr": "maskMode",
                "when_value": 3,
            },
        }
    return {"generator": None, "generator_options": {}}


def extract(
    doc: Path,
    companions: list[Path],
    requirements: dict | None,
) -> list[dict]:
    texts: list[tuple[Path, str]] = [(doc, doc.read_text(encoding="utf-8", errors="replace"))]
    for companion in companions:
        texts.append((companion, companion.read_text(encoding="utf-8", errors="replace")))

    targets: list[tuple[str, str]] = []
    if isinstance(requirements, dict):
        for item in requirements.get("required_value_attrs") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            target = str(item.get("target_param") or "")
            base = str(item.get("base_name") or name.removesuffix("_values"))
            if target or base:
                targets.append((target or name, base))
    if not targets:
        return []

    entries: list[dict] = []
    for param, base in targets:
        evidence: dict[str, set[int]] = {}
        for _, text in texts:
            for offset in _find_param_offsets(text, [param, base]):
                for rule, lines in _rules_near(text, offset).items():
                    evidence.setdefault(rule, set()).update(lines)
        if not evidence:
            continue
        rules = sorted(evidence)
        evidence = {rule: sorted(lines) for rule, lines in evidence.items()}
        lines = sorted({line for values in evidence.values() for line in values})
        generator = _generator_for(param, base, rules)
        entries.append({
            "param": param,
            "value_attr": base.removesuffix("_values") + "_values",
            "rules": rules,
            "rule_evidence": evidence,
            **generator,
            "src_txt_line": lines,
            "origin": "doc_candidate",
            "review_required": True,
        })
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Candidate value-constraint extractor")
    parser.add_argument("--doc", required=True)
    parser.add_argument("--companion-doc", action="append", default=[])
    parser.add_argument("--plugin-requirements", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    doc = Path(args.doc).expanduser()
    if not doc.is_file():
        print(json.dumps({"ok": False, "code": "DOC_NOT_FOUND", "doc": str(doc)}, ensure_ascii=False))
        return 2
    companions = [Path(item).expanduser() for item in args.companion_doc]
    requirements = None
    if args.plugin_requirements:
        requirements = json.loads(
            Path(args.plugin_requirements).read_text(encoding="utf-8")
        )
    entries = extract(doc, companions, requirements)
    payload = {
        "ok": True,
        "source": {
            "doc": str(doc),
            "companion_docs": [str(path) for path in companions],
            "plugin_requirements": args.plugin_requirements or "",
        },
        "value_constraints": entries,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(output), "count": len(entries)}, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
