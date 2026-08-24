#!/usr/bin/env python3
"""Preanalyze one ACLNN document and route the first-version knowledge base."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KNOWLEDGE = ROOT / "knowledge" / "aclnn"
SOURCE_ANALYSIS_SCOPE = "source_analysis"


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_list(raw: str) -> list[str]:
    """解析 YAML 内联列表，兼容带引号项与无引号裸名。

    之前用 ``r'["\\']([^"\\']+)["\\']'`` 只匹配带引号项，导致无引号的
    ``depends_on: [type_derivation, broadcast_relation]`` 被解析为空列表——
    depends_on 闭包长期静默失效（被依赖模块恰好都 default_load 或独立触发而掩盖）。
    现按逗号分割、剥外层括号与引号，覆盖两种写法。
    """
    items: list[str] = []
    for tok in raw.strip().strip("[]").split(","):
        tok = tok.strip().strip("\"'")
        if tok:
            items.append(tok)
    return items


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ValueError("knowledge module must start with frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("knowledge module frontmatter is not closed")
    meta: dict = {"triggers": [], "reject_on": [], "depends_on": []}
    current: dict | None = None
    current_list: str | None = None
    for line in text[3:end].splitlines():
        stripped = line.rstrip()
        if stripped.startswith("  - kind:"):
            current = {"kind": stripped.split(":", 1)[1].strip()}
            (meta["reject_on"] if current_list == "reject_on" else meta["triggers"]).append(current)
        elif stripped.startswith("    value:") and current is not None:
            raw = stripped.split(":", 1)[1].strip()
            current["value"] = _parse_list(raw) if raw.startswith("[") else raw.strip('"\'')
        elif stripped.startswith("module:"):
            meta["module"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("scope:"):
            meta["scope"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("default_load:"):
            meta["default_load"] = stripped.split(":", 1)[1].strip().lower() == "true"
        elif stripped.startswith("depends_on:"):
            meta["depends_on"] = _parse_list(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("reject_on:"):
            current_list = "reject_on" if stripped.split(":", 1)[1].strip() else None
        elif stripped.startswith("triggers:"):
            current_list = "triggers"
    return meta, text[end + 4 :].lstrip("\r\n")


def extract_operator_name(text: str) -> str:
    heading = re.search(r"(?m)^#\s*(aclnn[A-Za-z0-9_]+)\b", text)
    if heading:
        return heading.group(1)
    token = re.search(r"\baclnn[A-Za-z0-9_]+\b", text)
    return re.sub(r"GetWorkspaceSize$", "", token.group(0)) if token else ""


def preanalyze_document(doc: Path, text: str | None = None) -> dict:
    raw = text if text is not None else doc.read_text(encoding="utf-8", errors="replace")
    name = extract_operator_name(raw)
    platforms = []
    for candidate in re.findall(r"Atlas[^|\n<]{2,80}(?:产品|加速卡)", raw):
        normalized = re.sub(r"\s+", " ", candidate).strip(" ：:，,。")
        if normalized not in platforms:
            platforms.append(normalized)
    flags = {
        "workspace_interface": bool(re.search(r"\baclnn\w+GetWorkspaceSize\s*\(", raw)),
        "optional_or_null": bool(re.search(r"Optional|nullptr|空指针|可选输入|可选输出", raw, re.I)),
        "cross_parameter_relations": bool(re.search(r"相同|一致|等于|小于|大于|依赖|对应|满足.*关系", raw)),
        "conditional_scenarios": bool(re.search(r"支持场景|当.*时|仅当|groupType|splitItem", raw, re.I)),
        "quantization": bool(re.search(r"量化|反量化|pertensor|perchannel|pertoken|pergroup|perblock", raw, re.I)),
        "broadcast": bool(re.search(r"broadcast|广播|互推导", raw, re.I)),
        "layout_or_format": bool(re.search(r"FRACTAL|\bNZ\b|数据格式|format", raw, re.I)),
        "backward_or_grad": bool(re.search(r"Backward|Grad", name)),
    }
    return {
        "schema_version": "1.0",
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "document": {
            "path": str(doc.resolve()),
            "sha256": _sha_text(raw),
            "characters": len(raw),
            "lines": raw.count("\n") + 1,
            "markdown_table_rows": len(re.findall(r"(?m)^\|.*\|$", raw)),
            "html_tables": raw.lower().count("<table"),
        },
        "operator_family": "aclnn",
        "operator_name": name,
        "interface_mode": "two-stage" if flags["workspace_interface"] else "single-function",
        "platforms": platforms,
        "structural_flags": flags,
    }


def _load_modules(knowledge: Path) -> tuple[dict, dict]:
    manifest = json.loads((knowledge / "manifest.json").read_text(encoding="utf-8"))
    modules: dict[str, dict] = {}
    for item in manifest["modules"]:
        path = knowledge / item["path"]
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        if meta.get("module") != item["id"]:
            raise ValueError(f"module id mismatch: {path}: {meta.get('module')} != {item['id']}")
        modules[item["id"]] = {**item, **meta, "path_obj": path, "raw": raw, "body": body}
    return manifest, modules


def _trigger_evidence(trigger: dict, operator_name: str, text: str) -> str | None:
    kind, value = trigger["kind"], trigger.get("value")
    if kind == "operator_name_eq":
        return f"operator_name_eq:{value}" if operator_name == value else None
    if kind == "operator_name_regex":
        return f"operator_name_regex:{value}" if operator_name and re.search(value, operator_name) else None
    if kind == "name_contains":
        return f"name_contains:{value}" if operator_name and value in operator_name else None
    if kind == "doc_contains":
        match = re.search(value, text, re.I | re.M)
    elif kind == "format_any":
        values = value if isinstance(value, list) else [value]
        match = re.search("|".join(re.escape(x) for x in values), text, re.I)
    else:
        raise ValueError(f"unknown ACLNN knowledge trigger: {kind}")
    if not match:
        return None
    excerpt = re.sub(r"\s+", " ", text[max(0, match.start() - 50) : match.end() + 100]).strip()
    return f"{kind}:{value}; excerpt={excerpt[:220]}"


def route(
    doc: Path,
    knowledge: Path = DEFAULT_KNOWLEDGE,
    *,
    source_analysis_knowledge: bool = False,
) -> dict:
    text = doc.read_text(encoding="utf-8", errors="replace")
    preanalysis = preanalyze_document(doc, text)
    manifest, modules = _load_modules(knowledge)
    selected: set[str] = set()
    evidence: dict[str, list[str]] = {}
    rejected_candidates: list[dict] = []
    disabled_ids: set[str] = set()
    for module_id, item in modules.items():
        if (
            item.get("scope") == SOURCE_ANALYSIS_SCOPE
            and not source_analysis_knowledge
        ):
            disabled_ids.add(module_id)
            disabled_hits = [
                hit for trigger in item.get("triggers", [])
                if (hit := _trigger_evidence(
                    trigger, preanalysis["operator_name"], text
                ))
            ]
            if item.get("default_load") or disabled_hits:
                rejected_candidates.append({
                    "module_id": module_id,
                    "scope": item["scope"],
                    "reason": "feature_disabled",
                    "evidence": [
                        "source_analysis_knowledge=false",
                        *disabled_hits,
                    ],
                })
            continue
        if item.get("default_load"):
            selected.add(module_id)
            evidence[module_id] = ["default_load"]
        for trigger in item.get("triggers", []):
            hit = _trigger_evidence(trigger, preanalysis["operator_name"], text)
            if hit:
                selected.add(module_id)
                evidence.setdefault(module_id, []).append(hit)
    # reject_on: a positive hit removes an otherwise-selected module. Negative
    # precedence wins over default_load / positive triggers, so a doc that
    # explicitly negates a concept (e.g. "不支持广播") does not pull the rule.
    rejected_ids: set[str] = set()
    for module_id, item in modules.items():
        if module_id not in selected:
            continue
        for trigger in item.get("reject_on", []):
            hit = _trigger_evidence(trigger, preanalysis["operator_name"], text)
            if hit:
                selected.discard(module_id)
                evidence.pop(module_id, None)
                rejected_ids.add(module_id)
                rejected_candidates.append({
                    "module_id": module_id,
                    "scope": item["scope"],
                    "reason": "reject_on",
                    "evidence": [hit],
                })
                break
    changed = True
    while changed:
        changed = False
        for module_id in tuple(selected):
            for dependency in modules[module_id].get("depends_on", []):
                if dependency not in modules:
                    raise ValueError(f"unknown dependency {dependency!r} for {module_id!r}")
                if dependency in disabled_ids:
                    continue
                if dependency in rejected_ids:
                    # reject_on takes precedence; do not re-pull a rejected dep.
                    continue
                if dependency not in selected:
                    selected.add(dependency)
                    evidence.setdefault(dependency, []).append(f"required_by:{module_id}")
                    changed = True
    resolved = sorted(selected, key=lambda module_id: (-modules[module_id].get("priority", 0), module_id))
    decisions = []
    for module_id in resolved:
        item = modules[module_id]
        basis = "default" if item.get("default_load") else (
            "explicit_source_analysis" if item["scope"] == SOURCE_ANALYSIS_SCOPE else
            "exact_operator" if item["scope"] == "operator" else
            "dependency" if all(x.startswith("required_by:") for x in evidence.get(module_id, [])) else
            "document_or_operator_signal"
        )
        decisions.append({
            "module_id": module_id,
            "scope": item["scope"],
            "decision": (
                "applicable_explicit_source_analysis"
                if item["scope"] == SOURCE_ANALYSIS_SCOPE
                else "applicable_exact_operator_match"
                if item["scope"] == "operator"
                else "applicable"
            ),
            "basis": basis,
            "evidence": evidence.get(module_id, []),
        })
    return {
        "schema_version": "1.0",
        "route_source": "live-v4-split-routing",
        "preanalysis": preanalysis,
        "resolved_modules": resolved,
        "applicability": {
            "policy": (
                "exact-operator modules load only on operator_name_eq match; "
                "source_analysis modules additionally require an explicit feature flag"
            ),
            "feature_flags": {
                "source_analysis_knowledge": source_analysis_knowledge,
            },
            "selected_modules": decisions,
            "rejected_candidates": rejected_candidates,
            "selected_count": len(decisions),
        },
        "manifest_schema_version": manifest.get("schema_version", ""),
    }


def render_bundle(result: dict, knowledge: Path = DEFAULT_KNOWLEDGE) -> str:
    _, modules = _load_modules(knowledge)
    has_source_analysis = any(
        modules[module_id].get("scope") == SOURCE_ANALYSIS_SCOPE
        for module_id in result["resolved_modules"]
    )
    authority_note = (
        "> 当前算子文档是常规约束的最高优先级来源；显式启用的 source_analysis "
        "模块是锁定源码版本的附加约束源，冲突时必须保留来源、版本与可信度，不得静默覆盖。"
        if has_source_analysis else
        "> 当前算子文档是唯一事实源；知识只用于防漏、规范表达和检查适用性。"
    )
    lines = [
        "# 已装配的 ACLNN 约束知识", "",
        f"- 算子：`{result['preanalysis']['operator_name'] or 'UNKNOWN'}`",
        f"- 接口：`{result['preanalysis']['interface_mode']}`", "",
        authority_note, "",
    ]
    for module_id in result["resolved_modules"]:
        lines.extend(["---", "", f"<!-- knowledge-module: {module_id} -->", "", modules[module_id]["body"].rstrip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Route ACLNN knowledge for one document.")
    parser.add_argument("--doc", required=True)
    parser.add_argument("--knowledge", default=str(DEFAULT_KNOWLEDGE))
    parser.add_argument("--json")
    parser.add_argument("--bundle")
    parser.add_argument(
        "--source-analysis-knowledge",
        action="store_true",
        help="显式启用按算子名精准命中的源码分析约束知识；默认关闭。",
    )
    args = parser.parse_args()
    result = route(
        Path(args.doc).resolve(),
        Path(args.knowledge).resolve(),
        source_analysis_knowledge=args.source_analysis_knowledge,
    )
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.bundle:
        Path(args.bundle).write_text(render_bundle(result, Path(args.knowledge).resolve()), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
