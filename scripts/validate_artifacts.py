#!/usr/bin/env python3
"""Validate stage artifact structure without invoking an LLM."""

from __future__ import annotations

import argparse
import ast
import hashlib
import csv
import io
import json
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_CONDITIONAL_SHAPE_SIGNAL_RE = re.compile(
    r"(?:配置|设置|设为|为|等于)\s*(?:true|false|0|1|[-+]?\d+)\s*时"
    r"[^。；;\n]*shape"
    r"|when\b[^\n.]*\b(?:true|false|0|1)\b[^\n.]*\bshape\b",
    re.IGNORECASE,
)


def _looks_like_mojibake(text: str) -> bool:
    """Detect strongly corrupted UTF-8/legacy-codepage text conservatively."""
    if "\ufffd" in text:
        return True
    if not text:
        return False
    suspicious = sum(
        "\u00c0" <= char <= "\u00ff" or char in "\u00b2\u00b3\u00b5\u00b7"
        for char in text
    )
    cjk = sum("\u3400" <= char <= "\u9fff" for char in text)
    return suspicious >= 3 and suspicious / len(text) >= 0.2 and cjk == 0


def _validate_product_support_text(value) -> list[str]:
    """Reject corrupted labels without assuming concrete platform names."""
    product_support = value.get("product_support")
    if not isinstance(product_support, list):
        return []
    errors: list[str] = []
    for index, platform in enumerate(product_support):
        if isinstance(platform, str) and _looks_like_mojibake(platform):
            errors.append(
                f"product_support[{index}] looks like mojibake: {platform!r}; "
                "preserve the platform label from the source document as UTF-8"
            )
    return errors


def load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_expr_null(expr: str) -> str:
    """Normalize JSON-style bare ``null`` tokens to Python ``None``.

    String literals containing ``"null"`` are preserved. This keeps the
    constraints JSON ergonomic while ensuring expressions remain valid Python.
    """
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(expr).readline):
        if token.type == tokenize.NAME and token.string == "null":
            token = tokenize.TokenInfo(
                token.type, "None", token.start, token.end, token.line
            )
        tokens.append(token)
    return tokenize.untokenize(tokens)


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
                    isinstance(value, ast.Constant)
                    and (
                        value.value is None
                        or (
                            isinstance(value.value, (int, float))
                            and not isinstance(value.value, bool)
                        )
                    )
                    for value in values
                ):
                    return True
    return False


def _has_unsupported_sum_comprehension(node: ast.AST) -> bool:
    """Detect aggregate forms that the Z3 expression converter cannot lower.

    ``sum(param.range_value)`` is supported.  Generator/list/set
    comprehensions inside ``sum`` are not; linear reductions should be
    rewritten algebraically, e.g. ``sum(A) - sum(B)``.
    """
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        if not isinstance(item.func, ast.Name) or item.func.id != "sum":
            continue
        if item.args and isinstance(
            item.args[0], (ast.GeneratorExp, ast.ListComp, ast.SetComp)
        ):
            return True
    return False


def validate_constraint_semantics(value) -> list[str]:
    errors: list[str] = []

    for section, param, platform, attributes in _iter_param_attributes(value):
        allowed = attributes.get("allowed_range_value")
        if not isinstance(allowed, dict):
            continue
        range_value = allowed.get("value", [])
        # value 非空时 type 必须显式标注 enum/range: 离散枚举码不标 enum 会被
        # 生成器当浮点范围填充(如 bool 填出 1.23e-40), 区间不标 range 则语义不明。
        # 提示词 v4 §4.6.3 映射表与 format_cast §4.6.11 C 示例均要求带 type,
        # 此处是 LLM 漏填时的确定性兜底: 缺失/非法即报错, 由 GATE 拦回 re-EXTRACT。
        if isinstance(range_value, list) and range_value:
            range_type = allowed.get("type")
            if range_type not in ("enum", "range"):
                errors.append(
                    f"{section}.{param}[{platform}].allowed_range_value: "
                    f"value is non-empty (len={len(range_value)}) but type is "
                    f"{range_type!r}; type must be 'enum' or 'range' when "
                    "value is non-empty (离散枚举码用 enum, 区间用 range)"
                )
        if allowed.get("type") != "range":
            continue
        if any(item is None for item in _walk_values(range_value)):
            errors.append(
                f"{section}.{param}[{platform}].allowed_range_value: "
                "type=range does not allow null boundaries; use an inequality "
                "in constraints_in_parameters. type=enum may contain null"
            )

    for platform, index, constraint in _iter_constraints(value):
        expr = constraint.get("expr", "")
        if not expr:
            continue
        if not isinstance(expr, str):
            errors.append(
                f"constraints_in_parameters[{platform}][{index}].expr "
                "must be a string"
            )
            continue
        try:
            # 通用 TODO 标记：`# TODO:` 前缀的 expr 表示该约束 Z3 求解不完备或语义需
            # 人工 channel，由 z3_expression_solver_utils.add_constraint 检测并跳过
            # solver（dropped_constraints 携带 reason="todo_skip"）。这类 expr 不应
            # 作为 Python AST 解析，免校验直接放行。
            if expr.lstrip().startswith("# TODO:"):
                continue
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
        if _has_unsupported_sum_comprehension(tree):
            errors.append(
                f"constraints_in_parameters[{platform}][{index}].expr uses "
                "sum() with a generator/comprehension, which the Z3 "
                "converter does not support; for linear reductions rewrite "
                "sum(A[i] - B[i]) as "
                "sum(A.range_value) - sum(B.range_value)"
            )
        if any(
            isinstance(item, ast.Attribute) and item.attr == "array_length"
            for item in ast.walk(tree)
        ):
            errors.append(
                f"constraints_in_parameters[{platform}][{index}].expr uses "
                "'.array_length', which is JSON metadata rather than a "
                "runtime expression attribute; use len(container)"
            )
        for item in ast.walk(tree):
            if not isinstance(item, ast.Compare):
                continue
            compare_operands = [item.left, *item.comparators]
            for left, op, right in zip(
                compare_operands, item.ops, compare_operands[1:]
            ):
                scalar_attr = next((
                    operand.attr
                    for operand in (left, right)
                    if isinstance(operand, ast.Attribute)
                    and operand.attr in {"format", "dtype"}
                ), None)
                list_operand = any(
                    isinstance(operand, (ast.List, ast.Tuple, ast.Set))
                    for operand in (left, right)
                )
                if (
                    scalar_attr and list_operand
                    and isinstance(op, (ast.Eq, ast.NotEq))
                ):
                    errors.append(
                        f"constraints_in_parameters[{platform}][{index}].expr "
                        f"compares scalar .{scalar_attr} with a list using "
                        "==/!=; compare with a string literal or use 'in [...]'"
                    )
                    break
            operands = [item.left, *item.comparators]
            has_none = any(
                isinstance(operand, ast.Constant) and operand.value is None
                for operand in operands
            )
            if has_none and any(
                isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE))
                for op in item.ops
            ):
                errors.append(
                    f"constraints_in_parameters[{platform}][{index}].expr "
                    "uses null/None as a numeric comparison boundary"
                )
                break

    return errors


def _validate_scatter_pa_kv_cache_constraints(value) -> list[str]:
    """Operator-local completeness checks; never affect other ACLNN ops."""
    if value.get("operator_name") != "aclnnScatterPaKvCache":
        return []
    errors: list[str] = []
    expected_dimensions = {
        ("inputs", "value"): [0, 3, 4],
        ("outputs", "valueCacheRef"): [0, 4, 5],
    }
    for (section, name), expected in expected_dimensions.items():
        cards = (value.get(section) or {}).get(name) or {}
        for platform, attrs in cards.items():
            if not isinstance(attrs, dict):
                continue
            raw = attrs.get("dimensions")
            actual = raw.get("value") if isinstance(raw, dict) else raw
            if actual != expected:
                errors.append(
                    f"{section}.{name}[{platform}].dimensions.value must be "
                    f"the documented discrete rank set {expected}, got {actual!r}"
                )

    positive_params = {
        "num_blocks", "block_size", "num_head", "k_head_size", "v_head_size",
    }
    for platform, relations in (value.get("constraints_in_parameters") or {}).items():
        if not isinstance(relations, list):
            continue
        exprs = [
            str(item.get("expr") or "") for item in relations
            if isinstance(item, dict)
        ]
        compact = [re.sub(r"\s+", "", expr) for expr in exprs]
        joined = "\n".join(compact)
        for param in positive_params:
            if not any(
                re.search(
                    rf"(?:{re.escape(param)}\.range_value>0|0<{re.escape(param)}\.range_value)",
                    expr,
                )
                for expr in compact
            ):
                errors.append(
                    f"constraints_in_parameters[{platform}] misses "
                    f"{param}.range_value > 0"
                )
        required_fragments = {
            'keyCacheRef.format=="FRACTAL_NZ"': "PA_NZ key cache format",
            'valueCacheRef.format=="FRACTAL_NZ"': "PA_NZ value cache format",
            'keyCacheRef.format=="ND"': "Norm key cache format",
            'valueCacheRef.format=="ND"': "Norm value cache format",
        }
        single_quote_joined = joined.replace("'", '"')
        for fragment, label in required_fragments.items():
            if fragment not in single_quote_joined:
                errors.append(
                    f"constraints_in_parameters[{platform}] misses {label}: {fragment}"
                )
        for mode in ("Alibi", "Rope", "Omni", "Nct", "NHSD"):
            if mode not in joined:
                errors.append(
                    f"constraints_in_parameters[{platform}] does not gate "
                    f"scatterModeOptional={mode} to cacheModeOptional=Norm"
                )
    return errors


def _walk_values(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def _validate_tensor_format_values(value) -> list[str]:
    """Require Tensor format domains to use a list, even for one format."""
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
        format_value = (
            raw_format.get("value") if isinstance(raw_format, dict) else raw_format
        )
        if not isinstance(format_value, list) or not all(
            isinstance(item, str) for item in format_value
        ):
            errors.append(
                f"{section}.{param}[{platform}].format.value must be a "
                "list[str] for Tensor parameters; use ['ND'] for a single format"
            )
    return errors


def _validate_conditional_shape_constraints(value) -> list[str]:
    """Require a gated shape expression when an enum/bool description says so."""
    errors: list[str] = []
    constraints_by_platform: dict[str, list[dict]] = {}
    for platform, _, constraint in _iter_constraints(value):
        constraints_by_platform.setdefault(platform, []).append(constraint)

    for section, param, platform, attributes in _iter_param_attributes(value):
        description = attributes.get("description", "")
        if not isinstance(description, str):
            continue
        if not _CONDITIONAL_SHAPE_SIGNAL_RE.search(description):
            continue

        platform_constraints = list(constraints_by_platform.get(platform, []))
        if platform != "common":
            platform_constraints.extend(constraints_by_platform.get("common", []))
        gate_ref = f"{param}.range_value"
        has_gated_shape = any(
            isinstance(constraint.get("expr"), str)
            and gate_ref in constraint["expr"]
            and ".shape" in constraint["expr"]
            and param in constraint.get("relation_params", [])
            for constraint in platform_constraints
        )
        if not has_gated_shape:
            errors.append(
                f"{section}.{param}[{platform}].description contains a "
                "conditional Shape rule, but constraints_in_parameters has "
                f"no shape expression gated by {gate_ref}"
            )
    return errors


_EXACT_LENGTH_EQUALITY_RE = re.compile(
    r"^\s*长度与\s*([A-Za-z_]\w*)\s*相同[。.]?\s*$"
)


def _validate_tensor_list_length_constraints(value) -> list[str]:
    """Ensure every explicit TensorList length-equality statement is modeled."""
    errors: list[str] = []
    constraints_by_platform: dict[str, list[dict]] = {}
    for platform, _, constraint in _iter_constraints(value):
        constraints_by_platform.setdefault(platform, []).append(constraint)

    for section, param, platform, attributes in _iter_param_attributes(value):
        raw_type = attributes.get("type")
        type_name = raw_type.get("value") if isinstance(raw_type, dict) else raw_type
        if type_name != "aclTensorList":
            continue
        array_length = attributes.get("array_length")
        if not isinstance(array_length, dict):
            continue
        src_text = array_length.get("src_text", "")
        if not isinstance(src_text, str):
            continue
        match = _EXACT_LENGTH_EQUALITY_RE.fullmatch(src_text)
        if not match:
            continue

        reference = match.group(1)
        platform_constraints = list(constraints_by_platform.get(platform, []))
        if platform != "common":
            platform_constraints.extend(constraints_by_platform.get("common", []))
        param_len_re = re.compile(rf"\blen\(\s*{re.escape(param)}\s*\)")
        reference_len_re = re.compile(
            rf"\blen\(\s*{re.escape(reference)}\s*\)"
        )
        none_guard_re = re.compile(rf"\b{re.escape(param)}\s+is\s+None\b")
        is_optional = attributes.get("is_optional")
        optional_value = (
            is_optional.get("value")
            if isinstance(is_optional, dict)
            else is_optional
        )
        has_length_constraint = False
        for constraint in platform_constraints:
            expr = constraint.get("expr")
            relation_params = constraint.get("relation_params", [])
            if not isinstance(expr, str):
                continue
            if not (
                param_len_re.search(expr)
                and reference_len_re.search(expr)
                and param in relation_params
                and reference in relation_params
            ):
                continue
            if optional_value is True and not none_guard_re.search(expr):
                continue
            has_length_constraint = True
            break

        if not has_length_constraint:
            guard_hint = (
                f"({param} is None) or " if optional_value is True else ""
            )
            errors.append(
                f"{section}.{param}[{platform}].array_length says "
                f"'长度与{reference}相同', but no matching expression was "
                f"found; expected {guard_hint}"
                f"(len({param}) == len({reference}))"
            )
    return errors


def _validate_array_lengths(value) -> list[str]:
    """Reject null lengths and lossy representations of alternative ranges."""
    errors: list[str] = []
    alternative_range_re = re.compile(
        r"\[\s*-?\d+\s*,\s*-?\d+\s*\].*"
        r"(?:或者|或是|或).*"
        r"\[\s*-?\d+\s*,\s*-?\d+\s*\]"
    )
    for section, param, platform, attributes in _iter_param_attributes(value):
        array_length = attributes.get("array_length")
        if not isinstance(array_length, dict):
            continue
        length_value = array_length.get("value")
        path = f"{section}.{param}[{platform}].array_length.value"
        if length_value is None:
            errors.append(f"{path} must not be null; use [] when unconstrained")
            continue
        # A flat integer list is a list of exact/discrete candidate lengths.
        # In particular, a fixed Python-list shape ``[1]`` is represented as
        # array_length.value=[1].  Length intervals use nested [min, max]
        # entries so they are not confused with discrete candidates.
        is_discrete_lengths = (
            isinstance(length_value, list)
            and all(isinstance(item, int) for item in length_value)
        )
        is_interval_list = (
            isinstance(length_value, list)
            and all(
                isinstance(item, list)
                and len(item) == 2
                and all(isinstance(boundary, int) for boundary in item)
                for item in length_value
            )
        )
        if not (is_discrete_lengths or is_interval_list):
            errors.append(
                f"{path} must be [], [length1,length2,...], or "
                "[[min1,max1],[min2,max2],...]"
            )
            continue
        src_text = array_length.get("src_text", "")
        if (
            isinstance(src_text, str)
            and alternative_range_re.search(src_text)
            and not (
                isinstance(length_value, list)
                and len(length_value) >= 2
                and all(
                    isinstance(item, list) and len(item) == 2
                    for item in length_value
                )
            )
        ):
            errors.append(
                f"{path} must preserve every alternative interval from "
                "src_text as [[min1,max1],[min2,max2],...]"
            )
    return errors


_DYNAMIC_VALUE_RELATION_RE = re.compile(
    r"(?:小于|大于|不超过|不小于|等于|相同|一致|依赖|根据)"
)
_EXPLICIT_NULL_RE = re.compile(
    r"(?:空指针|nullptr|未传|缺省|支持空|可为空|配置空)",
    re.IGNORECASE,
)


def _validate_dynamic_allowed_ranges(value) -> list[str]:
    """Keep cross-parameter value bounds out of allowed_range_value."""
    errors: list[str] = []
    parameter_names = set()
    for section_name in ("inputs", "outputs"):
        section = value.get(section_name, {})
        if isinstance(section, dict):
            parameter_names.update(section)

    constraints_by_platform: dict[str, list[dict]] = {}
    for platform, _, constraint in _iter_constraints(value):
        constraints_by_platform.setdefault(platform, []).append(constraint)

    for section, param, platform, attributes in _iter_param_attributes(value):
        allowed = attributes.get("allowed_range_value")
        if not isinstance(allowed, dict):
            continue
        allowed_value = allowed.get("value", [])
        src_text = allowed.get("src_text", "")
        src_text = src_text if isinstance(src_text, str) else ""
        is_optional = attributes.get("is_optional")
        optional_value = (
            is_optional.get("value")
            if isinstance(is_optional, dict)
            else is_optional
        )
        description = attributes.get("description", "")
        null_context = " ".join(
            text
            for text in (
                src_text,
                description if isinstance(description, str) else "",
                is_optional.get("src_text", "")
                if isinstance(is_optional, dict)
                else "",
            )
            if isinstance(text, str)
        )
        if (
            allowed.get("type") == "enum"
            and any(item is None for item in _walk_values(allowed_value))
            and optional_value is not True
            and not _EXPLICIT_NULL_RE.search(null_context)
        ):
            errors.append(
                f"{section}.{param}[{platform}].allowed_range_value contains "
                "null, but the parameter is required and its source text "
                "does not permit an unset/null value"
            )

        references = [
            name
            for name in parameter_names
            if name != param
            and re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
                src_text,
            )
        ]
        if not references or not _DYNAMIC_VALUE_RELATION_RE.search(src_text):
            continue
        if allowed_value != []:
            errors.append(
                f"{section}.{param}[{platform}].allowed_range_value derives "
                f"a dynamic bound from {references}; keep value=[] and "
                "express the relationship in constraints_in_parameters"
            )

        platform_constraints = list(constraints_by_platform.get(platform, []))
        if platform != "common":
            platform_constraints.extend(constraints_by_platform.get("common", []))
        for reference in references:
            has_relation = any(
                isinstance(constraint.get("expr"), str)
                and param in constraint.get("relation_params", [])
                and reference in constraint.get("relation_params", [])
                for constraint in platform_constraints
            )
            if not has_relation:
                errors.append(
                    f"{section}.{param}[{platform}].allowed_range_value source "
                    f"references {reference}, but no corresponding "
                    "constraints_in_parameters expression was found"
                )
    return errors


def validate_constraints(value) -> list[str]:
    if not isinstance(value, dict):
        return ["constraints must be an object"]
    if "is_single_function_mode" in value:
        return [
            "is_single_function_mode 已废弃，不得出现在 constraints.json；"
            "一段式判定由 function_signature 是否含 GetWorkspaceSize 隐式表达。"
        ]
    array_length_errors = _validate_array_lengths(value)
    try:
        from agent.generators.common_model_definition import OperatorRule

        OperatorRule(**value)
        errors = (
            _validate_product_support_text(value)
            + validate_constraint_semantics(value)
            + array_length_errors
            + _validate_tensor_format_values(value)
            + _validate_conditional_shape_constraints(value)
            + _validate_tensor_list_length_constraints(value)
            + _validate_dynamic_allowed_ranges(value)
            + _validate_scatter_pa_kv_cache_constraints(value)
        )
        if str(value.get("operator_name", "")).startswith(("torch_npu.", "torch.npu.")):
            from agent.hs.constraint_validation import validate_hs_constraints

            errors += validate_hs_constraints(value)
    except Exception as exc:
        return [f"OperatorRule validation failed: {exc}"]
    return errors


def validate_constraint_check(value) -> list[str]:
    """Validate the compact semantic-review report for one EXTRACT iteration."""
    if not isinstance(value, dict):
        return ["constraint_check must be an object"]

    errors: list[str] = []
    required = {
        "schema_version", "iteration", "max_rounds", "current_round",
        "status", "constraints_file", "issues", "summary",
    }
    errors.extend(
        f"constraint_check missing field: {key}"
        for key in sorted(required - set(value))
    )
    if value.get("schema_version") != "1.0":
        errors.append("constraint_check.schema_version must be '1.0'")

    iteration = value.get("iteration")
    max_rounds = value.get("max_rounds")
    current_round = value.get("current_round")
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
        errors.append("constraint_check.iteration must be a positive integer")
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 1:
        errors.append("constraint_check.max_rounds must be a positive integer")
    if not isinstance(current_round, int) or isinstance(current_round, bool) or current_round < 1:
        errors.append("constraint_check.current_round must be a positive integer")
    elif isinstance(max_rounds, int) and current_round > max_rounds:
        errors.append("constraint_check.current_round must not exceed max_rounds")

    allowed_statuses = {"passed", "needs_repair", "failed"}
    status = value.get("status")
    if status not in allowed_statuses:
        errors.append(
            "constraint_check.status must be passed, needs_repair or failed"
        )

    constraints_file = value.get("constraints_file")
    constraints_line_count: int | None = None
    if not isinstance(constraints_file, str) or not constraints_file.strip():
        errors.append("constraint_check.constraints_file must be a non-empty string")
    else:
        constraints_path = Path(constraints_file)
        if not constraints_path.is_file():
            errors.append(
                f"constraint_check.constraints_file does not exist: {constraints_file}"
            )
        else:
            try:
                constraints_line_count = len(
                    constraints_path.read_text(encoding="utf-8").splitlines()
                )
            except OSError as exc:
                errors.append(f"cannot read constraint_check.constraints_file: {exc}")

    issues = value.get("issues")
    if not isinstance(issues, list):
        errors.append("constraint_check.issues must be an array")
        issues = []

    issue_counts = {"open": 0, "fixed": 0, "unfixed": 0}
    seen_ids: set[str] = set()
    for index, issue in enumerate(issues):
        prefix = f"constraint_check.issues[{index}]"
        if not isinstance(issue, dict):
            errors.append(f"{prefix} must be an object")
            continue
        issue_id = issue.get("id")
        if not isinstance(issue_id, str) or not issue_id.strip():
            errors.append(f"{prefix}.id must be a non-empty string")
        elif issue_id in seen_ids:
            errors.append(f"{prefix}.id is duplicated: {issue_id}")
        else:
            seen_ids.add(issue_id)

        found_round = issue.get("found_round")
        last_checked_round = issue.get("last_checked_round")
        if not isinstance(found_round, int) or isinstance(found_round, bool) or found_round < 1:
            errors.append(f"{prefix}.found_round must be a positive integer")
        if (
            not isinstance(last_checked_round, int)
            or isinstance(last_checked_round, bool)
            or last_checked_round < 1
        ):
            errors.append(f"{prefix}.last_checked_round must be a positive integer")
        elif isinstance(current_round, int) and last_checked_round > current_round:
            errors.append(f"{prefix}.last_checked_round exceeds current_round")
        if (
            isinstance(found_round, int)
            and isinstance(last_checked_round, int)
            and found_round > last_checked_round
        ):
            errors.append(f"{prefix}.found_round exceeds last_checked_round")

        line = issue.get("line")
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            errors.append(f"{prefix}.line must be a positive integer")
        elif constraints_line_count is not None and line > constraints_line_count:
            errors.append(
                f"{prefix}.line {line} exceeds constraints file line count "
                f"{constraints_line_count}"
            )

        for field in ("constraint", "problem", "suggestion"):
            field_value = issue.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                errors.append(f"{prefix}.{field} must be a non-empty string")

        issue_status = issue.get("status")
        if issue_status not in issue_counts:
            errors.append(f"{prefix}.status must be open, fixed or unfixed")
        else:
            issue_counts[issue_status] += 1

    summary = value.get("summary")
    expected_summary = {"total": len(issues), **issue_counts}
    if not isinstance(summary, dict):
        errors.append("constraint_check.summary must be an object")
    else:
        for key, expected in expected_summary.items():
            actual = summary.get(key)
            if actual != expected:
                errors.append(
                    f"constraint_check.summary.{key} must be {expected}, got {actual!r}"
                )

    active_count = issue_counts["open"] + issue_counts["unfixed"]
    if status == "passed" and active_count:
        errors.append("passed constraint_check must not contain open/unfixed issues")
    if status == "needs_repair":
        if active_count == 0:
            errors.append("needs_repair constraint_check must contain an active issue")
        if (
            isinstance(current_round, int)
            and isinstance(max_rounds, int)
            and current_round >= max_rounds
        ):
            errors.append("needs_repair requires current_round < max_rounds")
    if status == "failed":
        if active_count == 0:
            errors.append("failed constraint_check must contain an active issue")
        if (
            isinstance(current_round, int)
            and isinstance(max_rounds, int)
            and current_round != max_rounds
        ):
            errors.append("failed constraint_check requires current_round == max_rounds")
    return errors


def validate_cases(value) -> list[str]:
    if not isinstance(value, list):
        return ["cases must be an array"]
    if not value:
        return ["cases must not be empty"]
    return [f"cases[{index}] must be an object" for index, item in enumerate(value) if not isinstance(item, dict)]


def validate_ttk_cases(path: str) -> list[str]:
    """Validate a TTK ACLNN or E2E CSV without CANN/NPU."""
    file_path = Path(path)
    if not file_path.is_file():
        return [f"TTK cases file not found: {path}"]
    required = {"testcase_name", "api_name", "tensor_view_shapes", "tensor_dtypes"}
    errors: list[str] = []
    try:
        with file_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = set(reader.fieldnames or [])
            missing = sorted(required - headers)
            if missing:
                errors.append("missing TTK E2E headers: " + ", ".join(missing))
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        return [f"cannot parse TTK CSV: {exc}"]
    if not rows:
        errors.append("TTK cases CSV must not be empty")
        return errors
    first_api = (rows[0].get("api_name") or "").strip()
    if first_api.startswith("aclnn"):
        from scripts.validate_ttk_aclnn_csv import validate_csv

        result = validate_csv(file_path)
        return [str(issue) for issue in result["issues"]]
    for index, row in enumerate(rows):
        api = (row.get("api_name") or "").strip()
        if not api or api.lower().startswith("aclnn"):
            errors.append(f"row {index}: TTK E2E api_name must be a non-aclnn API")
        try:
            shapes = ast.literal_eval(row.get("tensor_view_shapes") or "")
            dtypes = ast.literal_eval(row.get("tensor_dtypes") or "")
            if not isinstance(shapes, tuple) or not isinstance(dtypes, tuple):
                errors.append(f"row {index}: shapes and dtypes must be tuples")
            elif len(shapes) != len(dtypes):
                errors.append(f"row {index}: shape/dtype count mismatch")
        except (ValueError, SyntaxError) as exc:
            errors.append(f"row {index}: invalid Python-literal TTK field: {exc}")
        attrs = row.get("attributes")
        if attrs:
            try:
                if not isinstance(ast.literal_eval(attrs), dict):
                    errors.append(f"row {index}: attributes must be a dict")
            except (ValueError, SyntaxError) as exc:
                errors.append(f"row {index}: invalid attributes: {exc}")
    return errors


def validate_execution(value) -> list[str]:
    if not isinstance(value, dict):
        return ["execution result must be an object"]
    errors = []
    required = ("status", "mode", "passed", "failed", "total", "records", "engine_error")
    errors.extend(f"missing field: {key}" for key in required if key not in value)
    passed, failed, total = value.get("passed", 0), value.get("failed", 0), value.get("total", 0)
    if all(isinstance(item, int) for item in (passed, failed, total)) and passed + failed != total:
        errors.append("passed + failed must equal total")
    if not isinstance(value.get("records", []), list):
        errors.append("records must be an array")
    # fusion 策略扩展：从产物顶层取 execution_strategy（不读 run_state，产物自包含）。
    # fusion 时 fusion_phases 必填且路径门禁 dir_check_passed 全真；
    # comparison_result 仅记录性，不做必填或阈值校验（精度不入成败）。
    strategy = value.get("execution_strategy", "default")
    if strategy == "fusion":
        phases = value.get("fusion_phases")
        if not isinstance(phases, list) or not phases:
            errors.append("fusion: fusion_phases 必须是非空数组")
        else:
            for ph in phases:
                if not isinstance(ph, dict):
                    continue
                phase_name = ph.get("phase", "")
                if phase_name in ("cpu_benchmark", "npu_cascaded"):
                    if ph.get("dir_check_passed") is not True:
                        errors.append(
                            f"fusion: phase {phase_name} dir_check_passed 必须为 true "
                            f"(rank_0/rank_1 输出非空门禁)"
                        )
    fingerprints = value.get("input_artifacts")
    if isinstance(fingerprints, dict):
        for name, metadata in fingerprints.items():
            if not isinstance(metadata, dict):
                errors.append(f"input_artifacts.{name} must be an object")
                continue
            path = Path(str(metadata.get("path", "")))
            expected = str(metadata.get("sha256", ""))
            if not path.is_file():
                errors.append(
                    f"input_artifacts.{name} no longer exists: {path}"
                )
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if not expected or actual != expected:
                errors.append(
                    f"input_artifacts.{name} changed after EXECUTE; "
                    "return to GENERATE/EXECUTE before accepting this result"
                )
    return errors


def validate_analysis(value) -> list[str]:
    if not isinstance(value, dict):
        return ["analysis must be an object"]
    allowed = {
        "constraint_extraction", "generator_bug", "executor_bug",
        "ttk_adapter", "golden_derivation", "execution_environment",
    }
    return [] if value.get("root_cause") in allowed else ["invalid root_cause"]


# CPU golden 推导 (atc-cpu-golden-derivation skill) 完成后, cases_executor.py
# 里 generator.py 写入的 dummy 块必须被替换. 这里的标记 / dummy 函数若仍存在,
# 说明推导未真正执行或未生效, real 模式上传的会是 torch.ones 假参考, 精度比对
# 无意义. 该校验是质量门禁兜住 "dummy 上线" 的确定性依据.
_EXECUTOR_DUMMY_MARKERS = (
    "_dummy_output",
    "# [FALLBACK]",
    "# TODO: CPU_GOLDEN",
    "# END_CPU_GOLDEN",
)

# 通用模板生成的 CPU 类带有该字段。CPU golden skill 只允许替换 TODO 块，
# 不允许覆盖模板提供的 kwargs/args 双通道绑定和必填 tensor 诊断。
_EXECUTOR_BINDING_MARKER = "_REQUIRED_TENSOR_NAMES"
_EXECUTOR_BINDING_SNIPPETS = (
    'getattr(input_data, "kwargs"',
    'getattr(input_data, "args"',
    "missing required tensor inputs",
)


def validate_executor(path: str) -> list[str]:
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
    if _EXECUTOR_BINDING_MARKER in source:
        missing_binding = [
            snippet for snippet in _EXECUTOR_BINDING_SNIPPETS
            if snippet not in source
        ]
        if missing_binding:
            errors.append(
                "CPU golden 通用入参绑定不完整: "
                + ", ".join(missing_binding)
                + " — 推导时只能替换 CPU_GOLDEN 标记之间的占位语句，"
                "必须保留 kwargs/args 双通道绑定与必填 tensor 诊断"
            )
    try:
        ast.parse(source)
    except SyntaxError as exc:
        errors.append(f"executor 语法错误 (推导输出可能残缺): {exc}")
    return errors


def _validate_md_file(path: str) -> tuple[list[str], list[str]]:
    """校验 markdown 文件存在；空文件返回 warning 不阻断（uncertain/conflict
    可为空，supplementary 空则由补充逻辑跳过）。自由格式，不做 schema。"""
    p = Path(path)
    if not p.is_file():
        return [f"doc file not found: {path}"], []
    if not p.read_text(encoding="utf-8").strip():
        return [], [f"doc file is empty (allowed): {path}"]
    return [], []


def validate_supplementary_doc(path: str) -> tuple[list[str], list[str]]:
    return _validate_md_file(path)


def validate_uncertain_doc(path: str) -> tuple[list[str], list[str]]:
    return _validate_md_file(path)


def validate_conflict_doc(path: str) -> tuple[list[str], list[str]]:
    return _validate_md_file(path)


def validate_source_raw(value) -> list[str]:
    if not isinstance(value, dict):
        return ["source_raw must be an object"]
    errors = []
    for key in ("aclnn_interfaces", "platform_matrix", "raw_checks"):
        if key not in value:
            errors.append(f"missing field: {key}")
    return errors


def _validate_scene_scan(value: dict) -> tuple[list[str], list[str]]:
    """three-level device → 量化模板 → 特性参数.

    No "通用" group (unmarked content merged into each concrete device). The template
    name encodes the quant path and ``feature_params`` carry enum/分档 selectable params
    grouped by 特性名. Derived consistency: ``device_types`` == set of
    ``devices[].device``.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if "has_scenarios" not in value:
        return ["missing field: has_scenarios"], warnings
    has_scn = value.get("has_scenarios")
    if not isinstance(has_scn, bool):
        return ["has_scenarios must be bool"], warnings

    if not has_scn:
        for fld in ("device_types", "devices"):
            if value.get(fld):
                warnings.append(f"has_scenarios=false but {fld} non-empty")
        # scan_notes allowed even when no scenarios (e.g. quant_signal_no_template)
        return errors, warnings

    operator = value.get("operator")
    if not isinstance(operator, str) or not operator.strip():
        errors.append("operator must be a non-empty string")

    device_types = value.get("device_types")
    if not isinstance(device_types, list) or not device_types:
        errors.append("device_types must be a non-empty list[str] when has_scenarios=true")
        device_types = []
    else:
        for d in device_types:
            if not isinstance(d, str) or not d.strip():
                errors.append("device_types entries must be non-empty strings")
            elif d == "通用":
                errors.append("v3 must not contain '通用' device group (merge into concrete devices)")

    devices = value.get("devices")
    if not isinstance(devices, list) or not devices:
        errors.append("devices must be a non-empty list[dict] when has_scenarios=true")
        devices = []

    device_set = set(device_types)
    declared_devices: set[str] = set()
    any_template = False
    for i, dev in enumerate(devices):
        if not isinstance(dev, dict):
            errors.append(f"devices[{i}] must be an object")
            continue
        dname = dev.get("device")
        if not isinstance(dname, str) or not dname.strip():
            errors.append(f"devices[{i}].device must be a non-empty string")
            dname = f"__no_dev_{i}"
        else:
            declared_devices.add(dname)
        if device_types and dname not in device_set:
            errors.append(f"devices[{i}].device {dname!r} not in top-level device_types")

        templates = dev.get("templates")
        if not isinstance(templates, list) or not templates:
            errors.append(f"devices[{i}].templates must be a non-empty list[dict]")
            templates = []
        seen_tpl: set[str] = set()
        for j, t in enumerate(templates):
            if not isinstance(t, dict):
                errors.append(f"devices[{i}].templates[{j}] must be an object")
                continue
            tname = t.get("template")
            if not isinstance(tname, str) or not tname.strip():
                errors.append(f"devices[{i}].templates[{j}].template must be a non-empty string")
                tname = f"__no_tpl_{i}_{j}"
            if tname in seen_tpl:
                errors.append(f"duplicate template in device {dname!r}: {tname!r}")
            seen_tpl.add(tname)
            any_template = True
            definition = t.get("definition")
            if not isinstance(definition, str) or not definition.strip():
                errors.append(f"devices[{i}].templates[{j}].definition must be a non-empty string")
            uf = t.get("unsupported_features", [])
            if uf is None:
                uf = []
            if not isinstance(uf, list):
                errors.append(f"devices[{i}].templates[{j}].unsupported_features must be a list")
            elif not all(isinstance(x, str) and x.strip() for x in uf):
                errors.append(
                    f"devices[{i}].templates[{j}].unsupported_features entries must be non-empty strings"
                )
            fps = t.get("feature_params")
            if fps is None:
                fps = []
            if not isinstance(fps, list):
                errors.append(f"devices[{i}].templates[{j}].feature_params must be a list")
                fps = []
            for k, fp in enumerate(fps):
                if not isinstance(fp, dict):
                    errors.append(f"devices[{i}].templates[{j}].feature_params[{k}] must be an object")
                    continue
                feat = fp.get("feature")
                if not isinstance(feat, str) or not feat.strip():
                    errors.append(
                        f"devices[{i}].templates[{j}].feature_params[{k}].feature must be non-empty"
                    )
                params = fp.get("params")
                if not isinstance(params, list) or not params:
                    errors.append(
                        f"devices[{i}].templates[{j}].feature_params[{k}].params must be non-empty list"
                    )
                    params = []
                seen_param: set[str] = set()
                for m, p in enumerate(params):
                    if not isinstance(p, dict):
                        errors.append(
                            f"devices[{i}].templates[{j}].feature_params[{k}].params[{m}] must be object"
                        )
                        continue
                    pname = p.get("name")
                    if not isinstance(pname, str) or not pname.strip():
                        errors.append(
                            f"devices[{i}].templates[{j}].feature_params[{k}].params[{m}].name non-empty"
                        )
                        pname = f"__no_param_{i}_{j}_{k}_{m}"
                    if pname in seen_param:
                        warnings.append(
                            f"device {dname!r} template {tname!r} feature {feat!r}: "
                            f"duplicate param {pname!r}"
                        )
                    seen_param.add(pname)
                    pvals = p.get("values")
                    if not isinstance(pvals, list) or not pvals:
                        errors.append(
                            f"devices[{i}].templates[{j}].feature_params[{k}].params[{m}].values "
                            f"must be non-empty list"
                        )
                    elif not all(isinstance(x, (str, int, float, bool)) for x in pvals):
                        errors.append(
                            f"devices[{i}].templates[{j}].feature_params[{k}].params[{m}].values "
                            f"entries must be str/int/float/bool"
                        )
                    pdesc = p.get("description")
                    if not isinstance(pdesc, str) or not pdesc.strip():
                        errors.append(
                            f"devices[{i}].templates[{j}].feature_params[{k}].params[{m}].description "
                            f"must be non-empty"
                        )
                    for opt in ("constraint", "related"):
                        ov = p.get(opt)
                        if ov is not None and not isinstance(ov, str):
                            errors.append(
                                f"devices[{i}].templates[{j}].feature_params[{k}].params[{m}].{opt} "
                                f"must be string or null"
                            )

    # derived consistency: device_types == set of devices[].device
    if device_types and declared_devices != device_set:
        missing = device_set - declared_devices
        extra = declared_devices - device_set
        if missing:
            errors.append(f"device_types has entries with no device object: {sorted(missing)}")
        if extra:
            errors.append(f"devices has device objects not in device_types: {sorted(extra)}")

    if not any_template:
        errors.append("has_scenarios=true but no device has any template")

    notes = value.get("scan_notes")
    if notes is not None:
        if not isinstance(notes, list):
            errors.append("scan_notes must be a list")
        else:
            for j, n in enumerate(notes):
                if not isinstance(n, dict):
                    errors.append(f"scan_notes[{j}] must be an object")
                elif not str(n.get("kind", "")).strip() or not str(n.get("message", "")).strip():
                    errors.append(f"scan_notes[{j}] must have non-empty kind and message")

    return errors, warnings


def validate_scene_scan(value) -> tuple[list[str], list[str]]:
    """Validate inputs/scene_scan.json produced by the scene-scanner Agent
    (three-level device → 量化模板 → 特性参数 model)."""
    if not isinstance(value, dict):
        return ["scene_scan must be an object"], []
    return _validate_scene_scan(value)


VALIDATORS = {
    "constraints": validate_constraints,
    "constraint_check": validate_constraint_check,
    "cases": validate_cases,
    "ttk_cases": validate_ttk_cases,
    "execution": validate_execution,
    "analysis": validate_analysis,
    "executor": validate_executor,
    "supplementary_doc": validate_supplementary_doc,
    "uncertain_doc": validate_uncertain_doc,
    "conflict_doc": validate_conflict_doc,
    "source_raw": validate_source_raw,
    "scene_scan": validate_scene_scan,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=VALIDATORS)
    parser.add_argument("path")
    args = parser.parse_args()
    try:
        # executor / TTK CSV / *_doc 校验对象是文件路径, 直接传路径;
        # 其余校验对象是 JSON 产物, 先解析再传结构.
        path_kinds = {
            "executor", "ttk_cases", "supplementary_doc", "uncertain_doc",
            "conflict_doc",
        }
        if args.kind in path_kinds:
            result = VALIDATORS[args.kind](args.path)
        else:
            result = VALIDATORS[args.kind](load(args.path))
    except Exception as exc:
        result = [str(exc)]
    # 兼容二元组 (errors, warnings) 与旧式 list[str]; warnings 非阻断, 不计入 exit code
    if isinstance(result, tuple):
        errors, warnings = result
    else:
        errors, warnings = result, []
    print(json.dumps({"valid": not errors, "errors": errors, "warnings": warnings},
                     ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
