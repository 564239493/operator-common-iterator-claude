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
        if not isinstance(allowed, dict) or allowed.get("type") != "range":
            continue
        range_value = allowed.get("value", [])
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


def validate_constraints(value) -> list[str]:
    if not isinstance(value, dict):
        return ["constraints must be an object"]
    try:
        from agent.generators.common_model_definition import OperatorRule

        OperatorRule(**value)
        return (
            validate_constraint_semantics(value)
            + _validate_conditional_shape_constraints(value)
            + _validate_tensor_list_length_constraints(value)
        )
    except Exception as exc:
        return [f"OperatorRule validation failed: {exc}"]


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


VALIDATORS = {
    "constraints": validate_constraints,
    "cases": validate_cases,
    "execution": validate_execution,
    "analysis": validate_analysis,
    "executor": validate_executor,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=VALIDATORS)
    parser.add_argument("path")
    args = parser.parse_args()
    try:
        # executor 校验对象是 Python 源文件, 直接传路径; 其余校验对象是
        # JSON 产物, 先解析再传结构.
        if args.kind == "executor":
            errors = VALIDATORS[args.kind](args.path)
        else:
            errors = VALIDATORS[args.kind](load(args.path))
    except Exception as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
