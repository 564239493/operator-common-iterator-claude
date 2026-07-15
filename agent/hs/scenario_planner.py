"""Scenario planning for HiSilicon ``torch_npu`` operators.

The retained generator is still responsible for producing concrete cases.  This
module only partitions the requested case budget and pins the small set of
layout attributes that define mutually exclusive HS execution scenarios.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


_SPARSE_FAMILY = {
    "torch_npu.npu_lightning_indexer",
    "torch_npu.npu_quant_lightning_indexer",
    "torch_npu.npu_sparse_flash_attention",
    "torch_npu.npu_kv_quant_sparse_flash_attention",
}


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
