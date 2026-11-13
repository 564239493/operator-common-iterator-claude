#!/usr/bin/env python3
"""Detect and inject operator auxiliary parameters for TTK cases.

Auxiliary parameters are values the harness needs but the ACLNN interface does
not declare, such as ``groupListData`` for ``aclnnGroupedMatmulV5``. They are
discovered from the operator document/knowledge and written to a run-local
snapshot:

    <run>/inputs/aux_params.json

The run-local snapshot is the only source used by the generator. No per-run
file is written into ``knowledge/``. The project knowledge directory is only
read for detection evidence.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.materialize_group_list_data import _first_shape
except ModuleNotFoundError:  # pragma: no cover - alternate package path
    from materialize_group_list_data import _first_shape


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KNOWLEDGE_ROOT = ROOT / "knowledge" / "aclnn" / "operators"
VALUE_KEYWORDS = (
    "非负",
    "单调",
    "非递减",
    "单调不减",
    "递增",
    "递减",
    "累加",
    "前缀和",
    "cumsum",
    "有序",
    "排序",
    "唯一",
    "互异",
    "连续",
    "序列",
)


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


def _string_attr(case: dict[str, Any], name: str) -> str | None:
    item = _case_input(case, name)
    if not isinstance(item, dict):
        return None
    value = item.get("range_values")
    if isinstance(value, list):
        value = value[0] if value else None
    return None if value is None else str(value)


def _aux_values(case: dict[str, Any], name: str) -> list[int] | None:
    item = _case_input(case, name)
    if not isinstance(item, dict):
        return None
    value = item.get("range_values")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                return None
    if not isinstance(value, list) or not value:
        return None
    try:
        return [int(item) for item in value]
    except (TypeError, ValueError):
        return None


def _shape_of(case: dict[str, Any], name: str) -> list[int] | None:
    item = _case_input(case, name)
    if not isinstance(item, dict):
        return None
    return _first_shape(item.get("shape"))


def _all_shapes(case: dict[str, Any], name: str) -> list[list[int]]:
    item = _case_input(case, name)
    if not isinstance(item, dict):
        return []
    shape = item.get("shape")
    if isinstance(shape, list) and shape and isinstance(shape[0], list):
        return [list(sub) for sub in shape if isinstance(sub, list)]
    first = _first_shape(shape)
    if first is None:
        return []
    length = item.get("length")
    if isinstance(length, int) and not isinstance(length, bool) and length > 1:
        return [list(first) for _ in range(length)]
    return [first]


def _build_from_counts(
    counts: list[int], group_type: int, group_list_type: int,
) -> list[Any]:
    if group_list_type == 0:
        data: list[Any] = []
        running = 0
        for count in counts:
            running += count
            data.append(running)
        return data
    if group_list_type == 1:
        return list(counts)
    if group_list_type == 2 and group_type == 0:
        return [[index, count] for index, count in enumerate(counts)]
    raise ValueError(
        f"unsupported groupType/groupListType: {group_type}/{group_list_type}"
    )


def _build_value(case: dict[str, Any], spec: dict[str, Any]) -> str | None:
    generator = spec.get("generator", "group_list_data")
    if generator == "group_list_data":
        group_type = _scalar_attr(case, "groupType")
        group_list_type = _scalar_attr(case, "groupListType")
        if group_type is None or group_list_type is None:
            return None
        if group_type not in (0, 2):
            return None
        target_param = spec.get("target_param", "groupListOptional")
        target_shape = _shape_of(case, target_param)
        if target_shape is None:
            return None
        g = int(target_shape[0])
        x_shapes = _all_shapes(case, "x")
        if not x_shapes:
            return None
        if len(x_shapes) > 1:
            if group_type != 0:
                raise ValueError(
                    "multi-TensorList groupListData is only supported for groupType=0"
                )
            if g != len(x_shapes):
                raise ValueError(
                    f"{target_param} length {g} must equal x TensorList length "
                    f"{len(x_shapes)}"
                )
            counts = [int(shape[0]) for shape in x_shapes]
        else:
            if group_type == 0:
                m = int(x_shapes[0][0])
            else:
                out_shape = _shape_of(case, "out")
                if out_shape is not None and len(out_shape) >= 2:
                    m = int(out_shape[1])
                elif len(x_shapes[0]) >= 2:
                    m = int(x_shapes[0][1])
                else:
                    m = int(x_shapes[0][0])
            counts = [
                m // g + (1 if index < m % g else 0)
                for index in range(g)
            ]
        data = _build_from_counts(counts, group_type, group_list_type)
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if generator == "non_negative_monotonic":
        options = spec.get("generator_options") or {}
        source = options.get("length_from") or spec.get("target_param")
        shape = _shape_of(case, source) if source else None
        if not shape:
            return None
        length = int(shape[0])
        max_value = options.get("max_value")
        if isinstance(max_value, str):
            max_value = _scalar_attr(case, max_value)
        data = list(range(length)) if max_value is None else [
            (max_value * index) // max(length - 1, 1) for index in range(length)
        ]
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if generator == "prefix_sum_prefix0":
        options = spec.get("generator_options") or {}
        target = options.get("length_from") or spec.get("target_param")
        target_shape = _shape_of(case, target) if target else None
        if not target_shape:
            return None
        length = int(target_shape[0])
        offset = int(options.get("length_offset") or 1)
        batch = length - offset
        if batch <= 0:
            return None
        total_param = options.get("total_from")
        total_shape = _shape_of(case, total_param) if total_param else None
        if not total_shape:
            return None
        layout = _string_attr(case, options.get("layout_param") or "")
        if layout == "TND":
            total = int(total_shape[0])
        elif len(total_shape) >= 2:
            total = int(total_shape[1])
        else:
            total = int(total_shape[0])
        base, remainder = divmod(total, batch)
        lengths = [
            base + (1 if index < remainder else 0) for index in range(batch)
        ]
        values = [0]
        running = 0
        for item in lengths:
            running += item
            values.append(running)
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    if generator == "within_prefix":
        options = spec.get("generator_options") or {}
        target = options.get("length_from") or spec.get("target_param")
        target_shape = _shape_of(case, target) if target else None
        if not target_shape:
            return None
        length = int(target_shape[0])
        ref = _aux_values(case, options.get("ref_attr") or "")
        if ref and len(ref) == length + 1:
            values = [int(ref[index + 1]) - int(ref[index]) for index in range(length)]
        else:
            default = int(options.get("default_value") or 1)
            values = [max(0, default) for _ in range(length)]
        max_from = options.get("max_from")
        if max_from:
            max_shape = _shape_of(case, max_from)
            if max_shape:
                capacity = 1
                for dim in max_shape:
                    capacity *= int(dim)
                values = [min(value, capacity) for value in values]
        budget_from = options.get("block_budget_from")
        if budget_from:
            layout = _string_attr(case, options.get("block_layout_param") or "")
            expected = options.get("block_layout_value")
            if expected is None or layout == expected:
                budget_shape = _shape_of(case, budget_from)
                num_dim = int(options.get("block_num_dim") or 0)
                size_dim = int(options.get("block_size_dim") or 1)
                if (
                    budget_shape
                    and len(budget_shape) > max(num_dim, size_dim)
                    and values
                ):
                    block_num = int(budget_shape[num_dim])
                    block_size = int(budget_shape[size_dim])
                    if block_num >= 0 and block_size > 0:
                        count = len(values)
                        base_blocks, extra = divmod(block_num, count)
                        allowed = [
                            (base_blocks + (1 if index < extra else 0))
                            * block_size
                            for index in range(count)
                        ]
                        values = [
                            min(value, limit)
                            for value, limit in zip(values, allowed)
                        ]
        values = [max(0, value) for value in values]
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    if generator == "lt_scalar":
        options = spec.get("generator_options") or {}
        when_attr = options.get("when_attr")
        when_value = options.get("when_value")
        if when_attr is not None and when_value is not None:
            actual = _scalar_attr(case, when_attr)
            if actual != when_value:
                return None
        ratio = _scalar_attr(case, options.get("ratio_attr") or "")
        if ratio is None or ratio <= 1:
            return None
        target = options.get("length_from") or spec.get("target_param")
        target_shape = _shape_of(case, target) if target else None
        if not target_shape:
            return None
        length = int(target_shape[0])
        values = [index % ratio for index in range(length)]
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    raise ValueError(f"unsupported auxiliary parameter generator: {generator}")


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


def _attribute_type_text(attributes: Any) -> str:
    if not isinstance(attributes, dict):
        return ""
    value = attributes.get("type")
    if isinstance(value, dict):
        value = value.get("value")
    return str(value or "")


def _tensor_param_names(constraints: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for name, cards in (constraints.get("inputs") or {}).items():
        values = (
            [_attribute_type_text(card) for card in cards.values()]
            if isinstance(cards, dict)
            else [_attribute_type_text(cards)]
        )
        if any("acltensor" in value.lower() for value in values):
            names.append(name)
    return names


def _tensor_slot_index(constraints: dict[str, Any], param_name: str) -> int | None:
    signature = str(constraints.get("function_signature") or "")
    if "(" not in signature or ")" not in signature:
        return None
    params = signature.split("(", 1)[1].rsplit(")", 1)[0].split(",")
    index = 0
    for param in params:
        if "acltensor" not in param.lower():
            continue
        match = re.search(r"([A-Za-z_]\w*)\s*$", param.strip())
        if not match:
            continue
        name = match.group(1)
        if name == param_name:
            return index
        index += 1
    return None


def _signature_index(constraints: dict[str, Any], param_name: str) -> int | None:
    """Position of ``param_name`` in the full ACLNN signature (all params)."""
    signature = str(constraints.get("function_signature") or "")
    if "(" not in signature or ")" not in signature:
        return None
    params = signature.split("(", 1)[1].rsplit(")", 1)[0].split(",")
    for index, param in enumerate(params):
        match = re.search(r"([A-Za-z_]\w*)\s*$", param.strip())
        if match and match.group(1) == param_name:
            return index
    return None


def _knowledge_texts(
    operator_name: str, knowledge_root: Path | None = None,
) -> list[tuple[Path, str]]:
    root = Path(knowledge_root or DEFAULT_KNOWLEDGE_ROOT)
    if not root.is_dir():
        return []
    texts: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if operator_name in text:
            texts.append((path, text))
    return texts


def _excerpt(text: str, start: int, end: int, radius: int = 160) -> str:
    return re.sub(
        r"\s+", " ", text[max(0, start - radius): end + radius],
    ).strip()


def detect_aux_specs(
    operator_name: str,
    doc_path: Path,
    constraints: dict[str, Any],
    knowledge_root: Path | None = None,
) -> dict[str, Any]:
    doc_path = Path(doc_path)
    doc_text = doc_path.read_text(encoding="utf-8", errors="replace")
    knowledge_files = _knowledge_texts(operator_name, knowledge_root)
    combined = "\n".join([doc_text, *(text for _, text in knowledge_files)])
    tensor_names = _tensor_param_names(constraints)
    aux_params: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    if operator_name == "aclnnGroupedMatmulV5" and "groupListOptional" in tensor_names:
        hits: list[str] = []
        evidence = ""
        for keyword in ("groupListType", "非负", "单调", "累加", "cumsum", "前缀和"):
            position = combined.find(keyword)
            if position >= 0:
                hits.append(keyword)
                if not evidence:
                    evidence = _excerpt(
                        combined, position, position + len(keyword),
                    )
        if hits:
            index = _tensor_slot_index(constraints, "groupListOptional")
            candidates.append({
                "param_name": "groupListOptional",
                "aux_attr": "groupListData",
                "keywords": sorted(set(hits)),
                "tensor_index": index,
                "evidence": evidence,
                "selected": True,
            })
            aux_params.append({
                "name": "groupListData",
                "type": "attr",
                "aux": True,
                "dtype": "string",
                "required": False,
                "target_param": "groupListOptional",
                "tensor_index": index if index is not None else 8,
                "signature_index": _signature_index(
                    constraints, "groupListOptional",
                ),
                "generator": "group_list_data",
                "validator": "group_list_data",
                "generator_options": {
                    "x_param": "x",
                    "weight_param": "weight",
                    "out_param": "out",
                    "group_type_attr": "groupType",
                    "group_list_type_attr": "groupListType",
                },
                "evidence": sorted(set(hits)),
            })

    selected_targets = {spec["target_param"] for spec in aux_params}
    for name in tensor_names:
        if name in selected_targets:
            continue
        for keyword in VALUE_KEYWORDS:
            for match in re.finditer(re.escape(keyword), combined):
                window = combined[max(0, match.start() - 250): match.end() + 250]
                if name not in window:
                    continue
                candidates.append({
                    "param_name": name,
                    "aux_attr": f"{name}Data",
                    "keywords": [keyword],
                    "tensor_index": _tensor_slot_index(constraints, name),
                    "signature_index": _signature_index(constraints, name),
                    "evidence": _excerpt(combined, match.start(), match.end()),
                    "selected": False,
                })
                break

    return {
        "schema_version": "1.1",
        "operator_name": operator_name,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "doc": str(doc_path.resolve()),
            "knowledge_files": [str(path.resolve()) for path, _ in knowledge_files],
        },
        "aux_params": aux_params,
        "candidates": candidates,
    }


def load_specs_file(
    path: Path, operator_name: str,
) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("operator_name") != operator_name:
        return []
    return [
        {**spec, "source_path": str(path)}
        for spec in payload.get("aux_params", []) or []
        if isinstance(spec, dict) and spec.get("name")
    ]


def find_aux_specs(
    operator_name: str,
    knowledge_root: Path | None = None,
) -> list[dict[str, Any]]:
    root = Path(knowledge_root or DEFAULT_KNOWLEDGE_ROOT)
    if not root.is_dir():
        return []
    specs: list[dict[str, Any]] = []
    for path in sorted(root.rglob("aux_params.json")):
        specs.extend(load_specs_file(path, operator_name))
    return specs


def inject_aux_params(
    cases: list[dict[str, Any]],
    operator_name: str,
    knowledge_root: Path | None = None,
    *,
    specs: list[dict[str, Any]] | None = None,
    spec_path: Path | None = None,
) -> dict[str, Any]:
    if specs is None:
        if spec_path is not None:
            specs = load_specs_file(spec_path, operator_name)
        else:
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
    *,
    specs: list[dict[str, Any]] | None = None,
    spec_path: Path | None = None,
) -> dict[str, Any]:
    path = Path(path)
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError(f"{path}: cases must be a JSON array")
    report = inject_aux_params(
        cases,
        operator_name,
        knowledge_root,
        specs=specs,
        spec_path=spec_path,
    )
    if report["updated_params"]:
        temporary = path.with_name(f".{path.name}.aux.tmp")
        temporary.write_text(
            json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    report["path"] = str(path)
    return report


def write_run_aux_specs(
    run_dir: Path,
    *,
    operator_name: str | None = None,
    doc_path: Path | None = None,
    constraints_path: Path | None = None,
    knowledge_root: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    state_path = run_dir / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if doc_path is None:
        doc_path = Path(str(state.get("operator_doc") or ""))
    if not doc_path.is_file():
        raise ValueError(f"operator doc snapshot not found: {doc_path}")
    if constraints_path is None:
        iteration = int(state.get("current_iteration") or 1)
        constraints_path = run_dir / f"iter_{iteration:03d}" / "constraints.json"
    constraints = json.loads(
        Path(constraints_path).read_text(encoding="utf-8")
    )
    operator_name = operator_name or str(constraints.get("operator_name") or "")
    if not operator_name:
        raise ValueError("operator_name is unavailable")
    payload = detect_aux_specs(
        operator_name, Path(doc_path), constraints, knowledge_root,
    )
    _merge_plugin_specs(
        payload, detect_plugin_value_specs(run_dir, constraints),
    )
    payload["source"]["constraints"] = str(Path(constraints_path).resolve())
    output_path = Path(output_path) if output_path else run_dir / "inputs" / "aux_params.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return payload


def detect_plugin_value_specs(
    run_dir: Path, constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build aux specs from plugin_requirements.json + value_constraints.json.

    No operator name is hardcoded: the plugin analyzer reports which
    ``*_values`` attributes the plugin reads, and ``value_constraints.json``
    supplies the generator/validator + options for each value.
    """
    run_dir = Path(run_dir)
    requirements_path = run_dir / "inputs" / "plugin_requirements.json"
    iteration = 1
    state_path = run_dir / "run_state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            iteration = int(state.get("current_iteration") or 1)
        except (OSError, ValueError, json.JSONDecodeError):
            iteration = 1
    constraints_path = run_dir / f"iter_{iteration:03d}" / "value_constraints.json"
    if not constraints_path.is_file():
        constraints_path = run_dir / "inputs" / "value_constraints.json"
    if not requirements_path.is_file() or not constraints_path.is_file():
        return []
    try:
        requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
        value_entries = json.loads(constraints_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(value_entries, dict):
        value_entries = value_entries.get("value_constraints") or []
    if not isinstance(value_entries, list):
        return []
    by_attr: dict[str, dict[str, Any]] = {}
    by_param: dict[str, dict[str, Any]] = {}
    for entry in value_entries:
        if not isinstance(entry, dict):
            continue
        attr = entry.get("value_attr") or entry.get("name")
        if attr:
            by_attr[str(attr)] = entry
        param = entry.get("param") or entry.get("target_param")
        if param:
            by_param[str(param)] = entry

    specs: list[dict[str, Any]] = []
    for item in requirements.get("required_value_attrs") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        target = str(item.get("target_param") or "")
        entry = by_attr.get(name) or by_param.get(target)
        if not isinstance(entry, dict):
            continue
        if entry.get("review_required"):
            # Candidate extractor output must be reviewed/finalized by the
            # EXTRACT/CHECK stage before it can drive case generation.
            continue
        target = target or str(entry.get("param") or "")
        generator = entry.get("generator")
        if not generator:
            continue
        specs.append({
            "name": name,
            "type": "attr",
            "aux": True,
            "value_kind": "tensor_values",
            "dtype": "string",
            "required": False,
            "target_param": target,
            "tensor_index": _tensor_slot_index(constraints, target),
            "signature_index": _signature_index(constraints, target),
            "generator": generator,
            "validator": entry.get("validator") or generator,
            "generator_options": entry.get("generator_options") or {},
            "value_attr": name,
            "evidence": [
                item.get("evidence", ""),
                *[f"doc:{line}" for line in entry.get("src_txt_line") or []],
            ],
        })
    return specs


def _write_aux_payload(spec_path: Path, payload: dict[str, Any]) -> None:
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = spec_path.with_name(f".{spec_path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(spec_path)


def _merge_plugin_specs(
    payload: dict[str, Any],
    plugin_specs: list[dict[str, Any]],
) -> bool:
    if not isinstance(payload, dict) or not plugin_specs:
        return False
    aux = payload.setdefault("aux_params", [])
    existing = {
        str(spec.get("name"))
        for spec in aux
        if isinstance(spec, dict)
    }
    added = [spec for spec in plugin_specs if spec["name"] not in existing]
    if not added:
        return False
    aux.extend(added)
    return True


def ensure_run_aux_specs(
    run_dir: Path,
    operator_name: str,
    constraints: dict[str, Any],
    knowledge_root: Path | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    spec_path = run_dir / "inputs" / "aux_params.json"
    plugin_specs = detect_plugin_value_specs(run_dir, constraints)
    if spec_path.is_file():
        try:
            payload = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("operator_name") == operator_name:
            if _merge_plugin_specs(payload, plugin_specs):
                _write_aux_payload(spec_path, payload)
            return payload
    state_path = run_dir / "run_state.json"
    if not state_path.is_file():
        return {"operator_name": operator_name, "aux_params": [], "candidates": []}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    doc_path = Path(str(state.get("operator_doc") or ""))
    if not doc_path.is_file():
        return {"operator_name": operator_name, "aux_params": [], "candidates": []}
    payload = detect_aux_specs(operator_name, doc_path, constraints, knowledge_root)
    _merge_plugin_specs(payload, plugin_specs)
    _write_aux_payload(spec_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect/inject TTK auxiliary parameters."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect = subparsers.add_parser("detect")
    detect.add_argument("--run-dir", required=True)
    detect.add_argument("--operator-name")
    detect.add_argument("--doc")
    detect.add_argument("--constraints")
    detect.add_argument("--knowledge-root")
    detect.add_argument("--output")

    inject = subparsers.add_parser("inject")
    inject.add_argument("--cases", required=True)
    inject.add_argument("--operator-name", required=True)
    inject.add_argument("--spec-file")
    inject.add_argument("--knowledge-root")

    args = parser.parse_args()
    if args.command == "detect":
        payload = write_run_aux_specs(
            Path(args.run_dir),
            operator_name=args.operator_name,
            doc_path=Path(args.doc) if args.doc else None,
            constraints_path=Path(args.constraints) if args.constraints else None,
            knowledge_root=Path(args.knowledge_root) if args.knowledge_root else None,
            output_path=Path(args.output) if args.output else None,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    report = inject_aux_params_into_file(
        Path(args.cases).expanduser().resolve(),
        args.operator_name,
        Path(args.knowledge_root).expanduser().resolve()
        if args.knowledge_root else None,
        spec_path=Path(args.spec_file).expanduser().resolve()
        if args.spec_file else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
