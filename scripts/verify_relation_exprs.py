#!/usr/bin/env python3
"""Z3 positive/negative example verification for constraints_in_parameters (advisory).

CHECK 阶段每轮由 constraint-checker 作为步骤 0 自行运行：逐平台桶处理
``constraints.json`` 的 ``constraints_in_parameters``，对每条表达式给出
「满足正例 / 违反反例」实例，并做整桶联合矛盾检测与永真检测。产物
``relation_examples.json`` 交给 constraint-checker 做正反例核对（"文档说 X，
expr 接受 {具体值}、拒绝 {具体值}，一致吗"）。

Z3 基础设施从 ``agent/generators`` 只读导入（Z3ConstraintBuilder +
ASTtoZ3Converter + ExpressionPreprocessor），不复制实现——与生成管线同一条
转换路径。转换失败通常意味着写法超出表达式语言或参数卡结构异常（如
``allowed_range_value=[null]`` 的 presence 参数无法声明），生成时同样难以
消费；逐条归因由 checker 结合 ``error`` 与 ``src_text`` 完成。

状态语义（每条约束）：
  - ``ok_with_witnesses``：expr 可满足且可违反（正常约束，附正反例）
  - ``tautology``：expr 可满足但不可违反（恒真，warning——大概率写错或冗余）
  - ``unsatisfiable``：expr 在参数卡域内不可满足（约束自相矛盾）
  - ``skipped_todo``：``# TODO:`` 前缀（沿用既有约定，保留不动）
  - ``syntax_error``：null 归一 + 关键词替换后仍非法（exit 2 阻断）
  - ``unconvertible``：AST→Z3 转换失败（如实记录，不阻断）
  - ``unknown``：Z3 求解超时/未知

整桶联合 assert unsat → ``bucket_status=contradiction``（附 unsat_core）。

exit code：出现 syntax_error → 2；其余（含 unconvertible/tautology/矛盾）→ 0
（advisory，由 checker 与人工裁决）。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent.generators.common_utils.data_handle_utils import DataHandleUtil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.generators.common_utils.expression_analysis import ExpressionPreprocessor
from agent.generators.data_definition.constants import DataMatchMap
from agent.generators.param_constraint_solve.expression_preprocess_utils import (
    ASTtoZ3Converter,
)
from agent.generators.param_constraint_solve.z3_expression_solver_utils import (
    Z3ConstraintBuilder,
)

import z3

SCHEMA_VERSION = "1.0"

# 表达式里允许出现的内置函数名（ASTtoZ3Converter 支持），不算参数
_FUNCTION_NAMES = {"len", "all", "any", "max", "min", "sum", "prod", "range"}

_STATUS_VALUES = (
    "ok_with_witnesses",
    "tautology",
    "unsatisfiable",
    "skipped_todo",
    "syntax_error",
    "unconvertible",
    "unknown",
)


def _force_utf8_stdio() -> None:
    """Windows GBK 控制台输出 JSON 时防 mojibake；只在 main() 调用。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# 参数卡 → Z3 变量声明
# ---------------------------------------------------------------------------


def _type_name(attributes: dict) -> str:
    raw = attributes.get("type")
    if isinstance(raw, dict):
        raw = raw.get("value")
    if not isinstance(raw, str):
        return ""
    return re.sub(r"\b(?:const|struct)\b|[*&]", "", raw).strip()


def _attr_value(attributes: dict, field: str):
    """取 ValueWithSrcText.value；N/A / 空列表归一为 None。"""
    raw = attributes.get(field)
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw in (None, "N/A", "N\\A"):
        return None
    if isinstance(raw, list) and not raw:
        return None
    return raw


def _dtype_list(param_type: str, attributes: dict) -> list[str] | None:
    raw = _attr_value(attributes, "dtype")
    if raw is None:
        return None
    values = raw if isinstance(raw, list) else [raw]
    mapped = [
        DataHandleUtil.data_dtype_map(param_type, str(v).strip())
        for v in values
        if isinstance(v, str)
    ]
    mapped = [m for m in mapped if m]
    return mapped or None


def _declared_dtype(attributes: dict, param_type: str, type_name: str) -> str | None:
    mapped = _dtype_list(param_type, attributes)
    if mapped:
        return mapped[0]
    return DataHandleUtil.data_dtype_map(param_type, type_name)


def _collect_param_cards(value: dict, platform: str) -> dict[str, dict]:
    """按平台桶取参数卡：优先该平台卡，缺则任取第一张（跨平台差异属 advisory）。"""
    cards: dict[str, dict] = {}
    for section in ("inputs", "outputs"):
        section_data = value.get(section, {})
        if not isinstance(section_data, dict):
            continue
        for param, platforms in section_data.items():
            if not isinstance(platforms, dict):
                continue
            if "type" in platforms:  # 扁平卡（无平台二级）
                cards[param] = platforms
                continue
            if isinstance(platforms.get(platform), dict):
                cards[param] = platforms[platform]
                continue
            for attrs in platforms.values():
                if isinstance(attrs, dict):
                    cards[param] = attrs
                    break
    return cards


def _expr_param_names(expr: str) -> set[str]:
    tree = ast.parse(expr, mode="eval")
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in _FUNCTION_NAMES
    }


def _declare_params(builder: Z3ConstraintBuilder, names, cards: dict) -> dict:
    """按参数卡声明 Z3 变量；返回 {name: {"type_hint", "dtype"}} 供报告记录。"""
    declared: dict[str, dict] = {}
    for name in sorted(names):
        attributes = cards.get(name)
        if attributes is None:
            continue
        type_name = _type_name(attributes)
        atk_type = DataMatchMap.ACL_TYPE_TRANSFER_ATK_MAP.get(type_name, "attr")
        type_hint = DataMatchMap.Z3_VAR_TYPE_MAP.get(atk_type, "scalar")
        dtype = _declared_dtype(attributes, param_type=atk_type, type_name=type_name)
        range_value = _attr_value(attributes, "allowed_range_value")
        if type_hint == "scalar" and dtype is None:
            dtype = "int64"
        try:
            if type_hint in ("tensor", "tensor_list"):
                formats = _attr_value(attributes, "format")
                builder.declare_var(
                    name,
                    type_hint=type_hint,
                    dtype=dtype,
                    allowed_dtypes=_dtype_list(param_type=atk_type, attributes=attributes),
                    allowed_formats=formats if isinstance(formats, list) else None,
                    range_value=range_value,
                )
            else:
                length_raw = _attr_value(attributes, "array_length")
                length = length_raw if isinstance(length_raw, int) else None
                builder.declare_var(
                    name,
                    type_hint=type_hint,
                    dtype=dtype,
                    range_value=range_value,
                    length=length,
                )
        except Exception:  # 声明失败按未声明处理，转换阶段会报 unconvertible
            continue
        declared[name] = {"type_hint": type_hint, "dtype": dtype}
    return declared


# ---------------------------------------------------------------------------
# 正反例提取
# ---------------------------------------------------------------------------


def _jsonify(value):
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [_jsonify(v) for v in sorted(value, key=repr)]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _witness(builder: Z3ConstraintBuilder) -> dict:
    model = builder.solver.model()
    result = {}
    for name, var_obj in builder.var_map.items():
        try:
            result[name] = _jsonify(var_obj.resolve_model(model))
        except Exception as exc:
            result[name] = {"resolve_error": str(exc)}
    return result


def _range_domain_exprs(declared: dict, cards: dict) -> dict[str, str]:
    """把参数卡 allowed_range_value 编译成 Z3 域约束表达式。

    与生成管线一致：Scalar/List 变量声明时不自动限定取值域，域由静态表达式
    （``param.range_value in [...]`` / ``min <= param.range_value <= max``）补充。
    正反例必须落在文档声明的取值域内，checker 才能对照文档核对接受/拒绝值。
    """
    exprs: dict[str, str] = {}
    for name, info in declared.items():
        if info["type_hint"] not in ("scalar", "list"):
            continue
        attributes = cards.get(name)
        if attributes is None:
            continue
        allowed = attributes.get("allowed_range_value")
        if isinstance(allowed, dict):
            value, range_type = allowed.get("value"), allowed.get("type")
        elif isinstance(allowed, list):
            value, range_type = allowed, None
        else:
            continue
        if not isinstance(value, list) or not value:
            continue
        if range_type == "range" and isinstance(value[0], list):
            parts = [
                f"({low} <= {name}.range_value <= {high})"
                for low, high in value
                if isinstance(low, (int, float)) and isinstance(high, (int, float))
            ]
            if parts:
                exprs[name] = " or ".join(parts)
            continue
        values = [
            v for v in value
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        if values:
            rendered = ", ".join(repr(v) for v in values)
            exprs[name] = f"{name}.range_value in [{rendered}]"
    return exprs


def _add_range_domains(builder: Z3ConstraintBuilder, declared: dict, cards: dict) -> None:
    for name, domain_expr in _range_domain_exprs(declared, cards).items():
        try:
            builder.add_constraint(f"domain:{name}", domain_expr)
        except Exception:
            continue


def _verify_single_expr(expr: str, cards: dict, timeout_ms: int) -> dict:
    """单条表达式：声明参数 → 补取值域 → 转换 → sat/Not(expr) 两次求解取证。"""
    builder = Z3ConstraintBuilder(timeout_ms=timeout_ms)
    names = _expr_param_names(expr)
    declared = _declare_params(builder, names, cards)
    _add_range_domains(builder, declared, cards)
    undeclared = sorted(names - set(declared))
    tree = ast.parse(expr, mode="eval")
    z3_expr = ASTtoZ3Converter(builder).visit(tree.body)
    solver = builder.solver

    solver.push()
    solver.add(z3_expr)
    sat_result = solver.check()
    satisfy = _witness(builder) if sat_result == z3.sat else None
    solver.pop()

    solver.push()
    solver.add(z3.Not(z3_expr))
    violate_result = solver.check()
    violate = _witness(builder) if violate_result == z3.sat else None
    solver.pop()

    if sat_result == z3.unsat:
        status = "unsatisfiable"
    elif sat_result == z3.unknown or violate_result == z3.unknown:
        status = "unknown"
    elif violate_result == z3.unsat:
        status = "tautology"
    else:
        status = "ok_with_witnesses"

    entry = {
        "status": status,
        "satisfy_example": satisfy,
        "violate_example": violate,
        "declared_params": declared,
    }
    if undeclared:
        entry["undeclared_params"] = undeclared
    return entry


def _verify_bucket_joint(
        platform: str,
        exprs: dict[int, str],
        cards: dict,
        timeout_ms: int,
) -> tuple[str, list[str]]:
    """整桶联合 assert：unsat → contradiction（附 unsat_core 约束名）。"""
    if not exprs:
        return "ok", []
    builder = Z3ConstraintBuilder(timeout_ms=timeout_ms)
    all_names: set[str] = set()
    for expr in exprs.values():
        try:
            all_names |= _expr_param_names(expr)
        except SyntaxError:
            continue
    declared = _declare_params(builder, all_names, cards)
    _add_range_domains(builder, declared, cards)
    named = {f"json:[{platform}][{index}]": expr for index, expr in exprs.items()}
    builder.add_constraints(named)
    result = builder.solver.check()
    if result == z3.unsat:
        core = [str(item) for item in builder.solver.unsat_core()]
        return "contradiction", core
    return "ok", []


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _iter_constraint_buckets(value: dict):
    raw = value.get("constraints_in_parameters", {})
    if isinstance(raw, list):
        yield "(default)", raw
        return
    if isinstance(raw, dict):
        for platform, constraints in raw.items():
            if isinstance(constraints, list):
                yield platform, constraints


def _ensure_logger() -> None:
    """agent.generators 的 LazyLogger 未初始化时打日志会 RuntimeError。

    幂等：已初始化（如 generate_cases 同进程）则不动；未初始化则给一个
    静默的临时目录 logger，避免校验脚本在无日志环境崩溃。
    """
    from agent.generators.common_utils.logger_util import get_logger, init_logger

    try:
        get_logger()
    except RuntimeError:
        import tempfile

        init_logger(
            log_name="verify_relation_exprs",
            log_dir=tempfile.gettempdir(),
            console_output=False,
        )


def verify_constraints(value: dict, constraints_path: Path, timeout_ms: int = 60000) -> dict:
    _ensure_logger()
    report = {
        "schema_version": SCHEMA_VERSION,
        "operator": value.get("operator_name", ""),
        "constraints_file": str(constraints_path.resolve()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeout_ms": timeout_ms,
        "counts": {status: 0 for status in _STATUS_VALUES},
        "platforms": {},
        "syntax_errors": [],
    }
    report["counts"]["bucket_contradictions"] = 0

    for platform, constraints in _iter_constraint_buckets(value):
        cards = _collect_param_cards(value, platform)
        entries: list[dict] = []
        joint_exprs: dict[int, str] = {}
        for index, constraint in enumerate(constraints):
            if not isinstance(constraint, dict):
                continue
            expr = constraint.get("expr", "")
            entry = {
                "constraint_index": index,
                "expr_type": constraint.get("expr_type", ""),
                "expr": expr,
                "relation_params": constraint.get("relation_params", []),
                "src_text": constraint.get("src_text", ""),
            }
            entries.append(entry)
            if not isinstance(expr, str) or not expr.strip():
                entry["status"] = "unconvertible"
                entry["error"] = "empty expr"
                report["counts"]["unconvertible"] += 1
                continue
            if expr.lstrip().startswith("# TODO:"):
                entry["status"] = "skipped_todo"
                report["counts"]["skipped_todo"] += 1
                continue
            normalized = ExpressionPreprocessor.normalize_json_null(expr)
            replaced = ExpressionPreprocessor.apply_keyword_replace(normalized)
            if not ExpressionPreprocessor.validate_expression(replaced):
                entry["status"] = "syntax_error"
                entry["error"] = "invalid after null->None + keyword replace"
                report["counts"]["syntax_error"] += 1
                report["syntax_errors"].append(
                    {
                        "platform": platform,
                        "constraint_index": index,
                        "expr": expr,
                    }
                )
                continue
            joint_exprs[index] = replaced
            try:
                entry.update(_verify_single_expr(replaced, cards, timeout_ms))
                report["counts"][entry["status"]] += 1
            except Exception as exc:
                entry["status"] = "unconvertible"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                report["counts"]["unconvertible"] += 1

        bucket_status, unsat_core = _verify_bucket_joint(
            platform, joint_exprs, cards, timeout_ms
        )
        if bucket_status == "contradiction":
            report["counts"]["bucket_contradictions"] += 1
        report["platforms"][platform] = {
            "bucket_status": bucket_status,
            "unsat_core": unsat_core,
            "constraints": entries,
        }

    report["counts"]["constraints"] = sum(
        len(bucket.get("constraints", []))
        for bucket in report["platforms"].values()
    )
    return report


def main() -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Verify constraints_in_parameters exprs with Z3 positive/negative examples (advisory)."
    )
    parser.add_argument("constraints", help="path to constraints.json")
    parser.add_argument(
        "--output",
        help="output relation_examples.json path (default: <constraints dir>/relation_examples.json)",
    )
    parser.add_argument("--timeout-ms", type=int, default=60000)
    args = parser.parse_args()

    constraints_path = Path(args.constraints).resolve()
    output_path = (
        Path(args.output).resolve()
        if args.output
        else constraints_path.parent / "relation_examples.json"
    )
    value = json.loads(constraints_path.read_text(encoding="utf-8"))
    report = verify_constraints(value, constraints_path, timeout_ms=args.timeout_ms)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output_path),
                "counts": report["counts"],
                "syntax_error_exit": bool(report["syntax_errors"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if report["syntax_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
