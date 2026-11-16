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
        try:
            rel_parts = candidate.relative_to(path).parts
        except ValueError:
            rel_parts = candidate.parts
        if any(part in SKIP_PARTS for part in rel_parts):
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


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _kwargs_direct_reads(tree: ast.AST) -> list[tuple[str, int]]:
    """Any literal kwargs key: ``kwargs.get("k")`` / ``kwargs["k"]``."""
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
                key = _const_str(node.args[0])
                if key:
                    found.append((key, node.lineno))
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == "kwargs":
                key = _const_str(node.slice)
                if key:
                    found.append((key, node.lineno))
    return found


def _kwargs_fstring_template(node: ast.AST) -> tuple[str, str, str] | None:
    """``f"{name}_values"`` → (param, prefix, suffix); ``f"x_{name}"`` supported."""
    if not isinstance(node, ast.JoinedStr) or len(node.values) != 2:
        return None
    first, second = node.values
    if (
        isinstance(first, ast.FormattedValue)
        and isinstance(first.value, ast.Name)
        and isinstance(second, ast.Constant)
        and isinstance(second.value, str)
    ):
        return first.value.id, "", second.value
    if (
        isinstance(first, ast.Constant)
        and isinstance(first.value, str)
        and isinstance(second, ast.FormattedValue)
        and isinstance(second.value, ast.Name)
    ):
        return second.value.id, first.value, ""
    return None


def _kwargs_fstring_reads(tree: ast.AST) -> list[tuple[str, str, str, int]]:
    """Return (param, prefix, suffix, line) for f-string kwargs reads."""
    found: list[tuple[str, str, str, int]] = []
    for node in ast.walk(tree):
        key_node: ast.AST | None = None
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Name)
                and func.value.id == "kwargs"
                and node.args
            ):
                key_node = node.args[0]
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == "kwargs":
                key_node = node.slice
        if key_node is None:
            continue
        parsed = _kwargs_fstring_template(key_node)
        if parsed:
            param, prefix, suffix = parsed
            found.append((param, prefix, suffix, node.lineno))
    return found


def _function_params(tree: ast.AST) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            result[node.name] = [arg.arg for arg in node.args.args]
    return result


def _module_string_tuples(tree: ast.AST) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, (ast.Tuple, ast.List)):
            continue
        values = [_const_str(item) for item in node.value.elts]
        if not values or any(value is None for value in values):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                result[target.id] = [str(value) for value in values]
    return result


def _loop_const_targets(
    tree: ast.AST, constants: dict[str, list[str]]
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Name):
            if node.iter.id in constants:
                result.setdefault(node.target.id, []).extend(constants[node.iter.id])
            continue
        if (
            isinstance(node.target, (ast.Tuple, ast.List))
            and isinstance(node.iter, ast.Call)
            and isinstance(node.iter.func, ast.Name)
            and node.iter.func.id == "zip"
            and node.iter.args
        ):
            first = node.iter.args[0]
            names = [
                item.id for item in node.target.elts if isinstance(item, ast.Name)
            ]
            if (
                names
                and isinstance(first, ast.Name)
                and first.id in constants
            ):
                result.setdefault(names[0], []).extend(constants[first.id])
    return result


def _call_literal_args(tree: ast.AST) -> list[tuple[str, int, str, int]]:
    """Map literal call args: (callee_name, arg_index, value, line)."""
    found: list[tuple[str, int, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        for index, arg in enumerate(node.args):
            value = _const_str(arg)
            if value is not None:
                found.append((name, index, value, node.lineno))
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
    if not normalized:
        return ""
    for candidate in constraint_inputs:
        candidate_norm = _norm(candidate)
        if candidate_norm in (normalized, normalized + "optional"):
            return candidate
    return ""


def analyze(plugin: Path, constraints: dict[str, Any] | None = None) -> dict[str, Any]:
    files = _iter_py_files(plugin)
    if not files:
        raise ValueError(f"no Python sources found under {plugin}")
    constraint_inputs = []
    if isinstance(constraints, dict):
        inputs = constraints.get("inputs")
        if isinstance(inputs, dict):
            constraint_inputs = [str(name) for name in inputs]

    trees: dict[str, ast.AST] = {}
    files_rel: list[str] = []
    for file in files:
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = str(file.relative_to(plugin)) if plugin.is_dir() else file.name
        trees[rel] = tree
        files_rel.append(rel)

    helper_templates: dict[str, list[tuple[int, str, str, str, int]]] = {}
    resolved: dict[str, dict[str, Any]] = {}
    scalars: dict[str, dict[str, Any]] = {}
    aux_targets: dict[str, Any] = {}
    metadata_lines: list[int] = []

    for rel, tree in trees.items():
        params = _function_params(tree)
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef):
                continue
            plist = params.get(func.name, [])
            for param, prefix, suffix, line in _kwargs_fstring_reads(func):
                if param in plist:
                    helper_templates.setdefault(func.name, []).append(
                        (plist.index(param), prefix, suffix, rel, line)
                    )
        constants = _module_string_tuples(tree)
        loops = _loop_const_targets(tree, constants)
        for key, line in _kwargs_direct_reads(tree):
            meta = {
                "kind": "direct",
                "file": rel,
                "line": line,
                "template": key,
                "arg": key,
            }
            if key.endswith(VALUE_SUFFIX):
                resolved.setdefault(key, meta)
            else:
                scalars.setdefault(key, meta)
        for param, prefix, suffix, line in _kwargs_fstring_reads(tree):
            for base in loops.get(param, []):
                key = f"{prefix}{base}{suffix}"
                resolved.setdefault(key, {
                    "kind": "constant_tuple",
                    "file": rel,
                    "line": line,
                    "template": f"{prefix}{{base}}{suffix}",
                    "arg": base,
                })
        targets = _literal_assign(tree, "AUX_TARGETS")
        if isinstance(targets, dict):
            aux_targets.update(targets)
        metadata_lines.extend(_metadata_evidence(tree))

    for rel, tree in trees.items():
        for callee, index, value, line in _call_literal_args(tree):
            for pos, prefix, suffix, hrel, hline in helper_templates.get(callee, []):
                if index != pos:
                    continue
                key = f"{prefix}{value}{suffix}"
                resolved.setdefault(key, {
                    "kind": "helper_arg",
                    "file": hrel,
                    "line": hline,
                    "call_file": rel,
                    "call_line": line,
                    "template": f"{prefix}{{base}}{suffix}",
                    "arg": value,
                })

    for rel, tree in trees.items():
        for key, line in _kwargs_value_keys(tree):
            resolved.setdefault(key, {
                "kind": "legacy_direct", "file": rel, "line": line,
            })

    required = []
    for key, meta in sorted(resolved.items()):
        if key.startswith("pytest_"):
            continue
        base = key[: -len(VALUE_SUFFIX)] if key.endswith(VALUE_SUFFIX) else key
        target = aux_targets.get(key) or _map_target(base, constraint_inputs)
        confidence = "declared" if key in aux_targets else "derived"
        if not target and meta.get("kind") not in ("helper_arg", "constant_tuple"):
            confidence = "derived"
        required.append({
            "name": key,
            "base_name": base,
            "target_param": target,
            "channel": "csv_attributes",
            "resolution": meta,
            "evidence": f"{meta.get('file', '?')}:{meta.get('line', '?')}",
            "confidence": confidence,
        })

    attributes = []
    internal = {
        "kwargs", "testcase_name", "expect_error", "batch_axis",
        "batch_slice_info", "batch_seed", "template_mode",
    }
    for key, meta in sorted(scalars.items()):
        if key in internal or key.startswith("pytest_"):
            continue
        attributes.append({
            "name": key,
            "target_param": _map_target(key, constraint_inputs),
            "channel": "csv_attributes",
            "kind": "scalar",
            "resolution": meta,
            "evidence": f"{meta.get('file', '?')}:{meta.get('line', '?')}",
            "confidence": "derived",
        })

    return {
        "schema_version": "1.1",
        "plugin": str(plugin),
        "plugin_sha256": _sha256_path(plugin),
        "files": files_rel,
        "required_value_attrs": required,
        "required_attributes": attributes,
        "aux_targets": aux_targets,
        "metadata_slot": {
            "present": bool(metadata_lines),
            "name": "metadataOptional" if metadata_lines else "",
            "sentinel": "(-1,)" if metadata_lines else "",
            "evidence": sorted(set(metadata_lines)),
        },
        "warnings": (
            []
            if required or attributes or aux_targets or metadata_lines
            else ["no value/attribute/AUX_TARGETS/metadataOptional references found"]
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
