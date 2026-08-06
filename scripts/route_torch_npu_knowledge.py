#!/usr/bin/env python3
"""Preanalyze one torch_npu document and route the first-version knowledge base.

Mirrors ``scripts/route_aclnn_knowledge.py`` but for the torch_npu family: a
prototype-based operator name, ``file_name_regex`` as an extra trigger kind, and
a torch_npu knowledge root. ACLNN and torch_npu stay strictly isolated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KNOWLEDGE = ROOT / "knowledge" / "torch_npu"


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_list(raw: str) -> list[str]:
    return re.findall(r'["\']([^"\']+)["\']', raw)


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


def extract_operator_name(doc_text: str) -> str:
    """Return the callable from the prototype, using H1 only as a fallback."""
    match = re.search(r"(?m)^(?:torch_npu\.|torch\.npu\.)[A-Za-z_]\w*(?=\s*\()", doc_text)
    if match:
        return match.group(0)
    bare = re.search(r"(?m)^(npu_[A-Za-z_]\w*)(?=\s*\()", doc_text)
    if bare:
        return f"torch_npu.{bare.group(1)}"
    heading = re.search(r"^#\s*(?:（beta）)?\s*([^\r\n<]+)", doc_text, re.MULTILINE)
    if not heading:
        return ""
    candidate = heading.group(1).strip().replace("\\_", "_")
    if re.fullmatch(r"(?:torch_npu|torch\.npu)\.[A-Za-z_]\w*", candidate):
        return candidate
    malformed = re.fullmatch(r"torch_npu-(npu_[A-Za-z_]\w*)", candidate)
    return f"torch_npu.{malformed.group(1)}" if malformed else ""


# Torch docs carry a product_support table; platform keys stay dynamic.
_PLATFORM_RE = re.compile(r"Atlas[^|\n<]{2,80}(?:产品|加速卡)")


def preanalyze_document(doc: Path, text: str | None = None) -> dict:
    raw = text if text is not None else doc.read_text(encoding="utf-8", errors="replace")
    name = extract_operator_name(raw)
    platforms = []
    for candidate in _PLATFORM_RE.findall(raw):
        normalized = re.sub(r"\s+", " ", candidate).strip(" ：:，,。")
        if normalized not in platforms:
            platforms.append(normalized)
    flags = {
        "two_stage_workspace": bool(re.search(r"GetWorkspaceSize|run_device_side", raw, re.I)),
        "optional_or_null": bool(re.search(r"Optional|nullptr|空指针|可选输入|可选输出", raw, re.I)),
        "cross_parameter_relations": bool(re.search(r"相同|一致|等于|小于|大于|依赖|对应|满足.*关系", raw)),
        "conditional_scenarios": bool(re.search(r"支持场景|当.*时|仅当|input_layout|sparse_mode", raw, re.I)),
        "quantization": bool(re.search(r"量化|反量化|quant|dequant|antiquant", raw, re.I)),
        "attention": bool(re.search(r"attention|flash|softmax_lse|PageAttention", raw, re.I)),
        "inplace_or_stateful": bool(re.search(r"原地|in-?place|cache.*更新|状态", raw, re.I)),
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
        "operator_family": "torch_npu",
        "operator_name": name,
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


def _trigger_evidence(trigger: dict, operator_name: str, doc_name: str, text: str) -> str | None:
    kind, value = trigger["kind"], trigger.get("value")
    if kind == "operator_name_eq":
        return f"operator_name_eq:{value}" if operator_name == value else None
    if kind == "operator_name_regex":
        return f"operator_name_regex:{value}" if operator_name and re.search(value, operator_name) else None
    if kind == "name_contains":
        return f"name_contains:{value}" if operator_name and value in operator_name else None
    if kind == "file_name_regex":
        return f"file_name_regex:{value}" if doc_name and re.search(value, doc_name, re.I) else None
    if kind == "doc_contains":
        match = re.search(value, text, re.I | re.M)
    else:
        raise ValueError(f"unknown torch_npu knowledge trigger: {kind}")
    if not match:
        return None
    excerpt = re.sub(r"\s+", " ", text[max(0, match.start() - 50) : match.end() + 100]).strip()
    return f"{kind}:{value}; excerpt={excerpt[:220]}"


def route(doc: Path, knowledge: Path = DEFAULT_KNOWLEDGE) -> dict:
    text = doc.read_text(encoding="utf-8", errors="replace")
    doc_name = doc.name
    preanalysis = preanalyze_document(doc, text)
    manifest, modules = _load_modules(knowledge)
    selected: set[str] = set()
    evidence: dict[str, list[str]] = {}
    for module_id, item in modules.items():
        if item.get("default_load"):
            selected.add(module_id)
            evidence[module_id] = ["default_load"]
        for trigger in item.get("triggers", []):
            hit = _trigger_evidence(trigger, preanalysis["operator_name"], doc_name, text)
            if hit:
                selected.add(module_id)
                evidence.setdefault(module_id, []).append(hit)
    rejected_ids: set[str] = set()
    rejected_candidates: list[dict] = []
    for module_id, item in modules.items():
        if module_id not in selected:
            continue
        for trigger in item.get("reject_on", []):
            hit = _trigger_evidence(trigger, preanalysis["operator_name"], doc_name, text)
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
                if dependency in rejected_ids:
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
            "exact_operator" if item["scope"] == "operator" else
            "dependency" if all(x.startswith("required_by:") for x in evidence.get(module_id, [])) else
            "document_or_operator_signal"
        )
        decisions.append({
            "module_id": module_id,
            "scope": item["scope"],
            "decision": "applicable_exact_operator_match" if item["scope"] == "operator" else "applicable",
            "basis": basis,
            "evidence": evidence.get(module_id, []),
        })
    return {
        "schema_version": "1.0",
        "route_source": "live-v3-split-routing",
        "preanalysis": preanalysis,
        "resolved_modules": resolved,
        "applicability": {
            "policy": "exact-operator modules load only on operator_name_eq match; current operator document is authoritative",
            "selected_modules": decisions,
            "rejected_candidates": rejected_candidates,
            "selected_count": len(decisions),
        },
        "manifest_schema_version": manifest.get("schema_version", ""),
    }


# Deterministic family-ordering preserved from the legacy MODULE_ORDER so that
# general family knowledge precedes exact-operator checklists.
_FAMILY_ORDER = [
    "collections_and_grouped_ops",
    "matrix_product_family",
    "distributed_collectives",
    "indexed_access_and_update",
    "normalization_family",
    "selection_reduction_sampling",
    "attention_family",
    "quantization",
    "inplace_and_stateful_ops",
    "npu_kv_quant_sparse_flash_attention",
    "npu_sparse_flash_attention",
    "npu_lightning_indexer",
    "npu_quant_lightning_indexer",
    "npu_mla_prolog_v3",
    "npu_fused_infer_attention_score",
]


def render_bundle(result: dict, knowledge: Path = DEFAULT_KNOWLEDGE) -> str:
    _, modules = _load_modules(knowledge)
    lines = [
        "# 已装配的 torch_npu 约束知识", "",
        f"- 算子：`{result['preanalysis']['operator_name'] or 'UNKNOWN'}`", "",
        "> 当前算子文档是唯一事实源；知识只用于防漏、规范表达和检查适用性。", "",
    ]
    resolved = result["resolved_modules"]
    ordered = sorted(resolved, key=lambda mid: (
        _FAMILY_ORDER.index(mid) if mid in _FAMILY_ORDER else len(_FAMILY_ORDER),
        mid,
    ))
    for module_id in ordered:
        lines.extend(["---", "", f"<!-- knowledge-module: {module_id} -->", "", modules[module_id]["body"].rstrip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Route torch_npu knowledge for one document.")
    parser.add_argument("--doc", required=True)
    parser.add_argument("--knowledge", default=str(DEFAULT_KNOWLEDGE))
    parser.add_argument("--json")
    parser.add_argument("--bundle")
    args = parser.parse_args()
    result = route(Path(args.doc).resolve(), Path(args.knowledge).resolve())
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.bundle:
        Path(args.bundle).write_text(render_bundle(result, Path(args.knowledge).resolve()), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
