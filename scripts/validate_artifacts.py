#!/usr/bin/env python3
"""Validate stage artifact structure without invoking an LLM."""

from __future__ import annotations

import argparse
import ast
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
        is_single_interval = (
            isinstance(length_value, list)
            and len(length_value) == 2
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
        if not (is_single_interval or is_interval_list):
            errors.append(
                f"{path} must be [], [min,max], or "
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
            validate_constraint_semantics(value)
            + array_length_errors
            + _validate_tensor_format_values(value)
            + _validate_conditional_shape_constraints(value)
            + _validate_tensor_list_length_constraints(value)
            + _validate_dynamic_allowed_ranges(value)
        )
    except Exception as exc:
        return [f"OperatorRule validation failed: {exc}"]
    return errors


def validate_cases(value) -> list[str]:
    if not isinstance(value, list):
        return ["cases must be an array"]
    if not value:
        return ["cases must not be empty"]
    return [f"cases[{index}] must be an object" for index, item in enumerate(value) if not isinstance(item, dict)]


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
    return errors


def validate_analysis(value) -> list[str]:
    if not isinstance(value, dict):
        return ["analysis must be an object"]
    allowed = {"constraint_extraction", "generator_bug", "executor_bug"}
    return [] if value.get("root_cause") in allowed else ["invalid root_cause"]


# CPU golden 推导 (atc-cpu-golden-derivation skill) 完成后, cases_executor.py
# 里 generator.py 写入的 dummy 块必须被替换. 这里的标记 / dummy 函数若仍存在,
# 说明推导未真正执行或未生效, real 模式上传的会是 torch.ones 假参考, 精度比对
# 无意义. 该校验是质量门禁兜住 "dummy 上线" 的确定性依据.
_EXECUTOR_DUMMY_MARKERS = (
    "_dummy_output",
    "# [FALLBACK]",
    "# TODO: CPU_GOLDEN",
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


VALIDATORS = {
    "constraints": validate_constraints,
    "cases": validate_cases,
    "execution": validate_execution,
    "analysis": validate_analysis,
    "executor": validate_executor,
    "supplementary_doc": validate_supplementary_doc,
    "uncertain_doc": validate_uncertain_doc,
    "conflict_doc": validate_conflict_doc,
    "source_raw": validate_source_raw,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=VALIDATORS)
    parser.add_argument("path")
    args = parser.parse_args()
    try:
        # executor / *_doc 校验对象是文件路径(Python 源/markdown), 直接传路径;
        # 其余校验对象是 JSON 产物, 先解析再传结构.
        path_kinds = {"executor", "supplementary_doc", "uncertain_doc", "conflict_doc"}
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
