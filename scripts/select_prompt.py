#!/usr/bin/env python3
"""Select and assemble operator-class prompt modules for a given operator doc.

Deterministic classifier (the "A" mechanism): reads module manifests under
prompts/modules/, scans the operator doc for structural signals, and assembles
the base prompt + matched modules into a single snapshot file. Returns the list
of loaded module names for run_state logging.

This runs at init time, BEFORE the constraint-extractor agent reads the prompt.
The agent still reads one whole file (inputs/prompt_v1.md) — behavior unchanged;
only the file's content is now a focused subset instead of the full monolith.

Design:
- Manifests are YAML-ish frontmatter in each modules/*.md (module/description/
  triggers/depends_on). Parsed by a minimal parser (no PyYAML dependency).
- Triggers are OR-ed within a module; any match loads the module.
- depends_on is resolved transitively.
- Loaded modules are appended at the end in a fixed order; original §-headings
  are preserved so cross-references resolve by heading text whether or not the
  module is loaded (refs into unloaded modules live inside conditional checks
  that don't fire — benign dangling).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"
MODULES_DIR = PROMPTS / "modules"

# Fixed assembly order = original §-order of the sections in the monolith.
MODULE_ORDER = ["nz_matmul", "backward_partial", "format_cast", "implicit_pos", "broadcast", "acl_format_enum"]


def parse_manifest(md_text: str) -> dict:
    """Parse YAML-ish frontmatter from a module .md file. Returns {manifest, body}."""
    if not md_text.startswith("---"):
        raise ValueError("module file must start with --- frontmatter")
    end = md_text.find("\n---", 3)
    if end < 0:
        raise ValueError("module frontmatter not closed")
    fm = md_text[3:end]
    body = md_text[end + 4:].lstrip("\n")
    manifest: dict = {"triggers": [], "depends_on": []}
    cur = None
    for line in fm.splitlines():
        s = line.rstrip()
        if s.startswith("  - kind:"):
            cur = {"kind": s[len("  - kind:"):].strip()}
            manifest["triggers"].append(cur)
        elif s.startswith("    value:") and cur is not None:
            cur["value"] = _parse_value(s[len("    value:"):].strip())
        elif s.startswith("module:"):
            manifest["module"] = s[len("module:"):].strip()
        elif s.startswith("description:"):
            manifest["description"] = s[len("description:"):].strip()
        elif s.startswith("depends_on:"):
            manifest["depends_on"] = _parse_list(s[len("depends_on:"):].strip())
    if "module" not in manifest:
        raise ValueError("manifest missing module: field")
    return {"manifest": manifest, "body": body}


def _parse_value(raw: str):
    if raw.startswith("["):
        return _parse_list(raw)
    return raw.strip().strip('"')


def _parse_list(raw: str) -> list:
    return re.findall(r'"([^"]*)"', raw)


def extract_operator_name(doc_text: str) -> str:
    """Operator name = first aclnn* token (prefer a leading # heading)."""
    m = re.search(r"^#\s*(aclnn[A-Za-z0-9_]+)", doc_text, re.MULTILINE)
    if m:
        return m.group(1)
    m = re.search(r"(aclnn[A-Za-z0-9_]+)", doc_text)
    return m.group(1) if m else ""


def trigger_matches(kind: str, value, op_name: str, doc_text: str) -> bool:
    if kind == "operator_name_eq":
        return bool(op_name) and op_name == value
    if kind == "operator_name_regex":
        return bool(op_name) and bool(re.search(value, op_name))
    if kind == "name_contains":
        return bool(op_name) and value in op_name
    if kind == "doc_contains":
        return bool(re.search(value, doc_text, re.MULTILINE))
    if kind == "format_any":
        vals = value if isinstance(value, list) else [value]
        return any(str(f) in doc_text for f in vals)
    raise ValueError(f"unknown trigger kind: {kind!r}")


def classify(doc_text: str) -> list[str]:
    """Return matched module names in MODULE_ORDER."""
    op_name = extract_operator_name(doc_text)
    module_files = {p.stem: p for p in MODULES_DIR.glob("*.md")}
    if not module_files:
        return []
    parsed = {n: parse_manifest(p.read_text(encoding="utf-8")) for n, p in module_files.items()}
    loaded: set[str] = set()
    for name, item in parsed.items():
        if any(
            trigger_matches(t["kind"], t.get("value"), op_name, doc_text)
            for t in item["manifest"]["triggers"]
        ):
            loaded.add(name)
    # transitive depends_on
    changed = True
    while changed:
        changed = False
        for name in list(loaded):
            for dep in parsed[name]["manifest"]["depends_on"]:
                if dep in parsed and dep not in loaded:
                    loaded.add(dep)
                    changed = True
    return [n for n in MODULE_ORDER if n in loaded] + sorted(
        n for n in loaded if n not in MODULE_ORDER
    )


def assemble(base_path: Path, doc_path: Path, output_path: Path) -> list[str]:
    """Assemble base prompt + matched modules -> output_path. Return module names."""
    base = base_path.read_text(encoding="utf-8")
    doc_text = doc_path.read_text(encoding="utf-8")
    names = classify(doc_text)
    parts = [base.rstrip(), ""]
    if names:
        parts.append("---")
        parts.append("## 加载的算子类模块（由 scripts/select_prompt.py 按算子特征装配）")
        parts.append("")
        for name in names:
            item = parse_manifest((MODULES_DIR / f"{name}.md").read_text(encoding="utf-8"))
            parts.append(item["body"].rstrip())
            parts.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return names


def main() -> int:
    p = argparse.ArgumentParser(
        description="Assemble base prompt + matched operator-class modules for an operator doc."
    )
    p.add_argument("--base", required=True, help="base prompt path (e.g. prompts/operator_constraints_extract_v4.md)")
    p.add_argument("--doc", required=True, help="operator doc path")
    p.add_argument("--output", help="assembled snapshot output path (omit with --list-modules)")
    p.add_argument("--list-modules", action="store_true", help="just print matched module names, do not write")
    args = p.parse_args()

    doc_text = Path(args.doc).read_text(encoding="utf-8")
    if args.list_modules:
        print(",".join(classify(doc_text)))
        return 0
    if not args.output:
        p.error("--output is required unless --list-modules is set")
    names = assemble(Path(args.base), Path(args.doc), Path(args.output))
    print(",".join(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
