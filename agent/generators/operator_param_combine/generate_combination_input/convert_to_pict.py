"""
将本项目的 JSON 配置文件转换为 PICT 兼容的 TXT 格式。

PICT 格式规范:
    <参数.属性>: 值1, 值2, 值3, ...

    <约束表达式>;
    ...

用法:
    python convert_to_pict.py <输入.json> [--output <输出.txt>]
"""

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.generators.common_utils.logger_util import LazyLogger

logger = LazyLogger()


# ---------------------------------------------------------------------------
# Python 表达式 → PICT 约束 翻译器
# ---------------------------------------------------------------------------

class _TranslateError(Exception):
    """表示某个表达式节点无法翻译为 PICT 语法。"""


def _to_pict_name(node: ast.expr) -> str:
    """将 Python AST 名称节点转为 PICT 名称格式。

    ``x1.dtype`` → ``[x1.dtype]``
    """
    if isinstance(node, ast.Name):
        return f"[{node.id}]"
    if isinstance(node, ast.Attribute):
        parts: List[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        else:
            raise _TranslateError(f"complex attribute base: {type(cur).__name__}")
        parts.reverse()
        return "[" + ".".join(parts) + "]"
    raise _TranslateError(f"expected name, got {type(node).__name__}")


def _to_pict_const(v: Any) -> str:
    """将 Python 常量转为 PICT 字面量。"""
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    raise _TranslateError(f"unsupported constant type: {type(v).__name__}")


def _translate_expr(node: ast.expr) -> str:
    """递归翻译 AST 节点到 PICT 约束表达式。"""
    # ---------- 比较运算 ----------
    if isinstance(node, ast.Compare):
        for comp in node.comparators:
            if isinstance(comp, ast.BinOp):
                raise _TranslateError(
                    "arithmetic in comparison is not supported in PICT"
                )
        left = _translate_expr(node.left)
        parts: List[str] = []
        for op, comp in zip(node.ops, node.comparators):
            right = _translate_expr(comp)
            if isinstance(op, ast.Eq):
                parts.append(f"{left} = {right}")
            elif isinstance(op, ast.NotEq):
                parts.append(f"{left} <> {right}")
            elif isinstance(op, ast.In):
                parts.append(f"{left} IN {right}")
            elif isinstance(op, ast.NotIn):
                if isinstance(comp, (ast.List, ast.Tuple)):
                    items = [_translate_expr(e) for e in comp.elts]
                    expanded = " AND ".join(
                        f"{left} <> {item}" for item in items
                    )
                    parts.append(f"({expanded})")
                else:
                    parts.append(f"NOT ({left} IN {right})")
            elif isinstance(op, ast.Lt):
                parts.append(f"{left} < {right}")
            elif isinstance(op, ast.LtE):
                parts.append(f"{left} <= {right}")
            elif isinstance(op, ast.Gt):
                parts.append(f"{left} > {right}")
            elif isinstance(op, ast.GtE):
                parts.append(f"{left} >= {right}")
            else:
                raise _TranslateError(f"unsupported comparison: {type(op).__name__}")
            left = right
        return " AND ".join(parts) if len(parts) > 1 else parts[0]

    # ---------- 布尔运算 ----------
    if isinstance(node, ast.BoolOp):
        op_token = "AND" if isinstance(node.op, ast.And) else "OR"
        vals = [_translate_expr(v) for v in node.values]
        return f"({(' ' + op_token + ' ').join(vals)})"

    # ---------- 一元 not ----------
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = node.operand
        if (
                isinstance(operand, ast.Compare)
                and isinstance(operand.left, (ast.Name, ast.Attribute))
                and len(operand.ops) == 1
                and isinstance(operand.ops[0], ast.In)
                and len(operand.comparators) == 1
                and isinstance(operand.comparators[0], (ast.List, ast.Tuple))
        ):
            left_name = _translate_expr(operand.left)
            items = [_translate_expr(e) for e in operand.comparators[0].elts]
            return " AND ".join(f"{left_name} <> {item}" for item in items)
        return f"NOT ({_translate_expr(operand)})"

    # ---------- 二元运算 (算术) ----------
    if isinstance(node, ast.BinOp):
        left = _translate_expr(node.left)
        right = _translate_expr(node.right)
        if isinstance(node.op, ast.Mod):
            raise _TranslateError("modulo (%) is not supported in PICT")
        if isinstance(node.op, ast.Sub):
            return f"({left} - {right})"
        if isinstance(node.op, ast.Add):
            return f"({left} + {right})"
        if isinstance(node.op, ast.Mult):
            return f"({left} * {right})"
        if isinstance(node.op, ast.Div):
            return f"({left} / {right})"
        if isinstance(node.op, ast.FloorDiv):
            return f"({left} // {right})"
        raise _TranslateError(f"unsupported binary operator: {type(node.op).__name__}")

    # ---------- 名称 ----------
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _to_pict_name(node)

    # ---------- 常量 ----------
    if isinstance(node, ast.Constant):
        return _to_pict_const(node.value)

    # ---------- 列表字面量 (用于 IN 右侧) ----------
    if isinstance(node, ast.List):
        items = [_translate_expr(e) for e in node.elts]
        return "{" + ", ".join(items) + "}"

    # ---------- 函数调用 any / all / abs ----------
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name):
            if fn.id == "any" and node.args:
                elts = node.args[0].elts if isinstance(node.args[0], ast.List) else []
                if not elts:
                    raise _TranslateError("any() requires a non-empty list")
                items = [_translate_expr(e) for e in elts]
                return "(" + " OR ".join(items) + ")"
            if fn.id == "all" and node.args:
                elts = node.args[0].elts if isinstance(node.args[0], ast.List) else []
                if not elts:
                    raise _TranslateError("all() requires a non-empty list")
                items = [_translate_expr(e) for e in elts]
                return "(" + " AND ".join(items) + ")"
            if fn.id == "abs":
                raise _TranslateError("abs() is not supported in PICT")
        raise _TranslateError(f"unsupported function call: {_format_ast(fn)}")

    raise _TranslateError(f"unsupported expression node: {type(node).__name__}")


def _format_ast(node: ast.expr) -> str:
    """将 AST 节点还原为可读的字符串 (用于警告信息)。"""
    return ast.unparse(node) if hasattr(ast, "unparse") else str(node)


def translate_constraint(python_expr: str) -> str:
    """将一条 Python 约束表达式翻译为 PICT 语法。

    成功时返回以 ``;`` 结尾的 PICT 约束文本。
    失败时返回以 ``//`` 开头的注释行，包含原文和错误原因。
    """
    try:
        tree = ast.parse(python_expr.strip(), mode="eval")
        result = _translate_expr(tree.body)
        return result + " ;"
    except _TranslateError as e:
        return (
            f"// ORIGINAL: {python_expr}\n"
            f"// WARNING: Cannot translate — {e}"
        )
    except SyntaxError as e:
        return (
            f"// ORIGINAL: {python_expr}\n"
            f"// WARNING: Syntax error — {e}"
        )


# ---------------------------------------------------------------------------
# 值格式化 & 主转换逻辑
# ---------------------------------------------------------------------------

def _format_value(v: Any) -> str:
    """将单个值格式化为 PICT 文本表示。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        if " " in v or "," in v:
            return f'"{v}"'
        return v
    return str(v)


def _values_str(values: List[Any]) -> str:
    """将值列表格式化为 PICT 参数行右侧的逗号分隔字符串。"""
    return ", ".join(_format_value(v) for v in values)


def convert_json_to_pict(json_path: str, output_path: str) -> None:
    """读取 JSON 配置，转换为 PICT 格式并写入 TXT 文件。"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    lines: List[str] = []

    parameters: Dict[str, Dict[str, List[Any]]] = data.get("parameters", {})
    for param_name in sorted(parameters):
        attrs = parameters[param_name]
        for attr_name in sorted(attrs):
            values = attrs[attr_name]
            if not values:
                continue
            factor_name = f"{param_name}.{attr_name}"
            lines.append(f"{factor_name}: {_values_str(values)}")

    constraints: List[str] = data.get("constraints", [])
    if constraints:
        lines.append("")
        translated_count = 0
        failed_count = 0
        for c in constraints:
            pict = translate_constraint(c)
            lines.append(pict)
            if pict.startswith("//"):
                failed_count += 1
            else:
                translated_count += 1

    content = "\n".join(lines) + "\n"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    factor_count = sum(
        len(attrs) for attrs in parameters.values()
    )

    status_parts = [
        f"已转换: {json_path} -> {output_path}",
        f"  参数: {len(parameters)}, 因子: {factor_count}, 约束: {len(constraints)}",
    ]
    if constraints and translated_count != len(constraints):
        status_parts.append(
            f"  约束转换: {translated_count} 条成功, {failed_count} 条失败 (已转为注释)"
        )
    logger.debug("\n".join(status_parts))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将 JSON 配置转换为 PICT 兼容的 TXT 格式",
    )
    parser.add_argument("--json_input", help="输入的 JSON 配置文件路径")
    parser.add_argument(
        "--output", "-o",
        help="输出的 TXT 文件路径 (默认与输入同名，扩展名改为 .txt)",
    )
    args = parser.parse_args()

    json_path = args.json_input
    output_path = args.output or str(Path(json_path).with_suffix(".txt"))

    try:
        convert_json_to_pict(json_path, output_path)
    except FileNotFoundError:
        logger.error(f"File not find : '{json_path}'")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed : '{e}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
