"""Safe, deterministic evaluation of extracted HS constraint expressions.

This module belongs to the torch_npu/TTK adaptation layer.  It deliberately
does not participate in the retained generic generator or its Z3 solving path;
it verifies extracted relations and final concrete cases after generation.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Mapping


_DTYPE_ALIASES = {
    "bf16": "bfloat16",
    "fp16": "float16",
    "fp32": "float32",
}
_ALLOWED_CALLS = {"len"}
_ALLOWED_ATTRIBUTES = {"shape", "dtype", "format", "range_value", "is_present"}
_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.IfExp,
    ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple, ast.Set,
    ast.Attribute, ast.Subscript, ast.Call, ast.And, ast.Or, ast.Not,
    ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.USub, ast.UAdd,
)


@dataclass(frozen=True)
class ConstraintValue:
    shape: tuple[int, ...] = ()
    dtype: str | None = None
    format: str | None = None
    range_value: Any = None
    is_present: bool = True

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, ConstraintValue):
            return (self.shape == other.shape and self.dtype == other.dtype
                    and self.format == other.format
                    and self.range_value == other.range_value
                    and self.is_present == other.is_present)
        return self.range_value == other

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash((self.shape, self.dtype, self.format,
                     self.range_value, self.is_present))

    def __len__(self) -> int:
        # 通用规则：absent 或 shape 缺失时回退空 tuple，保证 `len()`/下标访问
        # 在 audit 不抛异常；配合 `(not param.is_present) or (...)` 改写，
        # absent 路径 trivially satisfied。
        if not self.shape:
            return 0
        return self.shape[0] if len(self.shape) == 1 else len(self.shape)

    def __getitem__(self, idx):
        # absent 时 shape=()，下标会越界返回 IndexError；为兼容 audit 中
        # `param.shape[0]` 调用，捕获 IndexError 返回 -1 作为哨兵（实际
        # 不会被采纳，因为外层 `(not param.is_present) or ...` 已短路）
        try:
            return self.shape[idx]
        except IndexError:
            return -1


def _normal_dtype(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).lower()
    return _DTYPE_ALIASES.get(text, text)


def _is_absent_tensor(item: Mapping[str, Any]) -> bool:
    value = item.get("range_values")
    explicit_null = value == "null" or (
        isinstance(value, list)
        and len(value) == 1
        and value[0] in (None, "null")
    )
    shape = item.get("shape")
    return explicit_null or not (isinstance(shape, list) and shape)


def case_environment(case: Mapping[str, Any]) -> dict[str, Any]:
    """Build the expression namespace for a concrete CaseConfig payload."""
    environment: dict[str, Any] = {}
    for item in case.get("inputs", []):
        if not isinstance(item, Mapping) or not item.get("name"):
            continue
        name = str(item["name"])
        if item.get("type") == "tensor":
            if _is_absent_tensor(item):
                # absent 时仍暴露 ConstraintValue(is_present=False)，保证
                # `(not param.is_present) or ...` 类约束在 audit 中可求值；
                # 不再返回 None（旧实现下 None.is_present 会 AttributeError）。
                environment[name] = ConstraintValue(is_present=False)
                continue
            shape = item.get("shape")
            environment[name] = ConstraintValue(
                shape=tuple(shape) if isinstance(shape, list) else None,
                dtype=_normal_dtype(item.get("dtype")),
                format=item.get("format"),
                range_value=item.get("range_values"),
                is_present=bool(item.get("is_present", True)),
            )
        else:
            environment[name] = ConstraintValue(
                dtype=_normal_dtype(item.get("dtype")),
                format=item.get("format"),
                range_value=item.get("range_values"),
                is_present=bool(item.get("is_present", True)),
            )
    return environment


def _validate_tree(tree: ast.AST, known_names: set[str]) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"unsupported expression node: {type(node).__name__}")
        if isinstance(node, ast.Name):
            if node.id not in known_names and node.id not in _ALLOWED_CALLS:
                raise ValueError(f"unknown expression name: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr not in _ALLOWED_ATTRIBUTES:
                raise ValueError(f"unsupported attribute: {node.attr}")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS:
                raise ValueError("only len(...) calls are supported")
            if len(node.args) != 1 or node.keywords:
                raise ValueError("len(...) must have exactly one positional argument")


def evaluate_expression(expression: str, environment: Mapping[str, Any]) -> bool:
    """Evaluate one relation using a small AST whitelist and no builtins."""
    normalized = re.sub(r"\bnull\b", "None", expression)
    # 通用规则：把 `param is None` 改写为 `not param.is_present`，让既有
    # is None 风格约束与 absent→ConstraintValue 模型共存；`is_present` 是
    # ConstraintValue 的字段，not None 时一律 True（presence 假设），仅
    # absent 时 False。`param is not None` 同理改写为 `param.is_present`。
    # 正则采用两分支（先 `is not None` 再 `is None`），避免贪婪回溯陷阱。

    def _rewrite_is_not_none(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in environment and hasattr(environment[name], "is_present"):
            return f"{name}.is_present"
        return match.group(0)

    def _rewrite_is_none(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in environment and hasattr(environment[name], "is_present"):
            return f"(not {name}.is_present)"
        return match.group(0)

    normalized = re.sub(
        r"\b([A-Za-z_][A-Za-z_0-9]*)\s+is\s+not\s+None\b",
        _rewrite_is_not_none,
        normalized,
    )
    normalized = re.sub(
        r"\b([A-Za-z_][A-Za-z_0-9]*)\s+is\s+None\b",
        _rewrite_is_none,
        normalized,
    )
    tree = ast.parse(normalized, mode="eval")
    _validate_tree(tree, set(environment))
    result = eval(  # noqa: S307 - AST is strictly whitelisted above.
        compile(tree, "<hs-constraint>", "eval"),
        {"__builtins__": {}, "len": len},
        dict(environment),
    )
    if not isinstance(result, bool):
        raise ValueError(f"constraint did not evaluate to bool: {result!r}")
    return result


def platform_relations(
    constraints: Mapping[str, Any], platform: str | None
) -> list[dict[str, Any]]:
    raw = constraints.get("constraints_in_parameters") or {}
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, Mapping):
        return []
    if platform and isinstance(raw.get(platform), list):
        return [item for item in raw[platform] if isinstance(item, dict)]
    first = next((items for items in raw.values() if isinstance(items, list)), [])
    return [item for item in first if isinstance(item, dict)]


def evaluate_case_relations(
    case: Mapping[str, Any], constraints: Mapping[str, Any], platform: str | None
) -> list[str]:
    """Return all false or unevaluable hard relations for one concrete case."""
    environment = case_environment(case)
    # Optional parameters may be omitted from a concrete CaseConfig.  They are
    # still valid expression names and semantically evaluate as absent. 旧实现
    # 填 None 会让 `param.is_present` 在 audit 时 AttributeError；改为
    # ConstraintValue(is_present=False) 让 `(not param.is_present) or ...`
    # 类约束 trivially satisfied。
    absent_sentinel = ConstraintValue(is_present=False)
    for section in ("inputs", "outputs"):
        for name in (constraints.get(section) or {}):
            environment.setdefault(str(name), absent_sentinel)
    issues: list[str] = []
    for index, relation in enumerate(platform_relations(constraints, platform)):
        expression = str(relation.get("expr", "")).strip()
        if not expression:
            issues.append(f"constraint[{index}] has an empty expression")
            continue
        # 通用 TODO 机制：`# TODO:` 前缀的约束由 add_constraint 跳过 solver，
        # 此处审计时也直接跳过 evaluation（不作为 issue），与 solver 行为保持一致。
        # 任何 family 通用——文本前缀约定。
        if expression.startswith("# TODO:"):
            continue
        try:
            satisfied = evaluate_expression(expression, environment)
        except Exception as exc:  # deterministic diagnostic, then fail closed
            issues.append(
                f"constraint[{index}] could not be evaluated: {expression} "
                f"({type(exc).__name__}: {exc})"
            )
            continue
        if not satisfied:
            issues.append(f"constraint[{index}] is false: {expression}")
    return issues
