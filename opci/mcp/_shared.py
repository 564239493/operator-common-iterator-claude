"""Shared utilities for MCP tool implementations.

Contains validation logic extracted from scripts/validate_artifacts.py
and normalize logic from scripts/normalize_constraints.py.
"""

from __future__ import annotations

import ast
import io
import json
import re
import time
import tokenize
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# JSON loading helpers
# ---------------------------------------------------------------------------

def load_json(path: str | Path) -> Any:
    """Load and parse a JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path: str | Path, data: Any, indent: int = 2) -> None:
    """Write data as JSON to a file."""
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=indent) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Expression normalization (from validate_artifacts.py)
# ---------------------------------------------------------------------------

def normalize_expr_null(expr: str) -> str:
    """Normalize JSON-style bare ``null`` tokens to Python ``None``."""
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(expr).readline):
        if token.type == tokenize.NAME and token.string == "null":
            token = tokenize.TokenInfo(
                token.type, "None", token.start, token.end, token.line
            )
        tokens.append(token)
    return tokenize.untokenize(tokens)


# ---------------------------------------------------------------------------
# Constraint normalization (from normalize_constraints.py)
# ---------------------------------------------------------------------------

TENSOR_TYPES = {"aclTensor", "aclTensorList"}
ARRAY_TYPE_DTYPE_FALLBACK = {
    "aclIntArray": "int",
    "aclFloatArray": "float",
    "aclBoolArray": "bool",
}
NULL_POINTER_ONLY_RE = re.compile(
    r"(只支持传空指针|传空指针|必须为空指针|仅支持空指针)"
)
FORMAT_SEPARATOR_RE = re.compile(r"[、，,/]")


def _attribute_groups(section: Any):
    if not isinstance(section, dict):
        return
    for parameter in section.values():
        if not isinstance(parameter, dict):
            continue
        if "type" in parameter:
            yield parameter
            continue
        for attributes in parameter.values():
            if isinstance(attributes, dict):
                yield attributes


def _type_name(attributes: dict[str, Any]) -> str:
    raw_type = attributes.get("type")
    if isinstance(raw_type, dict):
        raw_type = raw_type.get("value")
    if not isinstance(raw_type, str):
        return ""
    return re.sub(r"\b(?:const|struct)\b|[*&]", "", raw_type).strip()


def _is_null_pointer_only(attributes: dict[str, Any]) -> bool:
    source_texts = [attributes.get("description", "")]
    for field_name in ("is_optional", "dtype"):
        field = attributes.get(field_name)
        if isinstance(field, dict):
            source_texts.append(field.get("src_text", ""))
    return any(
        isinstance(text, str) and NULL_POINTER_ONLY_RE.search(text)
        for text in source_texts
    )


def _normalize_tensor_format(attributes: dict[str, Any]) -> bool:
    raw_format = attributes.get("format")
    if isinstance(raw_format, dict):
        raw_value = raw_format.get("value")
        if not isinstance(raw_value, str):
            return False
        value = raw_value.strip()
        normalized = (
            []
            if not value or value.upper() == "N/A"
            else sorted({item.strip() for item in FORMAT_SEPARATOR_RE.split(value) if item.strip()})
        )
        raw_format["value"] = normalized
        return True
    if isinstance(raw_format, str):
        value = raw_format.strip()
        normalized = (
            []
            if not value or value.upper() == "N/A"
            else sorted({item.strip() for item in FORMAT_SEPARATOR_RE.split(value) if item.strip()})
        )
        attributes["format"] = {"value": normalized, "src_text": ""}
        return True
    return False


def normalize_constraints(value: dict[str, Any]) -> int:
    """Normalize type-dependent format, dimensions, and dtype values."""
    normalized_count = 0
    for section_name in ("inputs", "outputs"):
        for attributes in _attribute_groups(value.get(section_name, {})):
            type_name = _type_name(attributes)
            if type_name in TENSOR_TYPES:
                if _normalize_tensor_format(attributes):
                    normalized_count += 1
                continue

            dimensions = attributes.get("dimensions")
            if isinstance(dimensions, dict):
                if dimensions.get("value") != []:
                    dimensions["value"] = []
                    normalized_count += 1
            elif dimensions != {"value": [], "src_text": ""}:
                attributes["dimensions"] = {"value": [], "src_text": ""}
                normalized_count += 1

            dtype = attributes.get("dtype")
            dtype_value = dtype.get("value") if isinstance(dtype, dict) else dtype
            if dtype_value == [] and type_name and not _is_null_pointer_only(attributes):
                fallback = ARRAY_TYPE_DTYPE_FALLBACK.get(type_name, type_name)
                if isinstance(dtype, dict):
                    dtype["value"] = [fallback]
                else:
                    attributes["dtype"] = {"value": [fallback], "src_text": ""}
                normalized_count += 1
    return normalized_count


# ---------------------------------------------------------------------------
# Validation functions (from validate_artifacts.py)
# ---------------------------------------------------------------------------

def _iter_param_attributes(value):
    for section_name in ("inputs", "outputs"):
        section = value.get(section_name, {})
        if not isinstance(section, dict):
            continue
        for param_name, platforms in section.items():
            if not isinstance(platforms, dict):
                continue
            for platform, attributes in platforms.items():
                if isinstance(attributes, dict):
                    yield section_name, param_name, platform, attributes


def _iter_constraints(value):
    raw = value.get("constraints_in_parameters", {})
    if isinstance(raw, list):
        for index, constraint in enumerate(raw):
            if isinstance(constraint, dict):
                yield "common", index, constraint
        return
    if not isinstance(raw, dict):
        return
    for platform, constraints in raw.items():
        if not isinstance(constraints, list):
            continue
        for index, constraint in enumerate(constraints):
            if isinstance(constraint, dict):
                yield platform, index, constraint


def _walk_values(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def validate_constraints(value, _log_step: bool = False) -> list[str]:
    """Validate constraints.json structure and semantics.

    Args:
        value: parsed constraints dict
        _log_step: if True, write step-level debug logs via opci.mcp._logging
    """
    from opci.mcp._logging import log, log_elapsed

    t0 = time.monotonic()
    if _log_step:
        log("validate_constraints_internal", "start")

    if not isinstance(value, dict):
        if _log_step:
            log("validate_constraints_internal", "not_dict")
        return ["constraints must be an object"]

    if "is_single_function_mode" in value:
        if _log_step:
            log("validate_constraints_internal", "deprecated_field")
        return [
            "is_single_function_mode 已废弃，不得出现在 constraints.json；"
            "一段式判定由 function_signature 是否含 GetWorkspaceSize 隐式表达。"
        ]

    if _log_step:
        log("validate_constraints_internal", "step1_operator_rule_import")
    try:
        from opci.agent.generators.common_model_definition import OperatorRule
        if _log_step:
            log("validate_constraints_internal", "step2_operator_rule_validate")
        OperatorRule(**value)
        if _log_step:
            log_elapsed("validate_constraints_internal", "step2_done", t0)
    except Exception as exc:
        if _log_step:
            log("validate_constraints_internal", "operator_rule_failed", error=str(exc)[:200])
        return [f"OperatorRule validation failed: {exc}"]

    errors: list[str] = []

    if _log_step:
        log("validate_constraints_internal", "step3_semantic_checks")
    errors.extend(validate_constraint_semantics(value))

    if _log_step:
        log("validate_constraints_internal", "step4_array_lengths")
    errors.extend(_validate_array_lengths(value))

    if _log_step:
        log("validate_constraints_internal", "step5_tensor_formats")
    errors.extend(_validate_tensor_format_values(value))

    # Additional semantic checks would be added here
    if _log_step:
        log_elapsed("validate_constraints_internal", "done", t0, error_count=len(errors))
    return errors


def validate_constraint_semantics(value) -> list[str]:
    """Check constraint expressions for semantic issues."""
    errors: list[str] = []
    for platform, index, constraint in _iter_constraints(value):
        expr = constraint.get("expr", "")
        if not expr or not isinstance(expr, str):
            continue
        try:
            normalized = normalize_expr_null(expr)
            tree = ast.parse(normalized, mode="eval")
        except (SyntaxError, tokenize.TokenError) as exc:
            errors.append(
                f"constraints_in_parameters[{platform}][{index}].expr "
                f"is not valid after null->None normalization: {exc}"
            )
            continue
        if _is_nested_numeric_interval_membership(tree):
            errors.append(
                f"constraints_in_parameters[{platform}][{index}].expr uses "
                "'in [[min, max]]' as a numeric range; use chained "
                "inequalities such as 'min <= value <= max'"
            )
    return errors


def _is_nested_numeric_interval_membership(node: ast.AST) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Compare):
            continue
        for op, comparator in zip(item.ops, item.comparators):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if not isinstance(comparator, (ast.List, ast.Tuple)):
                continue
            for candidate in comparator.elts:
                if not isinstance(candidate, (ast.List, ast.Tuple)):
                    continue
                values = candidate.elts
                if len(values) != 2:
                    continue
                if all(
                    isinstance(v, ast.Constant)
                    and (v.value is None or isinstance(v.value, (int, float)))
                    for v in values
                ):
                    return True
    return False


def _validate_array_lengths(value) -> list[str]:
    """Reject null lengths and lossy representations."""
    errors: list[str] = []
    for section, param, platform, attributes in _iter_param_attributes(value):
        array_length = attributes.get("array_length")
        if not isinstance(array_length, dict):
            continue
        length_value = array_length.get("value")
        path = f"{section}.{param}[{platform}].array_length.value"
        if length_value is None:
            errors.append(f"{path} must not be null; use [] when unconstrained")
    return errors


def _validate_tensor_format_values(value) -> list[str]:
    """Require Tensor format domains to use a list."""
    errors: list[str] = []
    for section, param, platform, attributes in _iter_param_attributes(value):
        raw_type = attributes.get("type")
        type_name = raw_type.get("value") if isinstance(raw_type, dict) else raw_type
        if not isinstance(type_name, str):
            continue
        type_name = re.sub(r"\b(?:const|struct)\b|[*&]", "", type_name).strip()
        if type_name not in {"aclTensor", "aclTensorList"}:
            continue
        raw_format = attributes.get("format")
        format_value = raw_format.get("value") if isinstance(raw_format, dict) else raw_format
        if not isinstance(format_value, list) or not all(
            isinstance(item, str) for item in format_value
        ):
            errors.append(
                f"{section}.{param}[{platform}].format.value must be a "
                "list[str] for Tensor parameters"
            )
    return errors


def validate_cases(value) -> list[str]:
    """Validate cases.json."""
    if not isinstance(value, list):
        return ["cases must be an array"]
    if not value:
        return ["cases must not be empty"]
    return [
        f"cases[{index}] must be an object"
        for index, item in enumerate(value)
        if not isinstance(item, dict)
    ]


def validate_execution(value) -> list[str]:
    """Validate execution_result.json."""
    if not isinstance(value, dict):
        return ["execution result must be an object"]
    errors: list[str] = []
    required = ("status", "mode", "passed", "failed", "total", "records", "engine_error")
    errors.extend(f"missing field: {key}" for key in required if key not in value)
    passed = value.get("passed", 0)
    failed = value.get("failed", 0)
    total = value.get("total", 0)
    if all(isinstance(item, int) for item in (passed, failed, total)) and passed + failed != total:
        errors.append("passed + failed must equal total")
    if not isinstance(value.get("records", []), list):
        errors.append("records must be an array")
    return errors


def validate_analysis(value) -> list[str]:
    """Validate analysis.json."""
    if not isinstance(value, dict):
        return ["analysis must be an object"]
    allowed = {"constraint_extraction", "generator_bug", "executor_bug"}
    return [] if value.get("root_cause") in allowed else ["invalid root_cause"]


_EXECUTOR_DUMMY_MARKERS = (
    "_dummy_output",
    "# [FALLBACK]",
    "# TODO: CPU_GOLDEN",
)


def validate_executor(path: str) -> list[str]:
    """Validate cases_executor.py - check for dummy markers and syntax errors."""
    file_path = Path(path)
    if not file_path.is_file():
        return [f"executor file not found: {path}"]
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read executor file: {exc}"]
    errors: list[str] = []
    hits = [m for m in _EXECUTOR_DUMMY_MARKERS if m in source]
    if hits:
        errors.append(
            "CPU golden 推导未完成, 仍含 dummy 标记: "
            + ", ".join(hits)
            + " — 需先跑 atc-cpu-golden-derivation skill 替换后再执行 real"
        )
    try:
        ast.parse(source)
    except SyntaxError as exc:
        errors.append(f"executor 语法错误 (推导输出可能残缺): {exc}")
    return errors
