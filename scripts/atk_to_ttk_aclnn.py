#!/usr/bin/env python3
"""Convert ATK compact JSON cases to TTK ACLNN-mode CSV.

TTK ACLNN mode uses ``python3 -m ttk aclnn -i <csv>`` (executed from within
the ops-test-kit directory).  Unlike E2E mode, ACLNN mode:
- calls the native aclnn* C API directly → no custom golden plugin needed
- uses ``ApiTestcaseStructure``: tensor_view_shapes / tensor_dtypes / attributes / …
- TensorList params are nested tuples: ((sub0, sub1),) vs regular (d0, d1)

The converter accepts ``constraints.json`` so tensor/attribute order is derived
from the documented ``*GetWorkspaceSize`` signature.  A legacy
``aclnnGroupedMatmulV5`` fallback is retained for direct callers that do not
provide constraints, but the unified pipeline always supplies them.
"""
from __future__ import annotations

import argparse
import ast
import csv
from copy import deepcopy
import json
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# dtype mapping (ATK JSON → TTK ACLNN)
# ---------------------------------------------------------------------------
DTYPE_MAP = {
    "fp16": "float16",
    "float16": "float16",
    "fp32": "float32",
    "float32": "float32",
    "float": "float32",
    "bf16": "bfloat16",
    "int8": "int8",
    # TTK ACLNN supports native INT4 and packs its backing bytes itself.
    "int4": "int4",
    "int32": "int32",
    "int64": "int64",
    "uint8": "uint8",
    "uint64": "uint64",
    "bool": "bool",
}

# ---------------------------------------------------------------------------
# full tensor-parameter signature (ordered)
# ---------------------------------------------------------------------------
# Each entry: (name, is_tensor_list, is_output)
# Order MUST match the aclnnGroupedMatmulV5GetWorkspaceSize C signature.
_SIGNATURE: list[dict[str, Any]] = [
    {"name": "x",                             "tensor_list": True,  "output": False},
    {"name": "weight",                        "tensor_list": True,  "output": False},
    {"name": "biasOptional",                  "tensor_list": True,  "output": False},
    {"name": "scaleOptional",                 "tensor_list": True,  "output": False},
    {"name": "offsetOptional",                "tensor_list": True,  "output": False},
    {"name": "antiquantScaleOptional",        "tensor_list": True,  "output": False},
    {"name": "antiquantOffsetOptional",       "tensor_list": True,  "output": False},
    {"name": "perTokenScaleOptional",         "tensor_list": True,  "output": False},
    {"name": "groupListOptional",             "tensor_list": False, "output": False},
    {"name": "activationInputOptional",       "tensor_list": True,  "output": False},
    {"name": "activationQuantScaleOptional",  "tensor_list": True,  "output": False},
    {"name": "activationQuantOffsetOptional", "tensor_list": True,  "output": False},
    {"name": "out",                           "tensor_list": True,  "output": True},
    {"name": "activationFeatureOutOptional",  "tensor_list": True,  "output": True},
    {"name": "dynQuantScaleOutOptional",      "tensor_list": True,  "output": True},
]

# attr names (non-tensor params that go into the ``attributes`` CSV column)
_ATTR_NAMES = {
    "splitItem", "groupType", "groupListType", "actType", "tuningConfigOptional",
}

_OUTPUT_NAMES = {e["name"] for e in _SIGNATURE if e["output"]}


def _split_c_parameters(raw: str) -> list[str]:
    """Split a C parameter list without breaking nested declarators."""
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(raw):
        if char in "([<":
            depth += 1
        elif char in ")]>":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(raw[start:index].strip())
            start = index + 1
    tail = raw[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _signature_from_constraints(
    constraints: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return ordered tensor metadata and ordered non-tensor attributes with type info."""
    if not constraints:
        legacy = [
            {
                **entry,
                "optional": entry["name"] not in {"x", "weight"},
            }
            for entry in _SIGNATURE
        ]
        # Legacy path: no type info available, use empty string
        return legacy, [{"name": n, "type": ""} for n in sorted(_ATTR_NAMES)]

    function_signature = str(constraints.get("function_signature") or "")
    match = re.search(r"GetWorkspaceSize\s*\((.*?)\)\s*;?", function_signature, re.S)
    if not match:
        # One-stage ACLNN APIs are valid project inputs as well.  Prefer the
        # first documented ACLNN callable and ignore runtime-only parameters.
        match = re.search(r"\baclnn\w+\s*\((.*?)\)\s*;?", function_signature, re.S)
    if not match:
        raise ValueError(
            "TTK_ACLNN_SIGNATURE_REQUIRED: constraints.function_signature must "
            "contain an aclnn callable declaration"
        )

    output_names = set((constraints.get("outputs") or {}).keys())
    product_support = constraints.get("product_support") or []

    def is_optional(name: str) -> bool:
        section = (
            (constraints.get("outputs") or {}).get(name)
            if name in output_names
            else (constraints.get("inputs") or {}).get(name)
        )
        if not isinstance(section, dict):
            return False
        card = section
        if "is_optional" not in card:
            for platform in product_support:
                candidate = section.get(platform)
                if isinstance(candidate, dict):
                    card = candidate
                    break
        optional = card.get("is_optional") if isinstance(card, dict) else None
        if isinstance(optional, dict):
            optional = optional.get("value")
        return optional is True

    tensors: list[dict[str, Any]] = []
    attrs: list[dict[str, str]] = []
    ignored = {"workspace", "workspaceSize", "executor", "stream"}
    for declaration in _split_c_parameters(match.group(1)):
        declaration = declaration.split("=", 1)[0].strip()
        name_match = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^]]*\])?\s*$", declaration)
        if not name_match:
            raise ValueError(f"Cannot parse ACLNN parameter declaration: {declaration!r}")
        name = name_match.group(1)
        if name in ignored:
            continue
        type_text = declaration[: name_match.start(1)]
        if "aclTensorList" in type_text:
            tensors.append({
                "name": name, "tensor_list": True, "output": name in output_names,
                "optional": is_optional(name),
            })
        elif re.search(r"\baclTensor\b", type_text):
            tensors.append({
                "name": name, "tensor_list": False, "output": name in output_names,
                "optional": is_optional(name),
            })
        else:
            attrs.append({"name": name, "type": type_text.strip()})

    if not tensors:
        raise ValueError("TTK_ACLNN_SIGNATURE_EMPTY: no aclTensor/aclTensorList parameters")
    return tensors, attrs


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _get_attr_default(c_type: str) -> Any:
    """根据 C 签名的类型文本返回 attr 参数的默认值。

    当 cases.json 缺少某个参数时，根据类型设置合适的默认值：
    - 数组类型 (aclIntArray*/aclFloatArray*/aclBoolArray*) → []
    - 布尔类型 (bool/attr_bool/_Bool) → False
    - 浮点类型 (double/float) → 0.0
    - 整数类型 (int8_t/int32_t/int64_t/uint8_t/...) → 0
    - 字符串类型 (const char*/aclString*/...) → None
    - 其他类型 → None
    """
    rt = c_type.strip()
    # 数组类型 → 空列表
    if "aclIntArray" in rt or "aclFloatArray" in rt or "aclBoolArray" in rt:
        return []
    # 布尔类型
    if rt in ("bool", "attr_bool", "_Bool"):
        return False
    # 浮点类型
    if rt in ("double", "float", "float32", "float64"):
        return 0.0
    # 整数类型
    if rt in ("int", "int8_t", "int16_t", "int32_t", "int64_t",
              "uint8_t", "uint16_t", "uint32_t", "uint64_t", "long", "size_t"):
        return 0
    # 字符串类型
    if rt in ("str", "string", "const char*", "char*", "aclString*", "const aclString*"):
        return None
    # 其他类型（如 aclDataType, aclFormat 等）→ None
    return None


def _clamp_to_dtype(dtype: str, value: Any) -> Any:
    """Clamp integer values to dtype bounds (defence in depth)."""
    bounds = {
        "int8": (-128, 127), "uint8": (0, 255),
        "int4": (-8, 7),
        "int32": (-2_147_483_648, 2_147_483_647),
        "uint32": (0, 4_294_967_295),
        "int64": (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
        "uint64": (0, 18_446_744_073_709_551_615),
    }.get(dtype)
    if bounds is None or not isinstance(value, (int, float)):
        return value
    lo, hi = bounds
    if isinstance(value, list):
        return [max(lo, min(hi, v)) if isinstance(v, (int, float)) else v for v in value]
    return max(lo, min(hi, value))


def _resolve_range_value(item: dict[str, Any]) -> Any:
    """Resolve a concrete scalar from an ATK range_values field."""
    value = item.get("range_values")
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], (int, float)):
        return value[0]  # deterministic: take lower bound
    return value


def _expand_attr_range_values(item: dict[str, Any]) -> Any:
    """复刻 generator.py 对带 ``length`` 的 ``attrs`` 输入的展开语义。

    generator.py 的 ``_expand_attrs_input`` 把 ``type=="attrs"`` 且带 ``length``
    的输入展开为 ``length`` 个条目。对 TTK 的 ``attributes`` dict（一个参数名
    对应一个值），展开结果折叠为一个 ``length`` 元素数组：
    - ``length`` 为 None/0 → ``[]``（空数组）
    - ``range_values`` 为 list 且 ``len == length`` → 按位取值（``rv[i]``）
    - ``range_values`` 为 list 且 ``len != length`` → 复制 ``length`` 份，每份
      保持原 ``range_values``
    - ``range_values`` 为标量 → 复制 ``length`` 份标量
    """
    rv = item.get("range_values")
    length = item.get("length")
    if length is None or int(length) == 0:
        return []
    length = int(length)
    if isinstance(rv, list):
        rv_list = rv
    elif rv is not None:
        rv_list = [rv]
    else:
        rv_list = []
    if len(rv_list) == length:
        return list(rv_list)
    return [deepcopy(rv_list) for _ in range(length)]


def _tensor_data_range(item: dict[str, Any] | None) -> tuple[Any, Any]:
    """Preserve a safe scalar/range domain in the TTK ACLNN CSV."""
    if not item or item.get("shape") is None:
        return (None, None)
    value = item.get("range_values")
    dtype = _format_dtype(item.get("dtype"))
    if isinstance(value, list) and len(value) == 2 and all(
        isinstance(bound, (int, float)) for bound in value
    ):
        lo = _clamp_to_dtype(dtype, value[0])
        hi = _clamp_to_dtype(dtype, value[1])
        return (min(lo, hi), max(lo, hi))
    if isinstance(value, (int, float)):
        fixed = _clamp_to_dtype(dtype, value)
        return (fixed, fixed)
    return (None, None)


def materialize_scatter_pa_kv_cache_cases(
    cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project generated ScatterPaKvCache cases to a runnable A2 smoke set.

    This operator's seven documented scenarios are correlated tuples of rank,
    shape, dtype, format, presence and string attributes.  The generic random
    generator currently samples those axes independently.  Until literal
    scenario generation is available, use the already verified scenario 2
    (Norm + None + ND) as a non-blocking functional baseline.
    """
    fixed = deepcopy(cases)
    audit: list[dict[str, Any]] = []

    def tensor(name: str, dtype: str, shape: list[int], values: Any) -> dict[str, Any]:
        return {
            "name": name, "type": "tensor", "required": True,
            "dtype": dtype, "shape": shape, "range_values": values,
            "length": None, "format": "ND", "backward": False,
            "align_32B": None, "outlier_values": None,
        }

    def attr(
        name: str, dtype: str, value: Any, *, kind: str = "attr",
        length: int | None = None,
    ) -> dict[str, Any]:
        return {
            "name": name, "type": kind, "required": True, "dtype": dtype,
            "shape": None, "range_values": value, "length": length,
            "format": None, "backward": False, "align_32B": None,
            "outlier_values": None,
        }

    for index, case in enumerate(fixed):
        api_name = case.get("name") or case.get("aclnn_name")
        if api_name != "aclnnScatterPaKvCache":
            audit.append({"id": case.get("id", index), "changes": []})
            continue
        blocks = 2 + index % 3
        block_size = 16 if index < 8 else 32
        heads = 1 if index % 3 else 2
        head_size = 16 if index % 2 == 0 else 32
        case["id"] = index
        case["name"] = "aclnnScatterPaKvCache"
        case["aclnn_name"] = "aclnnScatterPaKvCache"
        case["outputs"] = "keyCacheRef,valueCacheRef"
        case["inputs"] = [
            tensor("key", "bf16", [1, heads, head_size], [-0.25, 0.25]),
            tensor(
                "keyCacheRef", "bf16",
                [blocks, block_size, heads, head_size], [0, 0],
            ),
            tensor("slotMapping", "int64", [1], 0),
            tensor("value", "bf16", [1, heads, head_size], [-0.25, 0.25]),
            tensor(
                "valueCacheRef", "bf16",
                [blocks, block_size, heads, head_size], [0, 0],
            ),
            attr("cacheModeOptional", "string", "Norm"),
            attr("scatterModeOptional", "string", "None"),
            # TTK ACLNN L2 counts all four attributes even when these arrays
            # are semantically inactive outside Nct.
            attr("stridesOptional", "int64", [1, 1], kind="attrs", length=2),
            attr("offsetsOptional", "int64", [1, 1], kind="attrs", length=2),
        ]
        audit.append({
            "id": index,
            "changes": [
                "projected to documented A2 scenario 2",
                "materialized cacheModeOptional=Norm",
                "materialized scatterModeOptional=None",
                "single-token slotMapping fixed to unique value 0",
                "key/value/cache dtype=bf16 and format=ND",
            ],
        })
    return fixed, {
        "operator": "aclnnScatterPaKvCache",
        "mode": "documented_scenario_2_functional_smoke",
        "changed_case_count": sum(bool(item["changes"]) for item in audit),
        "audit": audit,
    }


def _format_tensor_list_shape(shape: list[int] | None) -> str | None:
    """Format a single TensorList element shape for CSV."""
    if shape is None:
        return None
    return repr(tuple(shape))


def _format_tensor_list_shapes(shapes: list[list[int] | None]) -> str:
    """Build nested tuple string for a TensorList param.

    Single sub-tensor:   ``((M, K),)``
    Multiple sub-tensors: ``((M1,K), (M2,K))``
    Absent (None):        ``None``
    """
    filtered = [s for s in shapes if s is not None]
    if not filtered:
        return "None"
    subs = [repr(tuple(s)) for s in filtered]
    if len(subs) == 1:
        # 1-tuple needs the trailing comma
        return f"({subs[0]},)"
    return "(" + ", ".join(subs) + ")"


def _format_single_tensor_shape(shape: list[int] | None) -> str:
    """Format a single (non-TensorList) tensor shape."""
    if shape is None:
        return "None"
    return repr(tuple(shape))


def _format_dtype(dtype_str: str | None) -> str:
    """Map ATK dtype string to TTK dtype string."""
    if dtype_str is None:
        return "None"
    return DTYPE_MAP.get(dtype_str.lower(), dtype_str.lower())


def _format_tensor_format(format_str: str | None) -> str:
    """Return the TTK storage-format token for one tensor."""
    if format_str is None or not str(format_str).strip():
        return "ND"
    return str(format_str).strip().upper()


def _tensor_list_element_count(item: dict[str, Any]) -> int:
    """Return the number of TensorList elements represented by one ATK item."""
    length = item.get("length")
    if isinstance(length, int) and not isinstance(length, bool) and length > 0:
        return length
    shape = item.get("shape")
    if isinstance(shape, list) and shape and isinstance(shape[0], list):
        return len(shape)
    return 1


def _format_attrs(case: dict[str, Any], attr_specs: list[dict[str, str]]) -> dict[str, Any]:
    """Extract non-tensor attributes from ATK case inputs.

    attr_specs: list of {"name": str, "type": str} from signature parsing.
    Missing attrs get type-appropriate defaults via _get_attr_default.
    """
    attrs: dict[str, Any] = {}
    # Build lookup: name -> c_type
    attr_type_map = {spec["name"]: spec.get("type", "") for spec in attr_specs}
    attr_name_set = set(attr_type_map.keys())

    for item in case.get("inputs", []):
        name = item.get("name", "")
        if name not in attr_name_set:
            continue
        value = item.get("range_values")
        length = item.get("length") or 0
        if value is None:
            if (case.get("name") or case.get("aclnn_name")) == "aclnnScatterPaKvCache":
                value = {
                    "cacheModeOptional": "Norm",
                    "scatterModeOptional": "None",
                    "stridesOptional": [1, 1],
                    "offsetsOptional": [1, 1],
                }.get(name)
            if value is None:
                continue
        # attrs 类型参数 (aclIntArray*/aclFloatArray*/aclBoolArray* 等)
        # 必须传数组，不能是单独数值。带 length 的 attrs 按 generator.py 的
        # 展开语义生成 length 元素数组（_expand_attr_range_values）。
        if item.get("type") == "attrs":
            # tuningConfigOptional 保留既有特殊处理：
            # range pair [lo, hi] + length=3 → [lo, hi, -1]
            if name == "tuningConfigOptional":
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], (int, float)):
                    # Range pair [lo, hi] with explicit length
                    if length == 3:
                        value = [value[0], value[1], -1]
                    else:
                        value = list(value)
                elif not isinstance(value, list):
                    value = [value]
            else:
                value = _expand_attr_range_values(item)
        else:
            # 标量 attr（int/double/bool 等）：range_values 为 [lo, hi] 时解析为
            # 具体单值（确定性取下界），避免把取值范围当数组序列化进 CSV，导致
            # TTK phase1 参数构造器 c_type(val) 因 list 报 "must be real number,
            # not list"。数组型 attr 已在上方 "attrs" 分支处理，不受影响。
            value = _resolve_range_value(item)
        attrs[name] = value

    # 签名声明但 case 缺失的参数，根据 C 类型设置合适的默认值
    # (int→0, float→0.0, bool→False, array→[], string→None, etc.)
    for name, c_type in attr_type_map.items():
        if name not in attrs:
            attrs[name] = _get_attr_default(c_type)
    return attrs


# ---------------------------------------------------------------------------
# per-case conversion
# ---------------------------------------------------------------------------
def convert_case(
    case: dict[str, Any],
    case_index: int,
    signature: list[dict[str, Any]] | None = None,
    attr_specs: list[dict[str, str]] | None = None,
) -> dict[str, str]:
    """Convert one ATK compact case to a TTK ACLNN CSV row dict."""
    api_name = case.get("name") or case.get(
        "aclnn_name", "aclnnGroupedMatmulV5",
    )
    # index ATK inputs by name
    by_name: dict[str, dict[str, Any]] = {}
    for item in case.get("inputs", []):
        name = item.get("name", "")
        if name:
            by_name[name] = item

    shapes_parts: list[str] = []
    dtypes_parts: list[str] = []
    formats_parts: list[str] = []
    data_ranges: list[tuple[Any, Any]] = []
    output_indexes: list[int] = []

    signature = signature or _SIGNATURE
    # Convert attr_specs to the format expected by _format_attrs
    if attr_specs is None:
        attr_specs = [{"name": n, "type": ""} for n in sorted(_ATTR_NAMES)]
    for idx, sig in enumerate(signature):
        name = sig["name"]
        is_tensor_list = sig["tensor_list"]
        is_output = sig["output"]
        item = by_name.get(name)

        if item is None or item.get("type") not in ("tensor", "tensors"):
            # absent optional tensor
            shapes_parts.append("None")
            dtypes_parts.append("None")
            formats_parts.append("None")
            data_ranges.append((None, None))
            continue

        shape = deepcopy(item.get("shape"))
        dtype_ttk = _format_dtype(item.get("dtype"))
        format_ttk = _format_tensor_format(item.get("format"))

        if is_tensor_list:
            count = _tensor_list_element_count(item)
            # For TensorList, wrap in nested tuple
            if shape and isinstance(shape, list) and shape and isinstance(shape[0], list):
                # multiple sub-tensors (heterogeneous shapes given verbatim)
                shapes_parts.append(_format_tensor_list_shapes(shape))
            elif shape:
                # ``length`` is the number of sub-tensors in the TensorList
                # (per the ATK expansion semantics in
                # executer/resources/generator.py: a ``tensors`` input with
                # ``length=N`` unfolds into N sub-tensors that each carry the
                # same per-sub-tensor ``shape``). Replicate the single shape
                # ``length`` times so the CSV carries N sub-tensors instead of
                # just one.
                shapes_parts.append(_format_tensor_list_shapes([shape] * count))
            else:
                shapes_parts.append("None")
            dtypes_parts.append(repr(tuple(dtype_ttk for _ in range(count))))
            formats_parts.append(repr(tuple(format_ttk for _ in range(count))))
            # TTK ACLNN 对 None 槽无差别填 (-2,2),不按 dtype/role 挑默认,
            # 会退化 int 权重并丢弃 ATK 写死的 range_values;按顶层参数解析
            # 真实范围(标量→(v,v)、[lo,hi]→(lo,hi)、clamp 到 dtype 界)。
            # 一个 (lo,hi) 应用于该 TensorList 的全部子张量,与 TTK 的
            # per-slot 粒度一致(absent 可选项在上方早退分支已处理为 None)。
            data_ranges.append(_tensor_data_range(item))
        else:
            shapes_parts.append(_format_single_tensor_shape(shape))
            dtypes_parts.append(f"'{dtype_ttk}'")
            formats_parts.append(f"'{format_ttk}'")
            data_ranges.append(_tensor_data_range(item))

        if is_output:
            output_indexes.append(idx)

    # Build the outer tuples
    tensor_view_shapes = f"({','.join(shapes_parts)})"
    tensor_dtypes = f"({','.join(dtypes_parts)})"
    tensor_formats = f"({','.join(formats_parts)})"

    attrs = _format_attrs(case, attr_specs)
    # A8W4 with offset=null is interpreted by CANN as count mode regardless
    # of the incoming attribute.  Keep this compatibility rule narrowly bound
    # to the logical INT8 x + INT4 weight signature: A8W8 must preserve an
    # explicit groupListType=2 so its [groupIdx, groupSize] pairs reach the
    # companion input plugin unchanged.
    attr_name_set = {spec["name"] for spec in attr_specs}
    if (
        api_name == "aclnnGroupedMatmulV5"
        and str((by_name.get("x") or {}).get("dtype") or "").lower() == "int8"
        and str((by_name.get("weight") or {}).get("dtype") or "").lower()
        == "int4"
        and (
            by_name.get("offsetOptional") is None
            or by_name["offsetOptional"].get("type") not in ("tensor", "tensors")
            or by_name["offsetOptional"].get("shape") is None
        )
        and "groupListType" in attr_name_set
    ):
        attrs["groupListType"] = 1
    case_id = int(case.get("id", case_index))

    output_tensor_indexes = repr(tuple(output_indexes)) if output_indexes else "()"

    return {
        "testcase_name": f"{api_name}_{case_id:03d}",
        "api_name": api_name,
        "tensor_view_shapes": tensor_view_shapes,
        "tensor_dtypes": tensor_dtypes,
        "tensor_formats": tensor_formats,
        "attributes": repr(attrs),
        "input_data_ranges": repr(tuple(data_ranges)),
        "output_tensor_indexes": output_tensor_indexes,
        "is_enabled": "True",
        "remark": "converted from ATK compact JSON; TTK ACLNN mode",
    }


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
def audit_case(
    case: dict[str, Any], signature: list[dict[str, Any]] | None = None,
    converted_row: dict[str, str] | None = None,
    attr_specs: list[dict[str, str]] | None = None,
) -> list[str]:
    """Basic sanity checks."""
    issues: list[str] = []
    by_name = {item.get("name"): item for item in case.get("inputs", [])}
    signature = signature or _SIGNATURE
    # Every non-optional entry is checked more precisely by upstream constraint
    # validation.  Here ensure every signature tensor has a generated slot; an
    # optional None tensor is still represented by an item or a CSV None slot.
    for name in (
        entry["name"] for entry in signature
        if not entry["output"] and not entry.get("optional", False)
    ):
        if name not in by_name:
            issues.append(f"missing signature tensor: {name}")
    api_name = case.get("name") or case.get("aclnn_name") or ""
    if not str(api_name).startswith("aclnn"):
        issues.append(f"api_name is not ACLNN: {api_name!r}")
    if converted_row is not None:
        if api_name == "aclnnScatterPaKvCache":
            try:
                converted_attrs = ast.literal_eval(converted_row.get("attributes", ""))
            except (SyntaxError, TypeError, ValueError):
                issues.append("converted attributes is not a valid dict")
            else:
                attr_names = [spec["name"] for spec in (attr_specs or [])]
                missing_attrs = [
                    name for name in attr_names
                    if name not in converted_attrs
                ]
                if missing_attrs:
                    issues.append(
                        "TTK ACLNN L2 requires all ScatterPaKvCache attributes; "
                        f"missing={missing_attrs}"
                    )
        try:
            converted_formats = ast.literal_eval(
                converted_row.get("tensor_formats", "")
            )
        except (SyntaxError, TypeError, ValueError):
            issues.append("converted tensor_formats is not a valid tuple")
        else:
            expected_formats: list[Any] = []
            for entry in signature:
                item = by_name.get(entry["name"])
                if item is None or item.get("type") not in ("tensor", "tensors"):
                    expected_formats.append(None)
                    continue
                token = _format_tensor_format(item.get("format"))
                expected_formats.append(
                    tuple(token for _ in range(_tensor_list_element_count(item)))
                    if entry["tensor_list"] else token
                )
            if tuple(expected_formats) != tuple(converted_formats):
                issues.append(
                    "tensor format loss during conversion: "
                    f"expected={tuple(expected_formats)!r}, got={converted_formats!r}"
                )
    return issues


# ---------------------------------------------------------------------------
# CSV headers (TTK ACLNN ApiTestcaseStructure)
# ---------------------------------------------------------------------------
HEADERS = (
    "testcase_name",
    "api_name",
    "tensor_view_shapes",
    "tensor_dtypes",
    "tensor_formats",
    "attributes",
    "input_data_ranges",
    "precision_tolerances",
    "absolute_precision",
    "output_tensor_indexes",
    "is_enabled",
    "remark",
    "soc_series",
    "priority",
)


# ---------------------------------------------------------------------------
# file-level convert
# ---------------------------------------------------------------------------
def convert_file(
    source: Path,
    destination: Path,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cases = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("TTK_ACLNN_CASES_REQUIRED: source must be a non-empty JSON array")
    signature, attr_specs = _signature_from_constraints(constraints)
    destination.parent.mkdir(parents=True, exist_ok=True)

    rows = [
        convert_case(case, i, signature=signature, attr_specs=attr_specs)
        for i, case in enumerate(cases)
    ]
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    attr_names = [spec["name"] for spec in attr_specs]
    audit = []
    for i, (case, row) in enumerate(zip(cases, rows)):
        null_attrs = [
            item.get("name") for item in case.get("inputs", [])
            if item.get("name") in attr_names and item.get("range_values") is None
        ]
        audit.append({
            "id": case.get("id", i),
            "issues": audit_case(case, signature, row, attr_specs),
            "materialized_default_attributes": null_attrs,
        })
    return {
        "mode": "ttk_aclnn",
        "operator_name": rows[0]["api_name"],
        "source": str(source),
        "destination": str(destination),
        "case_count": len(cases),
        "semantically_clean_count": sum(not entry["issues"] for entry in audit),
        "tensor_signature": signature,
        "attribute_names": attr_names,
        "audit": audit,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert ATK compact JSON to TTK ACLNN CSV"
    )
    parser.add_argument("source", type=Path, help="ATK compact JSON (e.g. aclnnGroupedMatmulV5.json)")
    parser.add_argument("destination", type=Path, help="Output TTK ACLNN CSV")
    parser.add_argument("--constraints", type=Path, help="constraints.json for generic ACLNN signature order")
    parser.add_argument("--audit-output", type=Path, help="Optional JSON audit report")
    args = parser.parse_args()

    constraints = (
        json.loads(args.constraints.read_text(encoding="utf-8"))
        if args.constraints else None
    )
    result = convert_file(args.source, args.destination, constraints=constraints)
    if args.audit_output:
        args.audit_output.parent.mkdir(parents=True, exist_ok=True)
        args.audit_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()