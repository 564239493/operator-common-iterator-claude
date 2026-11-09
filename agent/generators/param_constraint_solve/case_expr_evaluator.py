# -*- coding: UTF-8 -*-
"""Python 侧约束评估器：生成器 per-case post-check 用真 Python 语义复核约束。

移植 `runs/<run-id>/<iter>/post_check_cases.py` 已验证可用的逻辑（它用真 `len()`/
`shape[-1]` 正确抓到 50/100 S4 伪 SAT 违反），适配消费 `InputCaseConfig` 对象
（而非 cases.json dict）。供 `ParamConstraintUtils._post_check_resolved_case` 替换
Z3 `solver.check()` 复检——后者对 `Length(SeqSort)`+`ProdShape` 递归同样不完备，
且 `_pin_case_value` 的 shape pin per-项异常仅 debug 静默跳过，抓不到 shape-rank 违例。
"""
import ast
import math
from typing import Dict, Optional, Tuple

from agent.generators.atk_common_utils.case_config import InputCaseConfig
from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.param_constraint_solve.z3_expression_solver_utils import ExpressionPreprocessor

logger = LazyLogger()

# 短名（InputCaseConfig.dtype 实际取值）→ Python 小写字面量名（constraints_in_parameters expr 用）。
# 镜像 per-run post_check_cases.py 的 DTYPE_SHORT_TO_CANON（DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP 反向），
# 但 target 改为小写 Python 形式以匹配 prompts/torch_npu_constraints_extract_v2.md §3.2
# 与 knowledge/torch_npu/operator_patterns/npu_sparse_flash_attention.md 的 dtype 约定
# （prompt/knowledge 使用 'float16'/'bfloat16'/'int32'/'double' 等小写 Python 字面量，
# 而非 ACL canonical 大写 'FLOAT16'/'BFLOAT16'）。
DTYPE_SHORT_TO_CANON = {
    "fp16": "float16", "fp32": "float32", "fp64": "float64", "bf16": "bfloat16",
    "float": "float32", "double": "double",
    "int8": "int8", "int16": "int16", "int32": "int32", "int64": "int64", "int": "int",
    "uint8": "uint8", "uint16": "uint16", "uint32": "uint32", "uint64": "uint64",
    "bool": "bool", "string": "string",
    "float8_e5m2": "float8_e5m2", "float8_e4m3fn": "float8_e4m3fn", "float8_e8m0": "float8_e8m0",
    "float4_e2m1": "float4_e2m1", "float4_e1m2": "float4_e1m2",
    "float6_e3m2": "float6_e3m2", "float6_e2m3": "float6_e2m3",
    "hifloat8": "hifloat8", "int4": "int4", "uint4": "uint4",
    "complex64": "complex64", "complex128": "complex128",
}

def _ceil_div(a, b):
    """向上取整除法（整数运算，不经过浮点）。"""
    return -((-a) // b)


def _ceil_align(a, b):
    """向上对齐到 b 的倍数。"""
    return -((-a) // b) * b


def _ceil_div_any(a, b):
    """`ceil(a / b)` 的求值实现：整数操作数走精确整数运算，其余退回真除法。

    `ceil(A / B)` 里的 `/` 在 Python 是浮点真除法，操作数超过 2^53 时相邻整数
    无法区分（`(2**53 + 1) / 1` 会变回 `2**53`），`math.ceil` 之后结果就错了。
    求值前把这种写法整体改写成本函数，与求解期用不等式组确定商的编码保持同一整数语义。
    """
    if (isinstance(a, int) and isinstance(b, int)
            and not isinstance(a, bool) and not isinstance(b, bool)):
        return -((-a) // b)
    return math.ceil(a / b)


class _CeilDivRewriter(ast.NodeTransformer):
    """把 `ceil(A / B)` / `Ceil(A / B)` 改写成 `_ceil_div_any(A, B)`。

    只改单参数且参数是 `/` 的形式；`ceil(A // B)` 的 `//` 本身是整数运算，
    不会丢精度，保持原样。嵌套调用先由 `generic_visit` 自底向上处理。
    """

    _NAMES = ("ceil", "Ceil")

    def visit_Call(self, node):
        self.generic_visit(node)
        inner = node.args[0] if len(node.args) == 1 and not node.keywords else None
        if (isinstance(node.func, ast.Name) and node.func.id in self._NAMES
                and isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Div)):
            new = ast.Call(
                func=ast.Name(id="_ceil_div_any", ctx=ast.Load()),
                args=[inner.left, inner.right],
                keywords=[],
            )
            return ast.copy_location(new, node)
        return node


SAFE_BUILTINS = {
    "len": len, "min": min, "max": max, "abs": abs, "sum": sum,
    "any": any, "all": all, "True": True, "False": False, "None": None,
    "ceil": math.ceil, "Ceil": math.ceil,
    "ceil_div": _ceil_div, "ceildiv": _ceil_div, "ceilDiv": _ceil_div,
    "CeilDiv": _ceil_div,
    "ceil_align": _ceil_align, "CeilAlign": _ceil_align, "AlignUp": _ceil_align,
    "RoundUp": _ceil_align,
    # `_CeilDivRewriter` 的改写目标（ceil(A / B) 精确求值）
    "_ceil_div_any": _ceil_div_any,
}


class Param:
    """包一个 `InputCaseConfig`，使约束 expr 能自然 eval。

    暴露 `.format`/`.dtype`/`.shape`/`.range_value`；`__eq__` 对 int/float 比 `range_value`
    （故裸名 `dstFormat in [2,29,30,32,33]`、`dstFormat == 29` 经 `__eq__` 可求值），
    同时规范形 `dstFormat.range_value == 29`/`additionalDtype.range_value in [1,27,2,36]`
    也成立（`.range_value` 暴露标量 int）。`__eq__` 仅对纯数值匹配，否则 NotImplemented 回退身份比较。
    """

    __slots__ = ("name", "format", "dtype", "shape", "range_value", "is_attr_scalar")

    def __init__(self, case_input: InputCaseConfig):
        self.name = getattr(case_input, "name", None)
        short = getattr(case_input, "dtype", None)
        # 兼容短名（表内映射到 Python 小写）与已 Python 化形式（不在表内 → lower 后回写）。
        # 不再强制 UPPERCASE：prompt/knowledge 用 'float16'/'bfloat16'，与 'FLOAT16' 不相等。
        self.dtype = DTYPE_SHORT_TO_CANON.get(short, (short.lower() if short else None))
        self.format = getattr(case_input, "format", None)
        self.shape = getattr(case_input, "shape", None)
        self.range_value = getattr(case_input, "range_values", None)
        self.is_attr_scalar = getattr(case_input, "type", None) == "attr"

    def __eq__(self, other):
        if isinstance(other, bool):
            return bool(self.range_value) == other
        if isinstance(other, (int, float)):
            return self.range_value == other
        return NotImplemented

    def __ne__(self, other):
        eq = self.__eq__(other)
        if eq is NotImplemented:
            return NotImplemented
        return not eq

    def __hash__(self):
        return id(self)

    def __repr__(self):
        return (f"<Param {self.name} dtype={self.dtype} format={self.format} "
                f"shape={self.shape} rv={self.range_value}>")


def build_namespace(case_input_map: Dict[str, InputCaseConfig]) -> Dict[str, Param]:
    """{param_name: InputCaseConfig} → {param_name: Param}。

    所有参数（含 int 标量 additionalDtype/dstFormat）一律包成 Param——同时支持裸名
    （`additionalDtype == 1`，经 `__eq__`）与规范 `.range_value` 形
    （`additionalDtype.range_value == 1`），对齐 `prompts/modules/acl_format_enum.md` §C.4
    与 generate-cases post-check 命名空间约定。不沿用 per-run 脚本把 additionalDtype
    特判成裸 int 的做法（裸 int 不支持 `.range_value`）。
    """
    return {name: Param(ci) for name, ci in case_input_map.items()}


def eval_constraint(expr: str, namespace: Dict[str, Param]) -> Tuple[Optional[bool], Optional[str]]:
    """对 resolved case 命名空间求值约束 expr 为 Python bool。

    返回 (ok, err)。ok=False=expr 显式求值假（违例）；ok=True=求值真；
    ok=None 且 err 非 None=eval 本身失败（调用方应 fail-open，不阻断）。
    先 `normalize_json_null`（裸 `null`→`None`，tokenizer 安全，不动引号内串），再
    `ast.parse(mode='eval')` 校验并把 `ceil(A / B)` 改写成精确整数求值，最后编译执行
    （沙箱 builtins 仅 len/min/max/abs/sum/any/all 与约束函数）。
    """
    if not expr:
        return True, None
    try:
        normed = ExpressionPreprocessor.normalize_json_null(expr)
    except Exception as e:  # 病态 expr tokenize 失败
        return None, f"normalize: {type(e).__name__}: {e}"
    try:
        tree = _CeilDivRewriter().visit(ast.parse(normed, mode='eval'))
        ast.fix_missing_locations(tree)
        code = compile(tree, "<constraint>", "eval")
    except SyntaxError as e:
        return None, f"ast.parse(SyntaxError): {e}"
    except Exception as e:
        return None, f"rewrite({type(e).__name__}): {e}"
    try:
        result = eval(code, {"__builtins__": SAFE_BUILTINS}, namespace)  # noqa: S307/B307 沙箱 builtins
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    if isinstance(result, bool):
        return result, None
    try:
        return bool(result), None
    except Exception as e:
        return None, f"truthy_conv: {type(e).__name__}: {e}"
