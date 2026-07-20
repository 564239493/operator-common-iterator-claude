# -*- coding: UTF-8 -*-
"""Python 侧约束评估器：生成器 per-case post-check 用真 Python 语义复核约束。

移植 `runs/<run-id>/<iter>/post_check_cases.py` 已验证可用的逻辑（它用真 `len()`/
`shape[-1]` 正确抓到 50/100 S4 伪 SAT 违反），适配消费 `InputCaseConfig` 对象
（而非 cases.json dict）。供 `ParamConstraintUtils._post_check_resolved_case` 替换
Z3 `solver.check()` 复检——后者对 `Length(SeqSort)`+`ProdShape` 递归同样不完备，
且 `_pin_case_value` 的 shape pin per-项异常仅 debug 静默跳过，抓不到 shape-rank 违例。
"""
import ast
from typing import Dict, Optional, Tuple

from agent.generators.atk_common_utils.case_config import InputCaseConfig
from agent.generators.common_utils.logger_util import LazyLogger
from agent.generators.param_constraint_solve.z3_expression_solver_utils import ExpressionPreprocessor

logger = LazyLogger()

# 短名（InputCaseConfig.dtype 实际取值）→ canonical 名（constraints_in_parameters expr 用）。
# 镜像 per-run post_check_cases.py 的 DTYPE_SHORT_TO_CANON（DataMatchMap.ACL_DTYPE_TRANSFER_TENSOR_MAP 反向）。
DTYPE_SHORT_TO_CANON = {
    "fp16": "FLOAT16", "fp32": "FLOAT32", "fp64": "FLOAT64", "bf16": "BFLOAT16",
    "float": "FLOAT32", "double": "FLOAT64",
    "int8": "INT8", "int16": "INT16", "int32": "INT32", "int64": "INT64", "int": "INT64",
    "uint8": "UINT8", "uint16": "UINT16", "uint32": "UINT32", "uint64": "UINT64",
    "bool": "BOOL", "string": "STRING",
    "float8_e5m2": "FLOAT8_E5M2", "float8_e4m3fn": "FLOAT8_E4M3FN", "float8_e8m0": "FLOAT8_E8M0",
    "float4_e2m1": "FLOAT4_E2M1", "float4_e1m2": "FLOAT4_E1M2",
    "float6_e3m2": "FLOAT6_E3M2", "float6_e2m3": "FLOAT6_E2M3",
    "hifloat8": "HIFLOAT8", "int4": "INT4", "uint4": "UINT4",
    "complex64": "COMPLEX64", "complex128": "COMPLEX128",
}

SAFE_BUILTINS = {
    "len": len, "min": min, "max": max, "abs": abs, "sum": sum,
    "any": any, "all": all, "True": True, "False": False, "None": None,
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
        # 兼容 canonical 名（不在表内 → upper）与短名（表内映射）。
        self.dtype = DTYPE_SHORT_TO_CANON.get(short, (short.upper() if short else None))
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
    先 `normalize_json_null`（裸 `null`→`None`，tokenizer 安全，不动引号内串），再静默
    `ast.parse(mode='eval')` 校验，通过才 `eval`（沙箱 builtins 仅 len/min/max/abs/sum/any/all）。
    """
    if not expr:
        return True, None
    try:
        normed = ExpressionPreprocessor.normalize_json_null(expr)
    except Exception as e:  # 病态 expr tokenize 失败
        return None, f"normalize: {type(e).__name__}: {e}"
    try:
        ast.parse(normed, mode='eval')
    except SyntaxError as e:
        return None, f"ast.parse(SyntaxError): {e}"
    try:
        result = eval(normed, {"__builtins__": SAFE_BUILTINS}, namespace)  # noqa: S307/B307 沙箱 builtins
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    if isinstance(result, bool):
        return result, None
    try:
        return bool(result), None
    except Exception as e:
        return None, f"truthy_conv: {type(e).__name__}: {e}"
