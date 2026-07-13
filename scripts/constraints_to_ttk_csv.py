#!/usr/bin/env python3
r"""Deterministically project constraints.json onto the TTK CSV case format.

TTK 路线（``run_state.toolchain == "ttk"``）的 GENERATE 阶段用例生成器。无 LLM、
无 Z3：constraints.json 的 ``constraints_in_parameters`` expr 是离散查找表
（``value_dependency`` / ``derived_value`` / ``type_equality``）+ 结构模板
（``format_rank_consistency`` / C0 守卫 / ``all(d>0)``），有限可枚举、无自由维算术耦合，
故用「枚举离散笛卡尔积 + eval 离散 expr 早期剪枝 + 按模板构造代表 shape + eval
shape expr 验证」秒级出全量合法组合，每组合产一条 CSV 行。

产物（对齐 ``scripts/generate_cases.py`` 的约定，便于 case-generator 透传）：
- ``<output_dir>/cases_<sanitized_platform>.csv`` —— 每平台一个 CSV
- ``<output_dir>/generation_summary.json`` —— 平台/数量/路径摘要
- ``<iter_dir>/generation.log`` —— ``--iter-dir`` 传入时的诊断日志

CSV 列契约以外部 ``D:\docs\ttk\cases_demo\torch_add.csv`` 为准（项目内无权威定义）：
``testcase_name, api_name, tensor_view_shapes, tensor_dtypes, tensor_formats,
attributes, output_tensor_indexes``。
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import logging
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("constraints_to_ttk_csv")

# _eval_all 中"不可 eval"的 expr 每条只 warn 一次（按 (platform, expr) 去重），
# 避免 FIA 隐式维度变量约束（B.range_value<=65536 等）在每组合上抛 NoneType 比较异常
# 时，随预算放开评估大量组合而刷屏数百万 warning、淹没 progress heartbeat。
_logged_unevaluable_all: set[tuple[str, str]] = set()


class _BudgetHit(Exception):
    """生成器 recurse 触达时间预算时抛出，使外层 for-loop 能捕获并置 budget_hit 终止。

    必要性：max_seconds 预算原仅在 combo 处理后检查（body 内），若生成器卡在产出首个
    叶子的深度递归/eval 阶段、连一个 combo 都没 pull 到，body 永不执行 → 预算永不触发
    → 静默挂死。下推到 recurse 入口检查，卡产叶时也能 raise 终止。
    """

# --- 既有规范表（与 prompts/modules/format_cast.md §A、acl_format_enum.md 对齐）---

# format -> 标准 rank（ND/NZ 族「任意」这里取 2，可被 format_rank_consistency expr 覆盖）
FORMAT_RANK: dict[str, int] = {
    "ND": 2, "NZ": 5, "FRACTAL_NZ": 5, "FRACTAL_NZ_C0_16": 5, "FRACTAL_NZ_C0_32": 5,
    "NC": 2, "NCL": 3, "NCHW": 4, "NHWC": 4, "HWCN": 4,
    "NCDHW": 5, "NDHWC": 5, "NC1HWC0": 5, "NC1HWC0_C04": 5,
    "NDC1HWC0": 6, "FRACTAL_Z_3D": 4, "FRACTAL_Z": 4, "NCHW_VECT_C0_16": 5,
}

# format -> C0 轴在 shape 中的下标（无 C0 轴则缺省）
FORMAT_C0_AXIS: dict[str, int] = {
    "NDC1HWC0": 5, "FRACTAL_Z_3D": 3, "NC1HWC0": 4, "FRACTAL_NZ": 3,
    "NZ": 3, "FRACTAL_NZ_C0_16": 3, "FRACTAL_NZ_C0_32": 3,
}

# CANN dtype -> (byte size, C0 = 32 // bytes)；C0 守卫按 srcTensor.dtype 的 byte 数
DTYPE_BYTES: dict[str, int] = {
    "INT8": 1, "UINT8": 1, "BOOL": 1, "FLOAT4_E2M1": 1,
    "FLOAT16": 2, "BFLOAT16": 2, "BF16": 2, "FLOAT8_E4M3FN": 1,
    "INT32": 4, "UINT32": 4, "FLOAT32": 4, "FLOAT": 4, "DOUBLE": 8,
    "INT64": 8, "UINT64": 8,
}

# CANN dtype -> torch dtype 名（torch_add.csv 风格）
DTYPE_TO_TORCH: dict[str, str] = {
    "FLOAT": "float32", "FLOAT32": "float32", "DOUBLE": "float64",
    "FLOAT16": "float16", "BF16": "bfloat16", "BFLOAT16": "bfloat16",
    "INT8": "int8", "UINT8": "uint8", "INT16": "int16", "UINT16": "uint16",
    "INT32": "int32", "UINT32": "uint32", "INT64": "int64", "UINT64": "uint64",
    "BOOL": "bool", "FLOAT8_E4M3FN": "float8_e4m3fn", "FLOAT4_E2M1": "float4_e2m1",
}

# 自由维取值集合（刻意避开 1，以规避 cross_param_constraint 的 shape[0]==1 禁止模式）
FREE_DIM_SIZES = (2, 16)

# eval 时放行的内建（expr 用到 len / all / any 等）
SAFE_BUILTINS = {
    "len": len, "all": all, "any": any, "min": min, "max": max, "abs": abs,
    "int": int, "float": float, "bool": bool, "str": str, "tuple": tuple,
    "list": list, "True": True, "False": False, "None": None,
}

CSV_HEADER = [
    "testcase_name", "api_name", "tensor_view_shapes", "tensor_dtypes",
    "tensor_formats", "attributes", "output_tensor_indexes",
]


# --- 小工具 ---


def _list_value(field: Any) -> list:
    """取 ParamAttributes 某字段的 value（dict 取 .value，list 直通，其它包成 list）。"""
    if isinstance(field, dict):
        v = field.get("value")
        return v if isinstance(v, list) else ([v] if v is not None else [])
    if isinstance(field, list):
        return field
    if field is None:
        return []
    return [field]


def _attr_for(constraints: dict, section: str, param: str, platform: str) -> dict:
    """取 constraints[section][param][platform] 的 ParamAttributes。"""
    bucket = constraints.get(section, {}).get(param, {})
    return bucket.get(platform, {}) if isinstance(bucket, dict) else {}


def _is_tensor(attr: dict) -> bool:
    t = attr.get("type")
    t = t.get("value") if isinstance(t, dict) else t
    return t in ("aclTensor", "aclTensorList")


def _type_name(attr: dict) -> str:
    t = attr.get("type")
    t = t.get("value") if isinstance(t, dict) else t
    return t if isinstance(t, str) else ""


def _is_optional(attr: dict) -> bool:
    """ParamAttributes.is_optional，兼容 dict{value:} 与标量两种形态。"""
    v = attr.get("is_optional")
    v = v.get("value") if isinstance(v, dict) else v
    return v is True


def _is_dtype_empty(attr: dict) -> bool:
    """dtype.value 为空（文档未给 dtype，如预留参数 key_rope_antiquant_scale）。

    文档对预留参数只写"使用默认值即可"、不给 dtype；提取按禁止伪造规则
    正确留空 dtype.value=[]。生成器不可填空串（TTK 生成期 numpy.astype('')
    抛 TypeError）、不可在 attributes 标 None（TTK 对张量参数显式拒 ValueError，
    ops-test-kit input_generation.py:160-165）。正道是整体省略该张量位。
    """
    return not _list_value(attr.get("dtype"))


def _dtype_c0(dtype: str | None) -> int | None:
    if not dtype:
        return None
    bytes_ = DTYPE_BYTES.get(dtype)
    if bytes_ is None:
        return None
    return 32 // bytes_  # 1byte->32, 2byte->16, 4byte->8


def _parse_format_ranks(exprs: list[dict]) -> dict[str, int]:
    """从 format_rank_consistency expr 里抽 format->rank（min for 范围，固定值 for ==）。"""
    overrides: dict[str, int] = {}
    pat_range = re.compile(
        r'\.format\s*==\s*"([^"]+)"\s*and\s*(\d+)\s*<=\s*len\(\w+\.shape\)\s*<=\s*(\d+)'
    )
    pat_fixed = re.compile(
        r'\.format\s*==\s*"([^"]+)"\s*and\s*len\(\w+\.shape\)\s*==\s*(\d+)'
    )
    for e in exprs:
        if e.get("expr_type") != "format_rank_consistency":
            continue
        s = e.get("expr", "")
        for m in pat_range.finditer(s):
            overrides.setdefault(m.group(1), int(m.group(2)))
        for m in pat_fixed.finditer(s):
            overrides.setdefault(m.group(1), int(m.group(2)))
    return overrides


def _is_shape_touching(expr: str) -> bool:
    """expr 是否引用 shape（需先构造 shape 才能 eval）。"""
    return "shape" in expr or "len(" in expr


def _construct_shape(
    fmt: str | None,
    dtype: str | None,
    dimensions: list | None,
    rank_overrides: dict[str, int],
) -> list[int]:
    """按 format->rank + C0 轴 + 自由维构造一个结构合法的代表 shape。"""
    rank: int | None = None
    if fmt:
        rank = rank_overrides.get(fmt)
        if rank is None:
            rank = FORMAT_RANK.get(fmt)
    if rank is None:
        if isinstance(dimensions, list) and len(dimensions) == 2:
            rank = dimensions[0]
        elif isinstance(dimensions, list) and dimensions:
            rank = len(dimensions)
        else:
            rank = 2
    if rank < 1:
        rank = 1
    shape = [FREE_DIM_SIZES[0]] * rank  # 自由维 = 2（>0，避开禁止模式）
    c0_axis = FORMAT_C0_AXIS.get(fmt) if fmt else None
    if c0_axis is not None and c0_axis < rank:
        c0 = _dtype_c0(dtype)
        if c0 is not None:
            shape[c0_axis] = c0
    return shape


# --- 离散枚举 + 早期剪枝 ---


def _synthesized_scalar_domain(attr: dict) -> list:
    """标量参数 allowed_range_value 为空时合成的代表域。

    使 range_value 非 None：(1) 离散剪枝 expr 可正常 eval——不再因 NoneType 比较抛异常
    被 except 兜底成「放行全部」从而零剪枝（缺陷 B）；(2) CSV attributes 列带具体标量值，
    不再因 range_value=None 被 `_row_for` 的 `if range_value is not None` 过滤而 omit
    required 标量参数。仅对 aclBool/aclFloat/aclInt 合成；aclIntArray 等数组类型与未知
    类型回落 [None]——数组值语义非标量，其约束走 len()/shape，不参与离散 range_value expr。
    """
    t = _type_name(attr)
    if t == "aclBool":
        return [True, False]
    if t == "aclFloat":
        return [1.0]
    if t == "aclInt":
        return [1, 2, 4, 8]
    return [None]


def _discrete_options(attr: dict) -> list[dict[str, Any]]:
    """单个参数的离散取值候选（每项是一个 partial binding dict）。"""
    if _is_tensor(attr):
        formats = _list_value(attr.get("format")) or [None]
        dtypes = _list_value(attr.get("dtype")) or [None]
        opts = []
        for f, d in itertools.product(formats, dtypes):
            opts.append({"format": f, "dtype": d, "range_value": None})
        return opts
    rvs = _list_value(attr.get("allowed_range_value"))
    if not rvs:
        # 缺陷 B 修复：空 allowed_range_value 的标量参数合成代表域，避免 range_value=None
        # 导致引用 .range_value 的剪枝 expr NoneType 异常→放行→零剪枝（且 CSV 缺标量值）。
        rvs = _synthesized_scalar_domain(attr)
    return [{"format": None, "dtype": None, "range_value": v} for v in rvs]


def _enumerate_combos(
    params: list[tuple[str, str, dict]],  # (section, name, attr)
    discrete_exprs: list[dict],
    platform: str,
    deadline: float = 0.0,  # 时间预算（monotonic 秒），0=不限；recurse 每帧入口检查
):
    """递归逐参数绑定，每绑一个就 eval 已可解的离散 expr 剪枝；惰性 yield 合法叶子。

    缺陷 A 修复：改为生成器（不再一次性物化完整笛卡尔积返回 list）。调用方按需取前 N
    个即早停，`--count` 对枚举阶段真正生效——FIA ~26 张量 × 多 dtype ≈ 3^N 的全量空间
    永不被物化，count=10 只评估前若干叶子。不可 eval 的 expr 每条仅记一次 warning
    （缺陷 B 配套：不再每个候选重复→百 MB 日志）。
    """
    option_lists = [_discrete_options(attr) for _, _, attr in params]
    _total = 1
    for _o in option_lists:
        _total *= max(len(_o), 1)
        if _total > 10**15:
            _total = -1
            break
    logger.info(
        "platform=%s: 生成器启动 参数数=%d 各参数候选数=%s 离散总积≈%s",
        platform, len(params), [len(o) for o in option_lists],
        ">10^15" if _total < 0 else str(_total),
    )
    logged_unevaluable: set[tuple[str, str]] = set()
    leaf_count = [0]

    def expr_applies(e: dict, binding: dict[str, dict]) -> bool:
        if _is_shape_touching(e.get("expr", "")):
            return False
        return all(relation_param in binding for relation_param in e.get("relation_params", []))

    def eval_discrete(e: dict, binding: dict[str, dict]) -> bool:
        ns = {name: SimpleNamespace(**vals) for name, vals in binding.items()}
        try:
            return bool(eval(e["expr"], {"__builtins__": SAFE_BUILTINS}, ns))
        except Exception as exc:  # noqa: BLE001 - expr 不可解则跳过该 expr 的剪枝（保守放行）
            key = (platform, e.get("expr", ""))
            if key not in logged_unevaluable:
                logged_unevaluable.add(key)
                logger.warning(
                    "platform=%s expr 不可 eval（跳过该 expr 剪枝，保守放行）: %r → %s",
                    platform, e.get("expr"), exc,
                )
            return True

    def recurse(idx: int, binding: dict[str, dict]):
        if deadline and time.monotonic() > deadline:
            raise _BudgetHit()
        if idx == len(params):
            leaf_count[0] += 1
            if leaf_count[0] == 1 or leaf_count[0] % 1000 == 0:
                logger.info("platform=%s: 生成器产出第 %d 个叶子", platform, leaf_count[0])
            yield binding
            return
        section, name, attr = params[idx]
        for opt in option_lists[idx]:
            nb = dict(binding)
            nb[name] = opt
            ok = True
            for e in discrete_exprs:
                if expr_applies(e, nb) and not eval_discrete(e, nb):
                    ok = False
                    break
            if ok:
                yield from recurse(idx + 1, nb)

    yield from recurse(0, {})


def _build_binding(combo: dict[str, dict], params: list[tuple[str, str, dict]],
                   rank_overrides: dict[str, int]) -> dict[str, SimpleNamespace]:
    ns: dict[str, SimpleNamespace] = {}
    for section, name, attr in params:
        vals = combo[name]
        fmt = vals.get("format")
        dtype = vals.get("dtype")
        if _is_tensor(attr):
            dims = _list_value(attr.get("dimensions"))
            shape = _construct_shape(fmt, dtype, dims, rank_overrides)
        else:
            shape = None
        ns[name] = SimpleNamespace(
            format=fmt, dtype=dtype, range_value=vals.get("range_value"), shape=shape
        )
    return ns


def _eval_all(
    exprs: list[dict], ns: dict[str, SimpleNamespace], platform: str
) -> tuple[bool, str | None]:
    """eval 全部约束。返回 (passed, reason)：reason 为首个判 False 的 expr 文本（诊断用）。

    不可 eval 的 expr（NameError 等）仍保守放行并 warn——增量剪枝阶段 relation_params 不全时
    会走到这里；末尾全量 eval 阶段所有参数已绑定，再抛异常说明 expr 本身有缺陷。
    """
    for e in exprs:
        try:
            if not bool(eval(e["expr"], {"__builtins__": SAFE_BUILTINS}, ns)):
                return False, e.get("expr", "<no expr>")
        except Exception as exc:  # noqa: BLE001
            key = (platform, e.get("expr", ""))
            if key not in _logged_unevaluable_all:
                _logged_unevaluable_all.add(key)
                logger.warning("platform=%s expr 不可 eval（放行）: %r → %s", platform, e.get("expr"), exc)
    return True, None


# --- CSV 行组装 ---


def _torch_dtype(dtype: str | None) -> str:
    if not dtype:
        return ""
    return DTYPE_TO_TORCH.get(dtype, dtype.lower())


def _compact_repr(obj: Any) -> str:
    """repr 去掉逗号/冒号后空格，对齐 torch_add.csv 的紧凑风格（如 ((2,3,4),(2,3,4))）。"""
    return repr(obj).replace(", ", ",").replace(": ", ":")


def _row_for(
    operator_name: str,
    platform: str,
    case_id: int,
    params: list[tuple[str, str, dict]],
    combo: dict[str, dict],
    rank_overrides: dict[str, int],
    all_exprs: list[dict],
) -> tuple[dict[str, str] | None, str | None]:
    ns = _build_binding(combo, params, rank_overrides)
    # 末尾全量 eval：增量剪枝时 relation_params 不全的离散 expr 走了 NameError→放行兜底，
    # 这里所有参数已绑定，重新 eval 能补漏。
    ok, reason = _eval_all(all_exprs, ns, platform)
    if not ok:
        return None, reason

    # 剔除"可选 + dtype 为空"的张量位（文档未给 dtype 的预留参数，如
    # key_rope_antiquant_scale）。TTK e2e 对签名尾部可选张量支持整体省略：
    # build_args 队列空时自动补 None（ops-test-kit param_plan.py:397-398），
    # output_tensor_indexes 基于实际列出的张量位置（testcase_e2e.py:588-596）
    # 自动重定向，不会错位。前提：该参数在签名尾部（其后无非可选张量），
    # 否则张量按位置 pop（param_plan.py:392-396）会错位——故仅对"可选+空dtype"
    # 位省略，且调用方需保证其在签名尾部（key_rope_antiquant_scale 是签名末张量，满足）。
    # 被剔除参数仍在 _build_binding 的全量 ns 中，剪枝不受影响，仅不进最终 tuple。
    tensor_params = [
        (n, a) for _, n, a in params
        if _is_tensor(a) and not (_is_optional(a) and _is_dtype_empty(a))
    ]
    input_attr_params = [(n, a) for s, n, a in params if s == "inputs" and not _is_tensor(a)]
    output_section_names = {n for s, n, _ in params if s == "outputs"}

    shapes = [tuple(ns[n].shape) for n, _ in tensor_params]
    dtypes = [_torch_dtype(ns[n].dtype) for n, _ in tensor_params]
    formats = [ns[n].format or "" for n, _ in tensor_params]
    out_idx = [i for i, (n, _) in enumerate(tensor_params) if n in output_section_names]

    attributes = {n: ns[n].range_value for n, _ in input_attr_params
                  if ns[n].range_value is not None}

    src_fmt = formats[0] if formats else ""
    src_dt = _torch_dtype(ns[tensor_params[0][0]].dtype) if tensor_params else ""
    # testcase_name：算子短名 + 平台短名 + id + dtype + 格式线索
    op_short = re.sub(r"[^A-Za-z0-9]+", "_", operator_name).strip("_")
    plat_short = re.sub(r"[^A-Za-z0-9]+", "_", platform).strip("_").split("_")[0]
    testcase_name = f"{op_short}_{plat_short}_{case_id:03d}_{src_dt}_{src_fmt}"

    return {
        "testcase_name": testcase_name,
        "api_name": operator_name,
        "tensor_view_shapes": _compact_repr(tuple(shapes)) if shapes else "",
        "tensor_dtypes": _compact_repr(tuple(dtypes)) if dtypes else "",
        "tensor_formats": _compact_repr(tuple(formats)) if any(formats) else "",
        "attributes": _compact_repr(attributes) if attributes else "",
        "output_tensor_indexes": _compact_repr(tuple(out_idx)) if out_idx else "",
    }, None


def _write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _platform_params(constraints: dict, platform: str) -> list[tuple[str, str, dict]]:
    """inputs 段在前、outputs 段在后，按 JSON 键序收集该平台的所有参数。"""
    params: list[tuple[str, str, dict]] = []
    for section in ("inputs", "outputs"):
        for name, bucket in constraints.get(section, {}).items():
            attr = bucket.get(platform, {}) if isinstance(bucket, dict) else {}
            if attr:
                params.append((section, name, attr))
    return params


def _generate_for_platform(
    constraints: dict, platform: str, count: int,
    max_eval: int = 0, max_seconds: float = 0.0,
) -> tuple[list[dict[str, str]], int, str]:
    """枚举离散组合产出 CSV 行。返回 (rows, n, termination)。

    termination ∈ {"satisfied","exhausted","budget_hit"}：
    - satisfied: 取够 count 行；
    - exhausted: 枚举完所有组合（含 <count 的合法情形，如某平台合法组合本就少于 count）；
    - budget_hit: 触达 max_eval/max_seconds 上限仍未满足——通常意味约束不可满足
      （如可选张量从不缺席致 presence_dependency 的 `is None` 恒 False、或开放整型标量
      合成域不含约束要求值如 block_size 需 128 倍数但合成域=[1,2,4,8]），此刻应判
      generator_bug 而非静默穷举 10^15+ 组合挂死。
    """
    operator_name = constraints.get("operator_name", "<unknown>")
    params = _platform_params(constraints, platform)
    all_exprs = constraints.get("constraints_in_parameters", {}).get(platform, [])
    discrete_exprs = [e for e in all_exprs if not _is_shape_touching(e.get("expr", ""))]
    rank_overrides = _parse_format_ranks(all_exprs)

    # 离散组合空间估计（惰性枚举不物化，但让用户先看到规模，判断是否可能长任务）
    option_counts = [len(_discrete_options(a)) for _, _, a in params]
    space, overflow = 1, False
    for c in option_counts:
        space *= max(c, 1)
        if space > 10**15:
            overflow = True
            break
    logger.info(
        "platform=%s: params=%d, 离散组合空间≈%s, 目标=%d 行, 上限 max_eval=%s max_seconds=%s",
        platform, len(params),
        ">10^15 (仅作估计)" if overflow else f"{space}",
        count, max_eval if max_eval else "∞", max_seconds if max_seconds else "∞",
    )

    rows: list[dict[str, str]] = []
    evaluated = 0
    rejected = 0
    reasons: dict[str, int] = {}
    started = time.monotonic()
    last_heartbeat = started
    termination = "exhausted"
    deadline = (time.monotonic() + max_seconds) if max_seconds else 0.0
    logger.info("platform=%s: 进入枚举循环，拉取首个叶子中（若无后续 [progress] 则卡在产叶；deadline=%s）", platform, f"{max_seconds:.0f}s" if max_seconds else "∞")
    # 缺陷 A：_enumerate_combos 为惰性生成器，取够 count 即 break；
    # 但若约束不可满足（每组合全被 _eval_all 拒）或卡在产叶（深度递归/eval 慢）→ 永不取够，
    # 原实现静默穷举 10^15+ 挂死。故：(a) max_eval 限 combo 数；(b) deadline 下推到 recurse，
    # 卡在产叶阶段也能 raise _BudgetHit 终止；(c) 打印拒绝原因助诊断。
    try:
        for combo in _enumerate_combos(params, discrete_exprs, platform, deadline):
            if count and len(rows) >= count:
                termination = "satisfied"
                break
            evaluated += 1
            row, reason = _row_for(
                operator_name, platform, len(rows), params, combo, rank_overrides, all_exprs,
            )
            if row is not None:
                rows.append(row)
            else:
                rejected += 1
                if reason and len(reasons) < 12:
                    reasons[reason] = reasons.get(reason, 0) + 1
            now = time.monotonic()
            if now - last_heartbeat >= 5.0:
                last_heartbeat = now
                logger.info(
                    "  [progress] %s: evaluated=%d yielded=%d rejected=%d elapsed=%.1fs",
                    platform, evaluated, len(rows), rejected, now - started,
                )
            if max_eval and evaluated >= max_eval:
                termination = "budget_hit"
                break
            if max_seconds and (now - started) >= max_seconds:
                termination = "budget_hit"
                break
    except _BudgetHit:
        termination = "budget_hit"
        logger.warning(
            "platform=%s: 生成器触达时间预算 deadline=%s（卡在产叶/深度递归阶段，"
            "evaluated=%d yielded=%d），提前终止", platform, f"{max_seconds:.0f}s", evaluated, len(rows),
        )

    elapsed = time.monotonic() - started
    logger.info(
        "platform=%s: 枚举结束 termination=%s evaluated=%d yielded=%d rejected=%d elapsed=%.2fs",
        platform, termination, evaluated, len(rows), rejected, elapsed,
    )
    if termination == "budget_hit" and len(rows) < count:
        sample = "; ".join(f"{r[:80]}×{n}" for r, n in list(reasons.items())[:6])
        logger.error(
            "platform=%s: 触达预算仍仅 %d/%d 行。首要拒绝原因: %s。"
            "常见根因：(1) 可选张量从不缺席——_discrete_options 对 tensor 只产 format×dtype "
            "永不产 None，致 presence_dependency 的 `is None` 恒 False、零剪枝且语义非法；"
            "(2) 开放整型标量合成域不含约束要求值，如 block_size 需 128 倍数但合成域=[1,2,4,8]。",
            platform, len(rows), count, sample or "(无记录——可能全部因 NameError 放行却仍 0 行)",
        )
    return rows, len(rows), termination


def _setup_iter_log(iter_dir: Path) -> Path | None:
    try:
        iter_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    log_path = iter_dir / "generation.log"
    try:
        handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        return log_path
    except OSError as exc:
        print(f"[constraints_to_ttk_csv] warning: cannot open {log_path}: {exc}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--constraints", required=True)
    parser.add_argument("--output", required=True,
                        help="输出路径占位；实际取 .parent 当 output_dir（对齐 generate_cases.py）")
    parser.add_argument("--count", type=int, default=0,
                        help="每平台行数上限（0 = 穷举全部合法离散组合）")
    parser.add_argument("--seed", type=int, default=42,
                        help="占位参数（本脚本确定性枚举，不使用随机种子；为兼容透传保留）")
    parser.add_argument("--iter-dir", default=None,
                        help="可选: 迭代目录；传入后写 <iter-dir>/generation.log")
    parser.add_argument("--max-eval", type=int, default=5_000_000,
                        help="单平台最大评估组合数（防不可满足约束时静默穷举 10^15+ 挂死）；0=无限")
    parser.add_argument("--max-seconds", type=float, default=300.0,
                        help="单平台最大耗时秒数（同上防挂死）；0=无限")
    args = parser.parse_args()

    iter_dir = Path(args.iter_dir) if args.iter_dir else None
    if iter_dir:
        _setup_iter_log(iter_dir)
    # stderr 实时进度：无 --iter-dir 也能在终端看到 progress/heartbeat，避免长任务"卡着无反馈"
    if not any(getattr(h, "stream", None) is sys.stderr for h in logger.handlers):
        _sh = logging.StreamHandler(sys.stderr)
        _sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _sh.setLevel(logging.INFO)
        logger.addHandler(_sh)
    logger.setLevel(logging.INFO)

    started = time.monotonic()
    logger.info(
        "start: constraints=%s output=%s count=%d iter_dir=%s",
        args.constraints, args.output, args.count, iter_dir or "(none)",
    )

    constraints_path = Path(args.constraints)
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    from scripts.normalize_constraints import normalize_constraints
    normalized = normalize_constraints(constraints)
    if normalized:
        logger.info("normalized %d type-dependent constraint attribute values", normalized)

    operator_name = constraints.get("operator_name", "<unknown>")
    platforms = constraints.get("product_support", [])
    output_dir = Path(args.output).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    per_platform_files: dict[str, str] = {}
    per_platform_counts: dict[str, int] = {}
    per_platform_termination: dict[str, str] = {}
    any_budget_hit = False
    for platform in platforms:
        rows, n, term = _generate_for_platform(
            constraints, platform, args.count, args.max_eval, args.max_seconds,
        )
        sanitized = platform.replace("/", "_")
        csv_path = output_dir / f"cases_{sanitized}.csv"
        csv_path.unlink(missing_ok=True)
        _write_csv(rows, csv_path)
        per_platform_files[platform] = str(csv_path)
        per_platform_counts[platform] = n
        per_platform_termination[platform] = term
        if term == "budget_hit" and n < args.count:
            any_budget_hit = True
        logger.info("  %s -> %s (%d rows, %s)", platform, csv_path, n, term)

    status = "budget_exhausted" if any_budget_hit else "ok"
    summary = {
        "operator_name": operator_name,
        "requested_per_platform": args.count,
        "platforms": per_platform_counts,
        "per_platform_files": per_platform_files,
        "per_platform_termination": per_platform_termination,
        "status": status,
        "total": sum(per_platform_counts.values()),
        "seed": args.seed,
        "generator_version": "scripts.constraints_to_ttk_csv (enumeration + eval, no Z3)",
        "id_format": "平台内 0 基整数 (per-platform 0,1,2,...)；每离散组合 1 条代表 shape",
        "case_format": "csv",
        "toolchain": "ttk",
    }
    (output_dir / "generation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    elapsed = time.monotonic() - started
    logger.info(
        "done: %d rows across %d platforms in %.2fs status=%s -> %s",
        summary["total"], len(per_platform_files), elapsed, status, output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if any_budget_hit else 0


if __name__ == "__main__":
    raise SystemExit(main())
