"""Scenario planning for HiSilicon ``torch_npu`` operators.

The retained generator is still responsible for producing concrete cases.  This
module only partitions the requested case budget and pins the small set of
layout attributes that define mutually exclusive HS execution scenarios.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any


_SPARSE_FAMILY = {
    "torch_npu.npu_lightning_indexer",
    "torch_npu.npu_quant_lightning_indexer",
    "torch_npu.npu_sparse_flash_attention",
    "torch_npu.npu_kv_quant_sparse_flash_attention",
}


# combine-mode (quant_scale_repo_mode=1) packs key/value's int8 D=656 dimension
# as three byte segments: [0,512) int8 k_nope | [512,640) k_rope bytes |
# [640,656) float32 antiquant_scale (4 elements). The last 16 bytes are
# reinterpreted by the NPU kernel as float32 antiquant_scale, so a uniform
# random int8 fill (e.g. [-8,8]) produces random bit patterns whose float32
# reinterpretation is frequently NaN/Inf -> NPU dequant garbage scale ->
# attention all NaN (the iter_001 failure). The TTK E2E CSV is range-only
# (one (min,max) tuple per tensor) and has no input-preparation hook in the
# plugin contract, so it CANNOT express a per-segment byte layout. The best
# range-only mitigation is a FIXED positive int8 byte whose 4-byte float32
# reinterpretation is a finite positive number. For byte b in [0,127] the
# float32 exponent field is ((b & 0x7F) << 1) <= 254 < 255, so it is never
# NaN/Inf. b=63 (0x3F) reinterprets as float32 0x3F3F3F3F ~= 0.7476, the
# uniform-byte value closest to 1.0. This avoids NaN but does NOT equal the
# golden's hardcoded antiquant_scale=1.0, so precision will still mismatch
# the canonical golden until a literal byte-packing tensor builder exists.
# Tracked as generator_bug: combine-mode byte-packing constructor missing.
_KV_QUANT_BYTE_SAFE_FILL = 63


@dataclass(frozen=True)
class HSScenario:
    name: str
    fixed_attrs: dict[str, Any]
    count: int = 0


def _field_value(field: Any) -> Any:
    return field.get("value") if isinstance(field, dict) else field


def _platform_attributes(
    constraints: dict[str, Any], param: str, platform: str | None = None
) -> dict[str, Any] | None:
    raw = (constraints.get("inputs") or {}).get(param)
    if not isinstance(raw, dict):
        return None
    if "type" in raw:
        return raw
    if platform and isinstance(raw.get(platform), dict):
        return raw[platform]
    return next((value for value in raw.values() if isinstance(value, dict)), None)


def _domain(
    constraints: dict[str, Any], param: str, platform: str | None = None
) -> set[Any]:
    attrs = _platform_attributes(constraints, param, platform)
    if not attrs:
        return set()
    value = _field_value(attrs.get("allowed_range_value"))
    if isinstance(value, list):
        return set(value)
    return {value} if value is not None else set()


def _dtype_domain(
    constraints: dict[str, Any], param: str, platform: str | None = None
) -> tuple[str, ...]:
    attrs = _platform_attributes(constraints, param, platform) or {}
    value = _field_value(attrs.get("dtype"))
    values = value if isinstance(value, list) else [value]
    aliases = {"float16": "fp16", "bfloat16": "bf16"}
    return tuple(
        aliases.get(str(item).lower(), str(item).lower())
        for item in values if item is not None
    )


def _platform_relations(
    constraints: dict[str, Any], platform: str | None
) -> list[dict[str, Any]]:
    raw = constraints.get("constraints_in_parameters") or {}
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    if platform and isinstance(raw.get(platform), list):
        return [item for item in raw[platform] if isinstance(item, dict)]
    return next(
        ([item for item in values if isinstance(item, dict)]
         for values in raw.values() if isinstance(values, list)),
        [],
    )


def _query_head_domain(
    constraints: dict[str, Any], platform: str | None
) -> tuple[int, ...]:
    values: set[int] = set()
    pattern = re.compile(r"query\.shape\[\d+\]\s+in\s+\[([^\]]+)\]")
    for relation in _platform_relations(constraints, platform):
        for match in pattern.finditer(str(relation.get("expr", ""))):
            values.update(int(value) for value in re.findall(r"\d+", match.group(1)))
    return tuple(sorted(values))


def hs_coverage_domains(
    constraints: dict[str, Any], platform: str | None = None
) -> dict[str, tuple[Any, ...]]:
    """Return executable coverage domains without consulting generic generators."""
    operator = str(constraints.get("operator_name", ""))
    if operator != "torch_npu.npu_kv_quant_sparse_flash_attention":
        return {}
    return {
        "query_dtype": _dtype_domain(constraints, "query", platform) or ("fp16",),
        "query_heads": _query_head_domain(constraints, platform)
        or (1, 2, 4, 8, 16, 32, 64, 128),
        "sparse_block_size": tuple(sorted(_domain(
            constraints, "sparse_block_size", platform
        ))) or (1, 2, 4, 8, 16),
        "sparse_mode": tuple(sorted(_domain(
            constraints, "sparse_mode", platform
        ))) or (0, 3),
        # These are documented PA boundaries/representatives, not a replacement
        # for the extracted relation "multiple of 16 and <= 1024".
        "pa_block_size": (16, 32, 64, 1024),
    }


def plan_hs_scenarios(
    constraints: dict[str, Any], count: int, platform: str | None = None
) -> list[HSScenario]:
    """Return deterministic, budgeted layout scenarios.

    Operators without a known scenario family retain the existing one-shot
    generation behaviour.
    """
    if count < 1:
        return []
    operator = constraints.get("operator_name")
    if operator not in _SPARSE_FAMILY:
        return [HSScenario("default", {}, count)]

    kv_layout_name = "layout_key" if "indexer" in str(operator) else "layout_kv"
    query_domain = _domain(constraints, "layout_query", platform)
    kv_domain = _domain(constraints, kv_layout_name, platform)
    candidates: list[HSScenario] = []
    if "TND" in query_domain and "TND" in kv_domain:
        candidates.append(HSScenario(
            "tnd", {"layout_query": "TND", kv_layout_name: "TND"}
        ))
    if "BSND" in query_domain and "BSND" in kv_domain:
        candidates.append(HSScenario(
            "bsnd", {"layout_query": "BSND", kv_layout_name: "BSND"}
        ))
    if "BSND" in query_domain and "PA_BSND" in kv_domain:
        candidates.append(HSScenario(
            "paged_attention",
            {"layout_query": "BSND", kv_layout_name: "PA_BSND"},
        ))
    if not candidates:
        return [HSScenario("default", {}, count)]

    # When fewer cases than scenarios are requested, select scenarios in the
    # stable order above. Otherwise give every scenario at least one case.
    active = candidates[:count]
    base, remainder = divmod(count, len(active))
    return [
        HSScenario(item.name, item.fixed_attrs, base + (index < remainder))
        for index, item in enumerate(active)
    ]


def pin_scenario_constraints(
    constraints: dict[str, Any], scenario: HSScenario
) -> dict[str, Any]:
    """Clone constraints and pin scenario-driving scalar domains."""
    pinned = deepcopy(constraints)
    for param, value in scenario.fixed_attrs.items():
        raw = (pinned.get("inputs") or {}).get(param)
        if not isinstance(raw, dict):
            continue
        platform_values = [raw] if "type" in raw else list(raw.values())
        for attrs in platform_values:
            if not isinstance(attrs, dict):
                continue
            field = attrs.get("allowed_range_value")
            if isinstance(field, dict):
                field["value"] = [value]
                field["type"] = "enum"
            else:
                attrs["allowed_range_value"] = {
                    "value": [value], "src_text": "HS scenario pin", "type": "enum"
                }
    return pinned


def _case_item(case: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next(
        (
            item for item in case.get("inputs", [])
            if isinstance(item, dict) and item.get("name") == name
        ),
        None,
    )


def _upsert_case_item(
    case: dict[str, Any], name: str, kind: str, dtype: str
) -> dict[str, Any]:
    item = _case_item(case, name)
    if item is None:
        item = {
            "name": name,
            "type": kind,
            "dtype": dtype,
            "shape": None,
            "range_values": None,
            "length": None,
            "format": "ND" if kind == "tensor" else None,
            "backward": False,
            "align_32B": None,
            "outlier_values": None,
        }
        case.setdefault("inputs", []).append(item)
    item["type"] = kind
    item["dtype"] = dtype
    return item


def _set_tensor(
    case: dict[str, Any],
    name: str,
    dtype: str,
    shape: list[int] | None,
    values: Any,
    *,
    required: bool = False,
) -> None:
    item = _upsert_case_item(case, name, "tensor", dtype)
    item.update({
        "required": required,
        "shape": shape,
        "range_values": values,
        "format": "ND",
        "length": None,
    })


def _set_attr(case: dict[str, Any], name: str, dtype: str, value: Any) -> None:
    item = _upsert_case_item(case, name, "attr", dtype)
    item.update({"shape": None, "range_values": value, "length": None})


def _project_kv_quant_sparse_flash_attention(
    case: dict[str, Any], scenario: HSScenario, ordinal: int,
    constraints: dict[str, Any] | None = None, platform: str | None = None,
) -> dict[str, Any]:
    """Project a random retained-generator sample onto one complete legal scene.

    The generic generator can sample independent ranks and dimensions, but this
    operator's layouts define mutually exclusive OR-of-AND shape/presence rules.
    Keep the generic sample as the source case while deterministically projecting
    only the documented correlated fields required for a runnable TTK case.
    """
    if scenario.name not in {"tnd", "bsnd", "paged_attention"}:
        return deepcopy(case)
    projected = deepcopy(case)
    domains = hs_coverage_domains(constraints or {}, platform)
    query_dtypes = domains.get("query_dtype", ("fp16",))
    query_heads_domain = domains.get("query_heads", (1, 2, 4, 8, 16, 32, 64, 128))
    sparse_block_domain = domains.get("sparse_block_size", (1, 2, 4, 8, 16))
    sparse_modes = domains.get("sparse_mode", (0, 3))
    query_dtype = str(query_dtypes[ordinal % len(query_dtypes)])
    query_heads = int(query_heads_domain[ordinal % len(query_heads_domain)])
    sparse_block_size = int(sparse_block_domain[ordinal % len(sparse_block_domain)])
    sparse_mode = int(sparse_modes[ordinal % len(sparse_modes)])
    query_shape: list[int]
    key_shape: list[int]
    sparse_shape: list[int]

    if scenario.name == "tnd":
        # A single logical batch makes the generated exact tensor range a valid
        # one-element prefix sum instead of relying on random tensor contents.
        query_tokens = 2 + ordinal
        kv_tokens = max(query_tokens, 4 + ordinal * 2)
        query_shape = [query_tokens, query_heads, 576]
        key_shape = [kv_tokens, 1, 656]
        sparse_shape = [query_tokens, 1, 1]
        _set_tensor(
            projected, "actual_seq_lengths_query", "int32", [1],
            [query_tokens, query_tokens], required=True,
        )
        _set_tensor(
            projected, "actual_seq_lengths_kv", "int32", [1],
            [kv_tokens, kv_tokens], required=True,
        )
        _set_tensor(projected, "block_table", "int32", None, ["null"])
    elif scenario.name == "paged_attention":
        # Keep B=1 so actual lengths can be represented exactly by the current
        # range-based concrete case model. The block mapping still varies by
        # block_size/block_num and exercises the PA-specific tensor slots.
        batch = 1
        query_sequence = 1 + ordinal % 4
        pa_block_sizes = domains.get("pa_block_size", (16, 32, 64, 1024))
        block_size = int(pa_block_sizes[ordinal % len(pa_block_sizes)])
        block_num = 2 + ordinal % 3
        kv_tokens = block_num * block_size
        query_shape = [batch, query_sequence, query_heads, 576]
        key_shape = [block_num, block_size, 1, 656]
        sparse_shape = [batch, query_sequence, 1, 1]
        _set_tensor(
            projected, "block_table", "int32", [batch, block_num],
            [0, block_num - 1], required=True,
        )
        _set_tensor(
            projected, "actual_seq_lengths_query", "int32", [batch],
            [query_sequence, query_sequence],
        )
        _set_tensor(
            projected, "actual_seq_lengths_kv", "int32", [batch],
            [kv_tokens, kv_tokens], required=True,
        )
    else:  # bsnd
        batch = 1 + ordinal % 2
        query_sequence = 1 + ordinal % 4
        kv_sequence = max(4, query_sequence + 3 + ordinal)
        query_shape = [batch, query_sequence, query_heads, 576]
        key_shape = [batch, kv_sequence, 1, 656]
        sparse_shape = [batch, query_sequence, 1, 1]
        _set_tensor(projected, "block_table", "int32", None, ["null"])
        _set_tensor(
            projected, "actual_seq_lengths_query", "int32", None, ["null"]
        )
        _set_tensor(
            projected, "actual_seq_lengths_kv", "int32", None, ["null"]
        )

    _set_tensor(projected, "query", query_dtype, query_shape, [-1.0, 1.0], required=True)
    # combine-mode key/value: D=656 = 512 int8 k_nope + 128 rope bytes + 16
    # float32 antiquant_scale bytes. The TTK E2E CSV is range-only (one
    # (min,max) per tensor) and exposes no input-preparation hook, so a true
    # torch.cat byte-packing cannot be expressed. Use a FIXED positive int8
    # byte (63) so the trailing 16 bytes reinterpret as float32 ~0.7476
    # (finite positive, NaN-free) instead of the iter_001 random-fill NaN.
    # See _KV_QUANT_BYTE_SAFE_FILL for the byte-safety rationale. This is a
    # NaN-avoidance mitigation, NOT true three-segment packing (generator_bug).
    kv_fill = [_KV_QUANT_BYTE_SAFE_FILL, _KV_QUANT_BYTE_SAFE_FILL]
    _set_tensor(projected, "key", "int8", key_shape, kv_fill, required=True)
    _set_tensor(projected, "value", "int8", key_shape, kv_fill, required=True)
    _set_tensor(
        projected, "sparse_indices", "int32", sparse_shape, [0, 0], required=True
    )
    # Reserved tensor slots must remain explicit None placeholders so TTK's
    # positional Tensor arguments do not shift.
    _set_tensor(projected, "key_dequant_scale", "float16", None, ["null"])
    _set_tensor(projected, "value_dequant_scale", "float16", None, ["null"])

    _set_attr(projected, "scale_value", "double", 1 / (512 ** 0.5))
    _set_attr(projected, "key_quant_mode", "int64", 2)
    _set_attr(projected, "value_quant_mode", "int64", 2)
    _set_attr(projected, "sparse_block_size", "int64", sparse_block_size)
    _set_attr(projected, "layout_query", "string", scenario.fixed_attrs["layout_query"])
    _set_attr(projected, "layout_kv", "string", scenario.fixed_attrs["layout_kv"])
    _set_attr(projected, "sparse_mode", "int64", sparse_mode)
    _set_attr(projected, "pre_tokens", "int64", (1 << 63) - 1)
    _set_attr(projected, "next_tokens", "int64", (1 << 63) - 1)
    _set_attr(projected, "attention_mode", "int64", 2)
    _set_attr(projected, "quant_scale_repo_mode", "int64", 1)
    _set_attr(projected, "tile_size", "int64", 128)
    _set_attr(projected, "rope_head_dim", "int64", 64)

    output_names = {
        part.strip() for part in str(projected.get("outputs") or "out").split(",")
        if part.strip()
    }
    for output_name in output_names:
        _set_tensor(
            projected, output_name, query_dtype, list(query_shape), [-1.0, 1.0],
            required=True,
        )
    return projected


def project_hs_case(
    case: dict[str, Any], operator_name: str, scenario: HSScenario, ordinal: int,
    constraints: dict[str, Any] | None = None, platform: str | None = None,
) -> dict[str, Any]:
    """Apply an operator-specific complete scenario projection when available."""
    if operator_name == "torch_npu.npu_kv_quant_sparse_flash_attention":
        return _project_kv_quant_sparse_flash_attention(
            case, scenario, ordinal, constraints, platform
        )
    return deepcopy(case)


def classify_case_scenario(case: dict[str, Any]) -> str:
    values = {
        item.get("name"): item.get("range_values")
        for item in case.get("inputs", [])
        if item.get("type") in {"attr", "attrs"}
    }
    query = values.get("layout_query")
    kv = values.get("layout_kv", values.get("layout_key"))
    if query == "TND" and kv == "TND":
        return "tnd"
    if query == "BSND" and kv == "PA_BSND":
        return "paged_attention"
    if query == "BSND" and kv == "BSND":
        return "bsnd"
    return "other"
