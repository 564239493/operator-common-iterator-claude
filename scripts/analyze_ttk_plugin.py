#!/usr/bin/env python3
"""Static analysis of a TTK ``--plugin`` script/directory.

The analyzer only reads Python sources; it never imports or executes the
plugin.  It reports the extra per-element values the plugin consumes from the
CSV ``attributes`` channel (typically ``kwargs["<name>_values"]``), the
metadata-slot contract, and the aux-target map, each with ``file:line``
evidence.  This is the contract the case generator/materializer uses to decide
which extra values a case must provide.

Usage (project root, .venv interpreter):
    python scripts/analyze_ttk_plugin.py --plugin <path> --output <json>
    python scripts/analyze_ttk_plugin.py --plugin <path> \
        --snapshot-dir <run>/inputs/ttk_plugin_snapshot \
        --constraints <run>/iter_001/constraints.json --output <json>

Remote absolute paths (``/data/...``) are not fetched here; snapshot them first
with ``scripts/remote_file.py get`` (files) or ``run`` + tar (directories).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VALUE_SUFFIX = "_values"
HELPER_NAMES = frozenset({"list_value", "list_values", "get_values", "read_values"})
SKIP_PARTS = frozenset({"__pycache__", ".git", "tests", "test", "examples", "example"})


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).casefold())


def _iter_py_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix == ".py" else []
    files: list[Path] = []
    for candidate in sorted(path.rglob("*.py")):
        if any(part in SKIP_PARTS for part in candidate.parts):
            continue
        files.append(candidate)
    return files


def _sha256_path(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for file in _iter_py_files(path):
        digest.update(str(file.relative_to(path)).encode("utf-8"))
        digest.update(file.read_bytes())
    return digest.hexdigest()


def _string_constant(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kwargs_value_keys(tree: ast.AST) -> list[tuple[str, int]]:
    """Find direct ``kwargs.get("x_values")`` / ``kwargs["x_values"]`` reads."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Name)
                and func.value.id == "kwargs"
                and node.args
            ):
                key = _string_constant(node.args[0])
                if key and key.endswith(VALUE_SUFFIX):
                    found.append((key, node.lineno))
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == "kwargs":
                key = _string_constant(node.slice)
                if key and key.endswith(VALUE_SUFFIX):
                    found.append((key, node.lineno))
    return found


def _value_helpers(tree: ast.AST) -> set[str]:
    """Functions whose body performs a ``*_values`` read via a kwargs param."""
    helpers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                func = inner.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get"
                    and isinstance(func.value, ast.Name)
                    and func.value.id in {arg.arg for arg in node.args.args}
                    and inner.args
                ):
                    key = _string_constant(inner.args[0])
                    if key and key.endswith(VALUE_SUFFIX):
                        helpers.add(node.name)
                        break
            if isinstance(inner, ast.Subscript):
                if (
                    isinstance(inner.value, ast.Name)
                    and inner.value.id in {arg.arg for arg in node.args.args}
                ):
                    key = _string_constant(inner.slice)
                    if key and key.endswith(VALUE_SUFFIX):
                        helpers.add(node.name)
                        break
    return helpers


def _helper_calls(tree: ast.AST, helpers: set[str]) -> list[tuple[str, int]]:
    """``list_value(kwargs, "cu_seqlens_q")`` → ``cu_seqlens_q_values``."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name not in helpers and name not in HELPER_NAMES:
            continue
        if len(node.args) < 2:
            continue
        base = _string_constant(node.args[-1])
        if base and not base.endswith(VALUE_SUFFIX):
            found.append((f"{base}{VALUE_SUFFIX}", node.lineno))
    return found


def _literal_assign(tree: ast.AST, name: str) -> Any:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                try:
                    return ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return None
    return None


def _metadata_evidence(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "metadataOptional" in node.value or "metadata_optional" in node.value:
                lines.append(node.lineno)
        if isinstance(node, ast.Tuple) and len(node.elts) == 1:
            element = node.elts[0]
            if isinstance(element, ast.UnaryOp) and isinstance(element.op, ast.USub):
                if isinstance(element.operand, ast.Constant) and element.operand.value == 1:
                    lines.append(node.lineno)
    return sorted(set(lines))


def _map_target(base: str, constraint_inputs: list[str]) -> str:
    normalized = _norm(base)
    best = ""
    for candidate in constraint_inputs:
        candidate_norm = _norm(candidate)
        if candidate_norm == normalized or candidate_norm.startswith(normalized):
            if not best or len(candidate) < len(best):
                best = candidate
    return best


def analyze(plugin: Path, constraints: dict[str, Any] | None = None) -> dict[str, Any]:
    files = _iter_py_files(plugin)
    if not files:
        raise ValueError(f"no Python sources found under {plugin}")
    constraint_inputs = []
    if isinstance(constraints, dict):
        inputs = constraints.get("inputs")
        if isinstance(inputs, dict):
            constraint_inputs = [str(name) for name in inputs]

    value_attrs: dict[str, int] = {}
    helper_keys: dict[str, int] = {}
    aux_targets: dict[str, Any] = {}
    metadata_lines: list[int] = []
    files_rel: list[str] = []

    for file in files:
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = str(file.relative_to(plugin)) if plugin.is_dir() else file.name
        files_rel.append(rel)
        for key, line in _kwargs_value_keys(tree):
            value_attrs.setdefault(key, line)
        helpers = _value_helpers(tree)
        for key, line in _helper_calls(tree, helpers):
            helper_keys.setdefault(key, line)
        targets = _literal_assign(tree, "AUX_TARGETS")
        if isinstance(targets, dict):
            aux_targets.update(targets)
        metadata_lines.extend(_metadata_evidence(tree))

    merged: dict[str, int] = {}
    for key, line in {**value_attrs, **helper_keys}.items():
        merged.setdefault(key, line)

    required = []
    for key, line in sorted(merged.items()):
        base = key[: -len(VALUE_SUFFIX)]
        required.append({
            "name": key,
            "base_name": base,
            "target_param": _map_target(base, constraint_inputs),
            "evidence": f"line {line}",
        })

    return {
        "schema_version": "1.0",
        "plugin": str(plugin),
        "plugin_sha256": _sha256_path(plugin),
        "files": files_rel,
        "required_value_attrs": required,
        "aux_targets": aux_targets,
        "metadata_slot": {
            "present": bool(metadata_lines),
            "name": "metadataOptional" if metadata_lines else "",
            "sentinel": "(-1,)" if metadata_lines else "",
            "evidence": sorted(set(metadata_lines)),
        },
        "warnings": (
            []
            if required or aux_targets or metadata_lines
            else ["no *_values / AUX_TARGETS / metadataOptional references found"]
        ),
    }


def _snapshot(plugin: Path, snapshot_dir: Path) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    target = snapshot_dir / plugin.name
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    if plugin.is_dir():
        shutil.copytree(plugin, target)
    else:
        shutil.copy2(plugin, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Static TTK plugin contract analyzer")
    parser.add_argument("--plugin", required=True, help="local plugin file or directory")
    parser.add_argument("--output", default=None, help="write JSON here (default: stdout)")
    parser.add_argument("--snapshot-dir", default=None, help="copy plugin here before analysis")
    parser.add_argument("--constraints", default=None, help="constraints.json for target mapping")
    args = parser.parse_args()

    plugin = Path(args.plugin).expanduser()
    if str(plugin).startswith("/") and not plugin.exists():
        print(json.dumps({
            "ok": False,
            "code": "PLUGIN_REMOTE_NOT_SNAPSHOTTED",
            "message": (
                "远端插件路径无法直接分析；请先用 scripts/remote_file.py 拉取到本地，"
                "再传 --plugin 本地快照路径。"
            ),
            "plugin": str(plugin),
        }, ensure_ascii=False))
        return 2
    if not plugin.exists():
        print(json.dumps({
            "ok": False,
            "code": "PLUGIN_NOT_FOUND",
            "plugin": str(plugin),
        }, ensure_ascii=False))
        return 2

    analyzed = plugin
    snapshot_path = ""
    if args.snapshot_dir:
        analyzed = _snapshot(plugin, Path(args.snapshot_dir).expanduser())
        snapshot_path = str(analyzed)

    constraints = None
    if args.constraints:
        constraints = json.loads(Path(args.constraints).read_text(encoding="utf-8"))

    payload = analyze(analyzed, constraints)
    payload["snapshot"] = snapshot_path
    payload["ok"] = True
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
