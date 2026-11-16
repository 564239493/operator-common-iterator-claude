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
    "prefix_sum": re.compile(r"前缀和|累计|cumsum|累加"),
    "length_b_plus_1": re.compile(r"B\s*\+\s*1|长度为\s*B\s*\+\s*1|shape.{0,8}B\s*\+\s*1"),
    "length_b": re.compile(r"长度为\s*B\b|shape.{0,8}\(B\b|\(B,\s*\)"),
    "lt_ratio": re.compile(r"小于.{0,16}?cmpRatio|cmpRatio.{0,16}?小于|< ?cmpRatio"),
    "topk_range": re.compile(r"\[\s*0\s*,\s*K\s*\]|0\s*到\s*K|0\s*~\s*K"),
    "capacity": re.compile(r"block_num|block_size|block\s*总数|容量"),
}
SCOPE_FALLBACK = 240


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


def _scope_for_offset(text: str, offset: int) -> tuple[str, int, bool]:
    left = text.rfind("<tr", 0, offset)
    right = text.find("</tr>", offset)
    if left != -1 and right != -1 and left <= offset <= right:
        return text[left : right + 5], left, True
    start = text.rfind("\n\n", 0, offset)
    end = text.find("\n\n", offset)
    begin = start + 2 if start != -1 else max(0, offset - SCOPE_FALLBACK)
    finish = end if end != -1 else min(len(text), offset + SCOPE_FALLBACK)
    return text[begin:finish], begin, False


def _rules_for_param(text: str, names: list[str]) -> dict[str, list[int]]:
    row_scopes: list[tuple[str, int]] = []
    other_scopes: list[tuple[str, int]] = []
    for offset in _find_param_offsets(text, names):
        scope, start, is_row = _scope_for_offset(text, offset)
        (row_scopes if is_row else other_scopes).append((scope, start))
    scopes = row_scopes or other_scopes
    evidence: dict[str, set[int]] = {}
    for scope, start in scopes:
        for rule, pattern in RULE_PATTERNS.items():
            for match in pattern.finditer(scope):
                evidence.setdefault(rule, set()).add(
                    _line_of(text, start + match.start())
                )
    return {rule: sorted(lines) for rule, lines in evidence.items()}


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _input_name(token: str, constraints: dict | None) -> str:
    if not constraints:
        return ""
    want = _norm_name(token)
    for name in constraints.get("inputs") or {}:
        normalized = _norm_name(name)
        if normalized in (want, want + "optional"):
            return str(name)
    return ""


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)


def _generator_for(
    param: str,
    base: str,
    rules: list[str],
    requirements: dict | None,
    constraints: dict | None,
    scope_text: str,
) -> dict:
    lowered = f"{param} {base}".casefold()
    side = "cmp" if "cmp" in lowered else ("ori" if "ori" in lowered else "q")
    layout = _input_name(
        "layoutQOptional" if side == "q" else "layoutKvOptional", constraints
    )
    if "residual" in lowered or "lt_ratio" in rules:
        ratio = _input_name("cmpRatio", constraints)
        when = _input_name("cmpMaskMode", constraints)
        options = {"length_from": param}
        if ratio:
            options["ratio_attr"] = ratio
        if when:
            options["when_attr"] = when
            options["when_value"] = 3
        return {"generator": "lt_scalar", "generator_options": options}
    if (
        "cuseqlens" in lowered
        or "prefix_sum" in rules
        or "length_b_plus_1" in rules
    ):
        total = _input_name(
            "q" if side == "q" else ("oriKvOptional" if side == "ori" else "cmpKvOptional"),
            constraints,
        )
        options = {
            "length_from": param,
            "length_offset": 1
            if ("length_b_plus_1" in rules or "prefix_sum" in rules)
            else 0,
        }
        if total:
            options["total_from"] = total
        if layout:
            options["layout_param"] = layout
        return {"generator": "prefix_sum_prefix0", "generator_options": options}
    if "seqused" in lowered:
        ref = ""
        for item in (requirements or {}).get("required_value_attrs") or []:
            name = str(item.get("name") or "")
            if not name.startswith("cu_seqlens_"):
                continue
            if side == "cmp" and "cmp_kv" in name:
                ref = name
                break
            if side == "ori" and "ori_kv" in name:
                ref = name
                break
            if side == "q" and name.endswith("_q_values"):
                ref = name
                break
        options = {"length_from": param, "default_value": 1}
        if ref:
            options["ref_attr"] = ref
        if side != "q":
            budget = _input_name(
                "oriKvOptional" if side == "ori" else "cmpKvOptional", constraints
            )
            if budget:
                options.update({
                    "block_budget_from": budget,
                    "block_layout_param": layout or "layoutKvOptional",
                    "block_layout_value": "PA_BBND",
                    "block_num_dim": 0,
                    "block_size_dim": 1,
                })
        return {"generator": "within_prefix", "generator_options": options}
    return {"generator": None, "generator_options": {}}


def extract(
    doc: Path,
    companions: list[Path],
    requirements: dict | None,
    constraints: dict | None = None,
) -> list[dict]:
    texts: list[tuple[Path, str]] = [(doc, doc.read_text(encoding="utf-8", errors="replace"))]
    for companion in companions:
        texts.append((companion, companion.read_text(encoding="utf-8", errors="replace")))

    items: list[dict] = []
    if isinstance(requirements, dict):
        for item in requirements.get("required_value_attrs") or []:
            if isinstance(item, dict):
                items.append(item)
    if not items:
        return []

    entries: list[dict] = []
    for item in items:
        name = str(item.get("name") or "")
        param = str(item.get("target_param") or "")
        base = str(item.get("base_name") or name.removesuffix("_values"))
        if not param and not base:
            continue
        evidence: dict[str, set[int]] = {}
        scopes: list[str] = []
        for _, text in texts:
            for offset in _find_param_offsets(text, [param, base]):
                scope, _, _ = _scope_for_offset(text, offset)
                scopes.append(scope)
            for rule, lines in _rules_for_param(text, [param, base]).items():
                evidence.setdefault(rule, set()).update(lines)
        if not evidence:
            continue
        rules = sorted(evidence)
        evidence = {rule: sorted(lines) for rule, lines in evidence.items()}
        lines = sorted({line for values in evidence.values() for line in values})
        generator = _generator_for(
            param, base, rules, requirements, constraints, "\n".join(scopes)
        )
        review_required = generator.get("generator") is None
        entries.append({
            "param": param,
            "value_attr": name,
            "rules": rules,
            "rule_evidence": evidence,
            **generator,
            "src_txt_line": lines,
            "origin": "doc_candidate",
            "review_required": True,
            "blocked_reason": (
                "no compatible generator inferred"
                if review_required else ""
            ),
            "resolution": item.get("resolution") or {},
        })
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Candidate value-constraint extractor")
    parser.add_argument("--doc", required=True)
    parser.add_argument("--companion-doc", action="append", default=[])
    parser.add_argument("--plugin-requirements", default=None)
    parser.add_argument("--constraints", default=None)
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
    constraints = None
    if args.constraints:
        constraints = json.loads(
            Path(args.constraints).read_text(encoding="utf-8")
        )
    entries = extract(doc, companions, requirements, constraints)
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
