"""High-confidence semantic validation for extracted HS constraints."""
from __future__ import annotations

import ast
import re
from typing import Any

from .constraint_evaluator import (
    ConstraintValue,
    evaluate_case_relations,
    evaluate_expression,
)


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


def _tensor(name: str, dtype: str, shape: list[int] | None, values: Any) -> dict[str, Any]:
    return {
        "name": name, "type": "tensor", "dtype": dtype, "shape": shape,
        "format": "ND", "range_values": values,
    }


def _attr(name: str, value: Any) -> dict[str, Any]:
    return {"name": name, "type": "attr", "dtype": "", "range_values": value}


def _kv_quant_truth_cases() -> dict[str, dict[str, Any]]:
    """Known-valid document scenes used to catch inverted layout implications."""
    common_attrs = [
        _attr("scale_value", 0.044194173824159216),
        _attr("key_quant_mode", 2), _attr("value_quant_mode", 2),
        _attr("sparse_block_size", 2), _attr("sparse_mode", 3),
        _attr("pre_tokens", (1 << 63) - 1),
        _attr("next_tokens", (1 << 63) - 1),
        _attr("attention_mode", 2), _attr("quant_scale_repo_mode", 1),
        _attr("tile_size", 128), _attr("rope_head_dim", 64),
    ]
    reserved = [
        _tensor("key_dequant_scale", "float16", None, ["null"]),
        _tensor("value_dequant_scale", "float16", None, ["null"]),
    ]
    bsnd_query = _tensor("query", "float16", [2, 3, 4, 576], [-1, 1])
    cases = {
        "bsnd": {
            "inputs": [
                bsnd_query,
                _tensor("key", "int8", [2, 8, 1, 656], [-8, 8]),
                _tensor("value", "int8", [2, 8, 1, 656], [-8, 8]),
                _tensor("sparse_indices", "int32", [2, 3, 1, 2], [0, 0]),
                *reserved,
                _tensor("block_table", "int32", None, ["null"]),
                _tensor("actual_seq_lengths_query", "int32", None, ["null"]),
                _tensor("actual_seq_lengths_kv", "int32", None, ["null"]),
                _attr("layout_query", "BSND"), _attr("layout_kv", "BSND"),
                *common_attrs,
                _tensor("out", "float16", [2, 3, 4, 576], [-1, 1]),
            ]
        },
        "tnd_multibatch": {
            "inputs": [
                _tensor("query", "float16", [6, 4, 576], [-1, 1]),
                _tensor("key", "int8", [10, 1, 656], [-8, 8]),
                _tensor("value", "int8", [10, 1, 656], [-8, 8]),
                _tensor("sparse_indices", "int32", [6, 1, 2], [0, 0]),
                *reserved,
                _tensor("block_table", "int32", None, ["null"]),
                _tensor("actual_seq_lengths_query", "int32", [2], [3, 6]),
                _tensor("actual_seq_lengths_kv", "int32", [2], [4, 10]),
                _attr("layout_query", "TND"), _attr("layout_kv", "TND"),
                *common_attrs,
                _tensor("out", "float16", [6, 4, 576], [-1, 1]),
            ]
        },
        "paged_attention": {
            "inputs": [
                bsnd_query,
                _tensor("key", "int8", [4, 16, 1, 656], [-8, 8]),
                _tensor("value", "int8", [4, 16, 1, 656], [-8, 8]),
                _tensor("sparse_indices", "int32", [2, 3, 1, 2], [0, 0]),
                *reserved,
                _tensor("block_table", "int32", [2, 4], [0, 3]),
                _tensor("actual_seq_lengths_query", "int32", [2], [3, 3]),
                _tensor("actual_seq_lengths_kv", "int32", [2], [32, 64]),
                _attr("layout_query", "BSND"), _attr("layout_kv", "PA_BSND"),
                *common_attrs,
                _tensor("out", "float16", [2, 3, 4, 576], [-1, 1]),
            ]
        },
    }
    return cases


def _validate_kv_quant_truth_table(constraints: dict[str, Any]) -> list[str]:
    required = {
        "query", "key", "value", "sparse_indices", "block_table",
        "actual_seq_lengths_query", "actual_seq_lengths_kv",
    }
    if not required.issubset(set((constraints.get("inputs") or {}).keys())):
        return []
    raw = constraints.get("constraints_in_parameters") or {}
    platforms = list(raw) if isinstance(raw, dict) else [None]
    errors: list[str] = []
    for platform in platforms:
        for scene, case in _kv_quant_truth_cases().items():
            for issue in evaluate_case_relations(case, constraints, platform):
                errors.append(f"{platform or 'common'} truth-table {scene}: {issue}")
    return errors


def _validate_lightning_indexer_truth_table(
    constraints: dict[str, Any],
) -> list[str]:
    """Catch inverted presence implications before case generation.

    The operator contract is deliberately checked as a truth table.  Merely
    finding an expression mentioning layout_key/block_table is insufficient:
    the 2026-07-22 regression had the right names and src_text but constrained
    the opposite branch.
    """
    if constraints.get("operator_name") != "torch_npu.npu_lightning_indexer":
        return []
    raw = constraints.get("constraints_in_parameters") or {}
    groups = {"common": raw} if isinstance(raw, list) else raw
    if not isinstance(groups, dict):
        return []
    errors: list[str] = []
    for platform, items in groups.items():
        if not isinstance(items, list):
            continue
        presence_exprs = [
            str(item.get("expr", ""))
            for item in items
            if isinstance(item, dict)
            and item.get("expr_type") == "presence_dependency"
            and {"layout_key", "block_table"}.issubset(
                set(item.get("relation_params") or [])
            )
        ]
        for layout, present, expected in (
            ("PA_BSND", True, True),
            ("PA_BSND", False, False),
            ("BSND", True, False),
            ("BSND", False, True),
            ("TND", True, False),
            ("TND", False, True),
        ):
            environment = {
                "layout_key": ConstraintValue(range_value=layout),
                "block_table": ConstraintValue(is_present=present),
            }
            try:
                actual = bool(presence_exprs) and all(
                    evaluate_expression(expr, environment)
                    for expr in presence_exprs
                )
            except Exception as exc:
                errors.append(
                    f"{platform} lightning_indexer block_table truth-table "
                    f"could not evaluate: {type(exc).__name__}: {exc}"
                )
                break
            if actual != expected:
                state = "present" if present else "absent"
                errors.append(
                    f"{platform} lightning_indexer block_table truth-table "
                    f"failed for layout_key={layout}, block_table={state}: "
                    f"expected {expected}, got {actual}"
                )

        key_align_exprs = [
            str(item.get("expr", ""))
            for item in items
            if isinstance(item, dict)
            and {"layout_query", "query", "actual_seq_lengths_key"}.issubset(
                set(item.get("relation_params") or [])
            )
        ]
        mismatch_environment = {
            "layout_query": ConstraintValue(range_value="BSND"),
            "query": ConstraintValue(shape=(1, 2, 3, 128)),
            "actual_seq_lengths_key": ConstraintValue(shape=(7,)),
        }
        try:
            rejects_mismatch = bool(key_align_exprs) and not all(
                evaluate_expression(expr, mismatch_environment)
                for expr in key_align_exprs
            )
        except Exception as exc:
            errors.append(
                f"{platform} lightning_indexer actual_seq_lengths_key "
                f"truth-table could not evaluate: {type(exc).__name__}: {exc}"
            )
            continue
        if not rejects_mismatch:
            errors.append(
                f"{platform} lightning_indexer must reject BSND "
                "actual_seq_lengths_key.shape[0] != query.shape[0]"
            )

        tnd_align_exprs = [
            str(item.get("expr", ""))
            for item in items
            if isinstance(item, dict)
            and {
                "layout_query", "layout_key",
                "actual_seq_lengths_query", "actual_seq_lengths_key",
            }.issubset(set(item.get("relation_params") or []))
        ]
        tnd_mismatch_environment = {
            "layout_query": ConstraintValue(range_value="TND"),
            "layout_key": ConstraintValue(range_value="TND"),
            "actual_seq_lengths_query": ConstraintValue(shape=(2,)),
            "actual_seq_lengths_key": ConstraintValue(shape=(1,)),
        }
        try:
            rejects_tnd_mismatch = bool(tnd_align_exprs) and not all(
                evaluate_expression(expr, tnd_mismatch_environment)
                for expr in tnd_align_exprs
            )
        except Exception as exc:
            errors.append(
                f"{platform} lightning_indexer TND actual sequence "
                f"truth-table could not evaluate: {type(exc).__name__}: {exc}"
            )
            continue
        if not rejects_tnd_mismatch:
            errors.append(
                f"{platform} lightning_indexer must reject TND "
                "actual_seq_lengths_query.shape[0] != "
                "actual_seq_lengths_key.shape[0]"
            )
    return errors


def validate_hs_constraints(constraints: dict[str, Any]) -> list[str]:
    """Return blocking HS extraction errors; non-HS inputs pass through."""
    operator = str(constraints.get("operator_name", ""))
    if not operator.startswith(("torch_npu.", "torch.npu.")):
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
    if operator == "torch_npu.npu_kv_quant_sparse_flash_attention":
        errors.extend(_validate_kv_quant_truth_table(constraints))
    elif operator == "torch_npu.npu_lightning_indexer":
        errors.extend(_validate_lightning_indexer_truth_table(constraints))
    return errors
