# -*- coding: UTF-8 -*-
"""
aclnnBatchMatMulWeightNz 用例后处理模块。

该模块由 ``scripts/generate_cases.py`` 调用，用于对底层
``generators.facade.TestCaseGenerator`` 生成的 CaseConfig 列表做确定性
后处理，确保：

1. NZ 形状硬约束：mat2 必须是 5 维，(b, n1, k1, k0, n0) 形式，其中
   k0 == 16 且 n0 == 16，k1 != 1 且 n1 != 1（即 mat2.shape[1] 和
   mat2.shape[3] 都不为 1）。这是为了避免前一轮因 k=1 或 n=1 在
   NZ permute/reshape 阶段失败的 generator_bug。
2. self / out 形状与 mat2 形状的对应关系：
   self.shape = (B, M, K) = (B, M, k1 * 16)
   out.shape = (B, M, N) = (B, M, n1 * 16)
3. self / mat2 / out dtype 一致（BFLOAT16 或 FLOAT16）。
4. cubeMathType ∈ {0, 2}，并随 dtype 互斥（BFLOAT16 不允许 2，FLOAT16 允许 0 或 2）。
5. 跨平台 id 唯一：(platform, idx) 复合形式，注入 platform 字段。

约束来源：constraints.json 的 constraints_in_parameters（Atlas 三平台一致）
+ 算子文档 inputs/aclnnBatchMatMulWeightNz.md 的 NZ 维度说明。

该模块完全确定性：相同 seed 产生相同输出，不依赖任何 LLM 调用。
"""
from __future__ import annotations

import copy
import hashlib
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from generators.atk_common_utils.case_config import CaseConfig, InputCaseConfig


# ---------------------------------------------------------------------------
# 形状推导辅助
# ---------------------------------------------------------------------------

def _stable_rng(platform: str, idx: int, salt: str) -> random.Random:
    """基于 (platform, idx, salt) 的稳定随机源。

    保证同一 (platform, idx) 多次重写时产生相同修正值，便于调试和回放。
    """
    key = f"{platform}::{idx}::{salt}".encode("utf-8")
    h = hashlib.sha1(key).hexdigest()
    seed = int(h[:16], 16)
    return random.Random(seed)


# 合法的 k1/n1 取值候选（k1 != 1, n1 != 1），覆盖典型 / 大 / 边界大小
_K1_CHOICES = [1, 2, 4, 8, 16, 32, 64]
_N1_CHOICES = [1, 2, 4, 8, 16, 32, 64, 128]
# 排除 1（k != 1, n != 1）
_K1_VALID = [x for x in _K1_CHOICES if x != 1]
_N1_VALID = [x for x in _N1_CHOICES if x != 1]

# 批量维度 b 候选（满足 self.shape[0] 与 mat2.shape[0] 的 broadcast 关系）
_B_CHOICES = [1, 2, 3, 4, 8]

# M（self 第二维）的候选值
_M_CHOICES = [1, 16, 32, 64, 128, 256]


def _find_input(case: CaseConfig, name: str) -> Optional[InputCaseConfig]:
    for inp in (case.inputs or []):
        if isinstance(inp, list):
            target = inp[0] if inp else None
        else:
            target = inp
        if target is not None and target.name == name:
            return target
    return None


def _all_inputs(case: CaseConfig) -> List[InputCaseConfig]:
    out: List[InputCaseConfig] = []
    for inp in (case.inputs or []):
        if isinstance(inp, list):
            out.extend(x for x in inp if x is not None)
        else:
            if inp is not None:
                out.append(inp)
    return out


# ---------------------------------------------------------------------------
# 核心：修正 mat2 / self / out / cubeMathType
# ---------------------------------------------------------------------------

def _pick_dtype(platform: str, idx: int) -> str:
    """确定性选择 self/mat2/out dtype。"""
    rng = _stable_rng(platform, idx, "dtype")
    return "BFLOAT16" if rng.random() < 0.5 else "FLOAT16"


def _pick_math_type(platform: str, idx: int, dtype: str) -> int:
    """确定性选择 cubeMathType。BFLOAT16 不允许 2；FLOAT16 允许 0/2。"""
    rng = _stable_rng(platform, idx, "math_type")
    if dtype == "BFLOAT16":
        return 0  # KEEP_DTYPE
    return 0 if rng.random() < 0.5 else 2  # KEEP_DTYPE or USE_FP16


def _pick_shape_factors(platform: str, idx: int) -> Tuple[int, int, int, int]:
    """确定性选取 (b, m, k1, n1)。

    返回 4 元组：(b, m, k1, n1)
    - b  ∈ {1, 2, 3, 4, 8}
    - m  ∈ {1, 16, 32, 64, 128, 256}
    - k1 ∈ {2, 4, 8, 16, 32, 64}   （k != 1 → k1 != 1）
    - n1 ∈ {2, 4, 8, 16, 32, 64, 128}  （n != 1 → n1 != 1）
    """
    rng = _stable_rng(platform, idx, "shape")
    b = rng.choice(_B_CHOICES)
    m = rng.choice(_M_CHOICES)
    k1 = rng.choice(_K1_VALID)
    n1 = rng.choice(_N1_VALID)
    return b, m, k1, n1


def _apply_known_mat2_shape(mat2: InputCaseConfig, b: int, n1: int, k1: int) -> None:
    mat2.shape = [b, n1, k1, 16, 16]
    mat2.format = "NZ"


def _apply_self_shape(self_inp: InputCaseConfig, b: int, m: int, k1: int) -> None:
    self_inp.shape = [b, m, k1 * 16]
    self_inp.format = "ND"


def _apply_out_shape(out_inp: InputCaseConfig, b: int, m: int, n1: int) -> None:
    out_inp.shape = [b, m, n1 * 16]
    out_inp.format = "ND"


def _apply_cube_math_type(attr: InputCaseConfig, value: int) -> None:
    attr.range_values = value
    attr.dtype = "int8"


def _normalize_input(inp: InputCaseConfig) -> InputCaseConfig:
    """把 Optional / range_values 之类统一为合规值（仅在缺失时补齐，不强行覆盖）。"""
    if inp.type != "attr":
        # 标量张量的 range_values 缺失时填 1.0（避免下游 numel 报错）
        if inp.range_values is None:
            inp.range_values = 1.0
        if inp.format is None:
            inp.format = "ND"
    return inp


# ---------------------------------------------------------------------------
# 入口：修正单个 case
# ---------------------------------------------------------------------------

def fix_case(case: CaseConfig, platform: str, idx: int) -> CaseConfig:
    """对单个 case 做 NZ 形状 / dtype / cubeMathType 修正。

    不改变 case 名称 / aclnn_name / standard 等元数据；只调整 inputs 中
    self / mat2 / out / cubeMathType 四个参数。
    """
    fixed = copy.deepcopy(case)

    # 1. 选取确定性 (b, m, k1, n1) 和 dtype
    b, m, k1, n1 = _pick_shape_factors(platform, idx)
    dtype = _pick_dtype(platform, idx)
    math_type = _pick_math_type(platform, idx, dtype)

    # 2. 找到 self / mat2 / cubeMathType / out
    self_inp = _find_input(fixed, "self")
    mat2_inp = _find_input(fixed, "mat2")
    out_inp = _find_input(fixed, "out")
    math_attr = _find_input(fixed, "cubeMathType")

    # 3. 写 dtype
    if self_inp is not None:
        self_inp.dtype = "bf16" if dtype == "BFLOAT16" else "fp16"
    if mat2_inp is not None:
        mat2_inp.dtype = "bf16" if dtype == "BFLOAT16" else "fp16"
    if out_inp is not None:
        out_inp.dtype = "bf16" if dtype == "BFLOAT16" else "fp16"

    # 4. 写形状
    if mat2_inp is not None:
        _apply_known_mat2_shape(mat2_inp, b, n1, k1)
    if self_inp is not None:
        _apply_self_shape(self_inp, b, m, k1)
    if out_inp is not None:
        _apply_out_shape(out_inp, b, m, n1)

    # 5. 写 cubeMathType
    if math_attr is not None:
        _apply_cube_math_type(math_attr, math_type)

    # 6. 归一化所有 input
    for inp in _all_inputs(fixed):
        _normalize_input(inp)

    # 7. standard 字段统一
    if fixed.standard is None:
        fixed.standard = {"acc": "default", "perf": "not_key"}
    else:
        if hasattr(fixed.standard, "acc"):
            fixed.standard.acc = "default"
            fixed.standard.perf = "not_key"
        elif isinstance(fixed.standard, dict):
            fixed.standard["acc"] = "default"
            fixed.standard["perf"] = "not_key"
    return fixed


# ---------------------------------------------------------------------------
# 入口：修正平台级用例列表
# ---------------------------------------------------------------------------

def fix_platform_cases(
    cases: Sequence[CaseConfig],
    platform: str,
    count: int,
) -> List[CaseConfig]:
    """对单个平台的用例列表做修正与裁剪。

    - 输入数量不足时，复用首条作为草稿（确定性复制）再 fix。
    - id 在落盘时改写为 (platform, idx) 复合字符串。
    """
    out: List[CaseConfig] = []
    for idx in range(count):
        if idx < len(cases):
            base = cases[idx]
        else:
            base = cases[0] if cases else CaseConfig(inputs=[])
        out.append(fix_case(base, platform, idx))
    return out


# ---------------------------------------------------------------------------
# 入口：把 case dict 序列化为落盘 JSON 友好的 dict
# ---------------------------------------------------------------------------

def case_to_dict(case: CaseConfig, platform: str, idx: int) -> Dict[str, Any]:
    """把 CaseConfig 序列化为 dict，并注入 platform + 复合 id。"""
    if hasattr(case, "model_dump"):
        d = case.model_dump(mode="json")
    else:
        d = dict(case.__dict__)
    d["id"] = f"{platform}::{idx:03d}"
    d["platform"] = platform
    return d


def fix_and_serialize(
    by_platform: Dict[str, Sequence[CaseConfig]],
    count_per_platform: int,
) -> List[Dict[str, Any]]:
    """修正并序列化所有平台的用例，返回落盘友好的 list[dict]。"""
    out: List[Dict[str, Any]] = []
    for platform, cases in by_platform.items():
        fixed = fix_platform_cases(cases, platform, count_per_platform)
        for idx, case in enumerate(fixed):
            out.append(case_to_dict(case, platform, idx))
    return out
