"""High-confidence semantic validation for extracted HS constraints."""
from __future__ import annotations

import ast
import re
from typing import Any


def _value(field: Any) -> Any:
    return field.get("value") if isinstance(field, dict) else field


def _iter_attributes(constraints: dict[str, Any]):
    for section_name in ("inputs", "outputs"):
        for name, raw in (constraints.get(section_name) or {}).items():
            if not isinstance(raw, dict):
                continue
            platforms = {"common": raw} if "type" in raw else raw
            for platform, attrs in platforms.items():
                if isinstance(attrs, dict):
                    yield section_name, name, platform, attrs


def _iter_relations(constraints: dict[str, Any]):
    raw = constraints.get("constraints_in_parameters") or {}
    groups = {"common": raw} if isinstance(raw, list) else raw
    if not isinstance(groups, dict):
        return
    for platform, items in groups.items():
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if isinstance(item, dict):
                yield platform, index, item


def _signature_names(signature: str) -> list[str]:
    start = signature.find("(")
    if start < 0:
        return []
    depth = 0
    end = -1
    quote: str | None = None
    for index in range(start, len(signature)):
        char = signature[index]
        if quote:
            if char == quote and signature[index - 1:index] != "\\":
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        return []
    body = signature[start + 1:end]
    parts: list[str] = []
    token: list[str] = []
    depth = 0
    quote = None
    for char in body + ",":
        if quote:
            token.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            token.append(char)
        elif char in "([{":
            depth += 1
            token.append(char)
        elif char in ")]}":
            depth -= 1
            token.append(char)
        elif char == "," and depth == 0:
            part = "".join(token).strip()
            token = []
            if part and part not in {"*", "/"}:
                name = part.split("=", 1)[0].split(":", 1)[0].strip()
                if re.fullmatch(r"[A-Za-z_]\w*", name):
                    parts.append(name)
        else:
            token.append(char)
    return parts


def _has_always_absent_relation(constraints: dict[str, Any], name: str) -> bool:
    pattern = re.compile(rf"\b{re.escape(name)}\s+is\s+(?:None|null)\b", re.I)
    return any(pattern.search(str(item.get("expr", ""))) for _, _, item in _iter_relations(constraints))


def _is_nonconstant_mod(node: ast.BinOp) -> bool:
    return isinstance(node.op, ast.Mod) and not (
        isinstance(node.left, ast.Constant) or isinstance(node.right, ast.Constant)
    )


def validate_hs_constraints(constraints: dict[str, Any]) -> list[str]:
    """Return blocking HS extraction errors; non-HS inputs pass through."""
    operator = str(constraints.get("operator_name", ""))
    if not operator.startswith("torch_npu."):
        return []
    errors: list[str] = []
    input_names = list((constraints.get("inputs") or {}).keys())
    signature_names = _signature_names(str(constraints.get("function_signature", "")))
    missing = [name for name in signature_names if name not in input_names]
    if missing:
        errors.append("HS signature parameters missing from inputs: " + ", ".join(missing))
    common_order = [name for name in signature_names if name in input_names]
    actual_order = [name for name in input_names if name in signature_names]
    if common_order and actual_order != common_order:
        errors.append("HS inputs are not in function-signature order")

    known_params = set(input_names) | set((constraints.get("outputs") or {}).keys())
    for section, name, platform, attrs in _iter_attributes(constraints):
        type_name = str(_value(attrs.get("type")) or "")
        if "Tensor" not in type_name:
            continue
        dimensions = _value(attrs.get("dimensions"))
        optional = _value(attrs.get("is_optional"))
        optional = optional is True or str(optional).lower() == "true"
        if dimensions in (None, [], "") and not (
            optional and _has_always_absent_relation(constraints, name)
        ):
            errors.append(
                f"{section}.{name}[{platform}]: Tensor dimensions must not be empty"
            )

    for platform, index, relation in _iter_relations(constraints):
        expr = str(relation.get("expr", ""))
        for name in relation.get("relation_params") or []:
            if name not in known_params:
                errors.append(
                    f"constraints_in_parameters[{platform}][{index}] references unknown parameter {name!r}"
                )
        try:
            tree = ast.parse(expr.replace(" null", " None"), mode="eval")
        except SyntaxError:
            continue  # The generic validator owns syntax diagnostics.
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"all", "any"}
            for node in ast.walk(tree)
        ):
            errors.append(
                f"constraints_in_parameters[{platform}][{index}] uses unbounded all/any"
            )
        if any(isinstance(node, ast.BinOp) and _is_nonconstant_mod(node) for node in ast.walk(tree)):
            errors.append(
                f"constraints_in_parameters[{platform}][{index}] uses variable modulo variable"
            )
    return errors
