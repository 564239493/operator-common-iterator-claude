#!/usr/bin/env python3
"""Inject operator-specific auxiliary parameters into generated cases.

Knowledge directories may contain an ``aux_params.json`` next to the
operator markdown. It declares parameters that are not part of the ACLNN
interface but are needed by the test harness, such as ``groupListData``.

The injector scans ``knowledge/aclnn/operators/**/aux_params.json`` for the
current operator and appends an auxiliary input entry to every generated case.
The value is stored as a JSON string in ``range_values`` so it survives the
case model unchanged until the TTK adapter emits it into ``attributes``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts.materialize_group_list_data import _build_group_list, _first_shape
except ModuleNotFoundError:  # pragma: no cover - alternate package path
    from materialize_group_list_data import _build_group_list, _first_shape


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KNOWLEDGE_ROOT = ROOT / "knowledge" / "aclnn" / "operators"


def _case_input(case: dict[str, Any], name: str) -> dict[str, Any] | None:
    for item in case.get("inputs", []) or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
        if isinstance(item, list) and item and isinstance(item[0], dict):
            if item[0].get("name") == name:
                return item[0]
    return None


def _scalar_attr(case: dict[str, Any], name: str) -> int | None:
    item = _case_input(case, name)
    if not isinstance(item, dict):
        return None
    value = item.get("range_values")
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _shape_of(case: dict[str, Any], name: str) -> list[int] | None:
    item = _case_input(case, name)
    if not isinstance(item, dict):
        return None
    return _first_shape(item.get("shape"))


def _derive_m_and_g(case: dict[str, Any], group_type: int) -> tuple[int, int]:
    weight_shape = _shape_of(case, "weight")
    if weight_shape is None:
        raise ValueError("weight shape is missing; cannot derive groupListData")
    g = int(weight_shape[0])
    if group_type == 0:
        x_shape = _shape_of(case, "x")
        if x_shape is None:
            raise ValueError("x shape is missing for groupType=0")
        m = int(x_shape[0])
    elif group_type == 2:
        out_shape = _shape_of(case, "out")
        x_shape = _shape_of(case, "x")
        if out_shape is not None and len(out_shape) >= 2:
            m = int(out_shape[1])
        elif x_shape is not None and len(x_shape) >= 2:
            m = int(x_shape[1])
        elif x_shape is not None and len(x_shape) >= 1:
            m = int(x_shape[0])
        else:
            raise ValueError("out/x shape is missing for groupType=2")
    else:
        raise ValueError(f"unsupported groupType={group_type}")
    if m < 0 or g <= 0:
        raise ValueError(f"invalid M={m} or g={g}")
    return m, g


def _build_value(case: dict[str, Any], spec: dict[str, Any]) -> str | None:
    if spec.get("generator") != "group_list_data":
        raise ValueError(
            f"unsupported auxiliary parameter generator: {spec.get('generator')}"
        )
    group_type = _scalar_attr(case, "groupType")
    group_list_type = _scalar_attr(case, "groupListType")
    if group_type is None or group_list_type is None:
        return None
    if group_type not in (0, 2):
        return None
    m, g = _derive_m_and_g(case, group_type)
    data = _build_group_list(group_type, group_list_type, m, g)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _aux_input(spec: dict[str, Any], value: str) -> dict[str, Any]:
    return {
        "name": spec["name"],
        "type": spec.get("type", "attr"),
        "aux": True,
        "required": spec.get("required", False),
        "dtype": spec.get("dtype", "string"),
        "shape": None,
        "range_values": value,
        "length": None,
        "format": None,
        "backward": False,
        "align_32B": None,
        "outlier_values": None,
        "is_transpose": False,
        "transpose_id": None,
        "storage_shape": None,
    }


def find_aux_specs(
    operator_name: str,
    knowledge_root: Path | None = None,
) -> list[dict[str, Any]]:
    root = Path(knowledge_root or DEFAULT_KNOWLEDGE_ROOT)
    if not root.is_dir():
        return []
    specs: list[dict[str, Any]] = []
    for path in sorted(root.rglob("aux_params.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("operator_name") != operator_name:
            continue
        for spec in payload.get("aux_params", []) or []:
            if not isinstance(spec, dict) or not spec.get("name"):
                continue
            specs.append({**spec, "source_path": str(path)})
    return specs


def inject_aux_params(
    cases: list[dict[str, Any]],
    operator_name: str,
    knowledge_root: Path | None = None,
) -> dict[str, Any]:
    specs = find_aux_specs(operator_name, knowledge_root)
    if not specs:
        return {
            "operator_name": operator_name,
            "specs": [],
            "updated_cases": 0,
            "updated_params": 0,
        }
    updated_cases = 0
    updated_params = 0
    for case in cases:
        case_changed = False
        for spec in specs:
            value = _build_value(case, spec)
            if value is None:
                continue
            case["inputs"] = [
                item for item in (case.get("inputs") or [])
                if not (
                    isinstance(item, dict)
                    and item.get("name") == spec["name"]
                )
            ]
            case.setdefault("inputs", []).append(_aux_input(spec, value))
            updated_params += 1
            case_changed = True
        if case_changed:
            updated_cases += 1
    return {
        "operator_name": operator_name,
        "specs": specs,
        "updated_cases": updated_cases,
        "updated_params": updated_params,
    }


def inject_aux_params_into_file(
    path: Path,
    operator_name: str,
    knowledge_root: Path | None = None,
) -> dict[str, Any]:
    path = Path(path)
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError(f"{path}: cases must be a JSON array")
    report = inject_aux_params(cases, operator_name, knowledge_root)
    if report["updated_params"]:
        temporary = path.with_name(f".{path.name}.aux.tmp")
        temporary.write_text(
            json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    report["path"] = str(path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inject knowledge-declared auxiliary parameters into cases.json."
    )
    parser.add_argument("--cases", required=True)
    parser.add_argument("--operator-name", required=True)
    parser.add_argument("--knowledge-root")
    args = parser.parse_args()
    report = inject_aux_params_into_file(
        Path(args.cases).expanduser().resolve(),
        args.operator_name,
        Path(args.knowledge_root).expanduser().resolve()
        if args.knowledge_root else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
