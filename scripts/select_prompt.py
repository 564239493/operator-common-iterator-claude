#!/usr/bin/env python3
"""Freeze ACLNN base + routed knowledge and write an auditable assembly record."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.route_aclnn_knowledge import DEFAULT_KNOWLEDGE, render_bundle, route
except ModuleNotFoundError:
    from route_aclnn_knowledge import DEFAULT_KNOWLEDGE, render_bundle, route


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = ROOT / "prompts" / "operator_constraints" / "base.md"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def assemble(
    base_path: Path,
    doc_path: Path,
    output_path: Path,
    knowledge_path: Path = DEFAULT_KNOWLEDGE,
    record_path: Path | None = None,
    preanalysis_path: Path | None = None,
    source_analysis_knowledge: bool = False,
) -> list[str]:
    base_path, doc_path, output_path = base_path.resolve(), doc_path.resolve(), output_path.resolve()
    knowledge_path = knowledge_path.resolve()
    result = route(
        doc_path,
        knowledge_path,
        source_analysis_knowledge=source_analysis_knowledge,
    )
    base = base_path.read_text(encoding="utf-8").rstrip()
    bundle = render_bundle(result, knowledge_path).rstrip()
    snapshot = "\n".join([
        base, "", "---", "", "<!-- assembled-knowledge-begin -->",
        bundle, "<!-- assembled-knowledge-end -->", "",
    ])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(snapshot, encoding="utf-8")
    if preanalysis_path is not None:
        _write_json(preanalysis_path.resolve(), result["preanalysis"])
    if record_path is not None:
        manifest_path = knowledge_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        by_id = {item["id"]: item for item in manifest["modules"]}
        decisions = {item["module_id"]: item for item in result["applicability"]["selected_modules"]}
        components = []
        for order, module_id in enumerate(result["resolved_modules"], 1):
            path = knowledge_path / by_id[module_id]["path"]
            components.append({
                "order": order,
                "module_id": module_id,
                "scope": by_id[module_id]["scope"],
                "path": str(path.resolve()),
                "sha256": _sha256(path),
                "selection_decision": decisions[module_id],
            })
        record = {
            "schema_version": "1.0",
            "assembled_at": datetime.now(timezone.utc).isoformat(),
            "mode": "aclnn_routed_knowledge",
            "family": "aclnn",
            "frozen": True,
            "source_document": {"path": str(doc_path), "sha256": _sha256(doc_path)},
            "preanalysis_artifact": {
                "path": str(preanalysis_path.resolve()) if preanalysis_path else "",
                "sha256": _sha256(preanalysis_path.resolve()) if preanalysis_path else "",
            },
            "base_prompt": {"path": str(base_path), "sha256": _sha256(base_path)},
            "knowledge": {
                "root": str(knowledge_path),
                "manifest_path": str(manifest_path.resolve()),
                "manifest_sha256": _sha256(manifest_path),
                "route_source": result["route_source"],
                "feature_flags": result["applicability"].get("feature_flags", {}),
            },
            "applicability": result["applicability"],
            "assembly": {
                "order": ["base_prompt", "knowledge_modules"],
                "module_ids": result["resolved_modules"],
                "knowledge_components": components,
                "output_path": str(output_path),
                "output_sha256": _sha256(output_path),
                "boundary_markers": ["assembled-knowledge-begin", "assembled-knowledge-end"],
            },
        }
        _write_json(record_path.resolve(), record)
    return result["resolved_modules"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble ACLNN base + routed knowledge.")
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    parser.add_argument("--doc", required=True)
    parser.add_argument("--knowledge", default=str(DEFAULT_KNOWLEDGE))
    parser.add_argument("--output")
    parser.add_argument("--record")
    parser.add_argument("--preanalysis-output")
    parser.add_argument("--list-modules", action="store_true")
    parser.add_argument(
        "--source-analysis-knowledge",
        action="store_true",
        help="显式启用按算子名精准命中的源码分析约束知识；默认关闭。",
    )
    args = parser.parse_args()
    doc, knowledge = Path(args.doc).resolve(), Path(args.knowledge).resolve()
    if args.list_modules:
        print(",".join(route(
            doc,
            knowledge,
            source_analysis_knowledge=args.source_analysis_knowledge,
        )["resolved_modules"]))
        return 0
    if not args.output:
        parser.error("--output is required unless --list-modules is set")
    names = assemble(
        Path(args.base), doc, Path(args.output), knowledge,
        Path(args.record) if args.record else None,
        Path(args.preanalysis_output) if args.preanalysis_output else None,
        source_analysis_knowledge=args.source_analysis_knowledge,
    )
    print(",".join(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
