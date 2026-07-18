#!/usr/bin/env python3
"""Assemble the isolated torch_npu constraint-extraction prompt.

The ACLNN selector in ``select_prompt.py`` and this selector intentionally use
different roots.  A torch_npu run receives:

1. the latest ``prompts/torch_npu_constraints_extract_vN.md`` baseline;
2. the always-on torch_npu documentation conventions; and
3. only the torch_npu knowledge modules matched by the current document.

No file under ``prompts/modules`` is considered here.  This keeps ACLNN
workspace/signature assumptions out of the Python E2E API extraction path.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_ROOT = ROOT / "knowledge" / "torch_npu"
COMMON_PATH = KNOWLEDGE_ROOT / "common" / "documentation_conventions.md"
PATTERNS_DIR = KNOWLEDGE_ROOT / "operator_patterns"

# General family knowledge must precede exact-operator review checklists.
MODULE_ORDER = [
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


def _parse_list(raw: str) -> list[str]:
    return re.findall(r'"([^"]*)"', raw)


def _parse_value(raw: str):
    return _parse_list(raw) if raw.startswith("[") else raw.strip().strip('"')


def parse_manifest(md_text: str) -> dict:
    """Parse the deliberately small YAML-like frontmatter used by modules."""
    if not md_text.startswith("---"):
        raise ValueError("torch_npu knowledge module must start with --- frontmatter")
    end = md_text.find("\n---", 3)
    if end < 0:
        raise ValueError("torch_npu knowledge module frontmatter is not closed")
    frontmatter = md_text[3:end]
    body = md_text[end + 4 :].lstrip("\n")
    manifest: dict = {"triggers": [], "depends_on": []}
    current = None
    for line in frontmatter.splitlines():
        stripped = line.rstrip()
        if stripped.startswith("  - kind:"):
            current = {"kind": stripped[len("  - kind:") :].strip()}
            manifest["triggers"].append(current)
        elif stripped.startswith("    value:") and current is not None:
            current["value"] = _parse_value(
                stripped[len("    value:") :].strip()
            )
        elif stripped.startswith("module:"):
            manifest["module"] = stripped[len("module:") :].strip()
        elif stripped.startswith("description:"):
            manifest["description"] = stripped[len("description:") :].strip()
        elif stripped.startswith("depends_on:"):
            manifest["depends_on"] = _parse_list(
                stripped[len("depends_on:") :].strip()
            )
    if "module" not in manifest:
        raise ValueError("torch_npu knowledge module is missing module: field")
    return {"manifest": manifest, "body": body}


def extract_operator_name(doc_text: str) -> str:
    """Return the callable from the prototype, using H1 only as a fallback."""
    # Several 26.0.0 documents have a malformed or stale H1.  The executable
    # prototype is authoritative for both operator_name and exact-module routing.
    match = re.search(
        r"(?m)^(?:torch_npu\.|torch\.npu\.)[A-Za-z_]\w*(?=\s*\()",
        doc_text,
    )
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


def trigger_matches(
    kind: str,
    value,
    operator_name: str,
    doc_text: str,
    doc_name: str,
) -> bool:
    if kind == "operator_name_eq":
        return bool(operator_name) and operator_name == value
    if kind == "operator_name_regex":
        return bool(operator_name) and bool(re.search(value, operator_name))
    if kind == "doc_contains":
        return bool(re.search(value, doc_text, re.MULTILINE | re.IGNORECASE))
    if kind == "file_name_regex":
        return bool(re.search(value, doc_name, re.IGNORECASE))
    raise ValueError(f"unknown torch_npu trigger kind: {kind!r}")


def _load_modules() -> dict[str, dict]:
    files = {path.stem: path for path in PATTERNS_DIR.glob("*.md")}
    parsed = {
        name: parse_manifest(path.read_text(encoding="utf-8"))
        for name, path in files.items()
    }
    for name, item in parsed.items():
        declared = item["manifest"]["module"]
        if declared != name:
            raise ValueError(
                f"torch_npu module filename/name mismatch: {name!r} != {declared!r}"
            )
    return parsed


def classify(doc_text: str, doc_name: str = "") -> list[str]:
    """Return matched torch_npu knowledge modules in deterministic order."""
    operator_name = extract_operator_name(doc_text)
    parsed = _load_modules()
    loaded: set[str] = set()
    for name, item in parsed.items():
        if any(
            trigger_matches(
                trigger["kind"],
                trigger.get("value"),
                operator_name,
                doc_text,
                doc_name,
            )
            for trigger in item["manifest"]["triggers"]
        ):
            loaded.add(name)

    changed = True
    while changed:
        changed = False
        for name in list(loaded):
            for dependency in parsed[name]["manifest"]["depends_on"]:
                if dependency not in parsed:
                    raise ValueError(
                        f"torch_npu module {name!r} has unknown dependency {dependency!r}"
                    )
                if dependency not in loaded:
                    loaded.add(dependency)
                    changed = True

    return [name for name in MODULE_ORDER if name in loaded] + sorted(
        name for name in loaded if name not in MODULE_ORDER
    )


def assemble(base_path: Path, doc_path: Path, output_path: Path) -> list[str]:
    """Write a complete per-run torch_npu prompt snapshot and return modules."""
    if not COMMON_PATH.is_file():
        raise FileNotFoundError(f"missing torch_npu common knowledge: {COMMON_PATH}")
    base = base_path.read_text(encoding="utf-8")
    doc_text = doc_path.read_text(encoding="utf-8")
    parsed = _load_modules()
    names = classify(doc_text, doc_path.name)

    parts = [base.rstrip(), "", "---", "", "## 已装配的 torch_npu 通用知识", ""]
    parts.append(COMMON_PATH.read_text(encoding="utf-8").rstrip())
    if names:
        parts.extend(["", "---", "", "## 已装配的 torch_npu 算子知识", ""])
        for name in names:
            parts.append(parsed[name]["body"].rstrip())
            parts.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return ["common/documentation_conventions", *names]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble an isolated torch_npu extraction prompt snapshot."
    )
    parser.add_argument("--base", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--output")
    parser.add_argument("--list-modules", action="store_true")
    args = parser.parse_args()

    doc_path = Path(args.doc)
    doc_text = doc_path.read_text(encoding="utf-8")
    if args.list_modules:
        print(",".join(["common/documentation_conventions", *classify(doc_text, doc_path.name)]))
        return 0
    if not args.output:
        parser.error("--output is required unless --list-modules is set")
    names = assemble(Path(args.base), doc_path, Path(args.output))
    print(",".join(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
