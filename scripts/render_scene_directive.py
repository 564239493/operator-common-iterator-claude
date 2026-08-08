#!/usr/bin/env python3
"""Render the run-level scene directive and persist the scene selection.

Deterministic glue between the scene-scan Agent output and the
constraint-extractor Agent input. It does three things:

1. Validates the user's selection against what the scene-scan found in the
   operator doc. An inconsistent selection exits 2 and blocks EXTRACT (no
   silent fallback).
   - v2 (schema_version=2): selection is ``{device_types, scenes_by_device}``
     — a per-device multi-select of scene ids; the same scene id may be picked
     under more than one device (kept separately, not flattened).
   - v1 (no schema_version): single-select ``{quant_mode, quant_width}``.
2. Derives ``quant_combos`` (distinct (mode, width) from the selected scenes'
   ``quant_mode``/``quant_width``), renders ``inputs/scene_directive.md`` —
   the prose + machine-readable block the extractor reads to scope
   presence/Constraints. ≥2 quant_combos → union pruning; 1 → old single-combo
   prose; 0 → no pruning (non-quant context only).
3. Writes the ``scene`` field back into ``run_state.json`` (single source of
   truth), bumping ``updated_at``. Other run_state fields are preserved.

Scope (decided by the orchestrator from ``--scene`` + ``has_scenarios``):

- ``off``  — scene disabled; write run_state.scene(enabled=false, scope=off),
  do NOT write a directive file. Extractor sees no directive → no pruning.
- ``all``  — all scenarios in the doc; write run_state.scene(scope=all) with
  the full per-device scene set, do NOT write a directive file (no pruning).
- ``subset`` — user-chosen per-device scenes; write run_state.scene(scope=
  subset, scenes_by_device, selected_scene_ids, quant_combos) AND write
  inputs/scene_directive.md with the pruning instructions.

The directive file is written ONLY for ``subset``. Existence of
``inputs/scene_directive.md`` is the extractor's signal to prune.

v1 compatibility: when ``scene_scan.json`` lacks ``schema_version`` (or it is
not 2), the v1 single-select path is used unchanged so old runs can resume.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# v1 path (kept for old-run resume; schema_version missing)
# --------------------------------------------------------------------------- #

def _match_selection(
    scan_combos: list[dict],
    sel_mode: str,
    sel_width: str | None,
) -> tuple[list[dict], list[dict]]:
    """Return (selected_combos, dropped_combos) from scan.valid_combos.

    Single-select: a scan combo {mode, width} is "selected" when its mode
    equals sel_mode AND (its width is None OR its width equals sel_width).
    """
    selected: list[dict] = []
    dropped: list[dict] = []
    for combo in scan_combos:
        mode = combo.get("mode")
        width = combo.get("width")
        if mode == sel_mode and (width is None or width == sel_width):
            selected.append(combo)
        else:
            dropped.append(combo)
    return selected, dropped


def _validate_selection(
    scan: dict, selection: dict
) -> tuple[list[str], list[str], str | None, str | None]:
    """Return (errors, warnings, sel_mode, sel_width). errors non-empty → abort."""
    errors: list[str] = []
    warnings: list[str] = []
    sel_mode = selection.get("quant_mode")
    sel_width = selection.get("quant_width")

    if not isinstance(sel_mode, str) or not sel_mode:
        errors.append("selection.quant_mode must be a non-empty string")
        return errors, warnings, sel_mode, sel_width

    scan_modes = set(scan.get("quant_modes") or [])
    if sel_mode not in scan_modes:
        errors.append(f"selected quant_mode not in scan: {sel_mode!r}")

    if sel_width is not None:
        if not isinstance(sel_width, str) or not sel_width:
            errors.append("selection.quant_width must be a string or null")
        else:
            widths_for_selected_mode: set[str] = set()
            for combo in scan.get("valid_combos") or []:
                if combo.get("mode") == sel_mode and combo.get("width") is not None:
                    widths_for_selected_mode.add(combo["width"])
            if sel_width not in widths_for_selected_mode:
                errors.append(
                    f"selected quant_width {sel_width!r} not valid for mode {sel_mode!r}"
                )
    return errors, warnings, sel_mode, sel_width


def _render_directive(
    sel_mode: str, sel_width: str | None, valid_combos: list[dict]
) -> str:
    """Render the v1 prose directive consumed by constraint-extractor."""
    width_str = sel_width if sel_width is not None else "null（非量化/无位宽细分）"
    combo_lines: list[str] = []
    for c in valid_combos:
        w = c.get("width")
        combo_lines.append(f"  - ({c['mode']}, {w if w is not None else 'null'})")
    combos_block = "\n".join(combo_lines) if combo_lines else "  (none)"

    return f"""## 场景指令（run 级，本次提取范围）

由 `scripts/render_scene_directive.py` 渲染；源 `inputs/scene_scan.json` + 用户选择。
提取器必须读本文件并据此屏蔽非选定场景的参数与约束。

本次仅提取以下单个量化场景的约束，屏蔽其他所有场景（单选）：
- 量化方式：{sel_mode}
- 量化位宽组合：{width_str}
- 选定 (方式, 位宽) 组合：
{combos_block}

提取要求：
1. 仅保留符合上述组合的参数存在性路径。例如选定量化时：`scaleOptional`
   /`offsetOptional` 视为可能存在；`antiquantScaleOptional`
   /`antiquantOffsetOptional` 视为必 None（屏蔽），其 `presence_dependency`
   不产出。`perTokenScaleOptional` 按"动态"是否在选定组合内决定。
2. `constraints_in_parameters` 仅保留与选定场景一致的约束行；与未选场景绑定的
   专属约束（如伪量化 weight=INT4 仅 perchannel、A16W4 非对称仅 perchannel）不产出。
3. 与所有场景通用的约束（`shape_equality`、维度、`dtype`、`format`、`groupType`
   等）原样保留，不得因场景删除。
4. `allowed_range_value` 中与本场景无关的枚举候选（如未选位宽、未选场景专属值）
   剔除；保留通用候选。
5. 不得臆造文档未声明的限制；场景仅做"屏蔽"，不做"扩展"。提取结果必须仍满足
   `OperatorRule` 与 `scripts/validate_artifacts.py constraints` 校验。
6. 落盘后照常跑 `normalize_constraints.py` + `validate_artifacts.py constraints`。
"""


def _scene_payload(
    scope: str,
    scan_path: Path,
    scan: dict,
    sel_mode: str | None = None,
    sel_width: str | None = None,
    valid_combos: list[dict] | None = None,
    directive_path: Path | None = None,
) -> dict:
    return {
        "enabled": scope != "off",
        "scope": scope,
        "quant_mode": sel_mode,
        "quant_width": sel_width,
        "valid_combos": valid_combos or [],
        "directive": str(directive_path) if directive_path else "",
        "scan": str(scan_path),
    }


# --------------------------------------------------------------------------- #
# v2 path (schema_version=2): device types + per-device scene multi-select
# --------------------------------------------------------------------------- #

def _scenes_by_id(scan: dict) -> dict[str, dict]:
    return {
        s.get("id"): s
        for s in (scan.get("scenes") or [])
        if isinstance(s, dict) and s.get("id") is not None
    }


_GENERAL_DEVICE = "通用"  # op-scene wildcard: "无设备标注 = 适用所有设备"


def _concrete_devices(scan: dict) -> list[str]:
    """Scan device_types with the '通用' wildcard marker removed."""
    return [d for d in (scan.get("device_types") or []) if d != _GENERAL_DEVICE]


def _scene_applies_to(scene: dict, device: str) -> bool:
    """A scene applies to a concrete device when the device is explicitly
    listed, OR the scene is tagged '通用' (no device annotation = all devices).
    """
    sdev = scene.get("device_types") or []
    return device in sdev or _GENERAL_DEVICE in sdev


def _ids_for_device(scan: dict, device: str) -> list[str]:
    """Scene ids applicable to ``device`` — includes '通用'-tagged scenes."""
    out: list[str] = []
    for s in (scan.get("scenes") or []):
        if not isinstance(s, dict) or s.get("id") is None:
            continue
        if _scene_applies_to(s, device):
            out.append(s["id"])
    return out


def _normalize_id_list(ids: Any, scan: dict, device: str) -> list[str]:
    if ids in ("全部", "all"):
        return _ids_for_device(scan, device)
    if isinstance(ids, list):
        return [i for i in ids if isinstance(i, str)]
    return []


def _resolve_selection_v2(
    scan: dict, selection: dict
) -> tuple[list[str], list[str], dict[str, list[str]], list[str]]:
    """Validate + resolve a v2 selection.

    Returns (errors, warnings, scenes_by_device_resolved, selected_scene_ids).
    ``scenes_by_device_resolved`` keeps per-device lists without cross-device
    dedup (the same id may appear under several devices). ``selected_scene_ids``
    is the distinct id union (convenience, non-authoritative).

    '通用' is a wildcard meaning "applies to all devices the operator supports".
    It is kept verbatim in the resolved ``device_types`` and as a
    ``scenes_by_device`` key when selected as a device — render never expands
    it, because ``scene_scan.device_types`` only lists devices that have scenes,
    which may be a proper subset of the doc's supported products. The
    constraint-extractor (which reads the operator doc's product-support table)
    expands '通用' to the doc's √-marked products when writing ``product_support``.
    """
    errors: list[str] = []
    warnings: list[str] = []
    scenes_by_id = _scenes_by_id(scan)
    scan_devices = list(scan.get("device_types") or [])

    raw_devices = selection.get("device_types")
    raw_sbd = selection.get("scenes_by_device")

    # Resolve device_types verbatim — '通用' is kept as the wildcard marker
    # (the constraint-extractor expands it against the doc's √-marked products).
    if raw_devices in ("全部", "all"):
        sel_devices = [d for d in scan_devices if isinstance(d, str)]
    elif isinstance(raw_devices, list):
        sel_devices = [d for d in raw_devices if isinstance(d, str)]
    else:
        errors.append("selection.device_types must be a list or '全部'")
        sel_devices = []

    for d in sel_devices:
        if d not in scan_devices:
            warnings.append(f"selected device not in scan device_types: {d!r} (kept)")

    # Resolve scenes_by_device: keys mirror the selected device_types. A '通用'
    # key is kept as-is (device-agnostic scenes); a scene tagged '通用' applies
    # to every device, so it is valid under any concrete key too.
    sbd: dict[str, list[str]] = {}
    if raw_sbd in ("全部", "all"):
        for d in sel_devices:
            sbd[d] = _ids_for_device(scan, d)
    elif isinstance(raw_sbd, dict):
        for d, ids in raw_sbd.items():
            if d not in sel_devices:
                warnings.append(
                    f"scenes_by_device has device {d!r} not in selection.device_types; ignored"
                )
                continue
            sbd[d] = _normalize_id_list(ids, scan, d)
            if not isinstance(ids, (list, str)) and ids not in ("全部", "all"):
                warnings.append(
                    f"scenes_by_device[{d!r}] must be a list or '全部'; ignored"
                )
                sbd[d] = []
    else:
        errors.append("selection.scenes_by_device must be a dict or '全部'")

    # Existence + device-applicability check (通用-tagged scenes apply everywhere);
    # drop empty devices (partial-empty handling).
    for d in list(sbd.keys()):
        kept: list[str] = []
        for sid in sbd[d]:
            s = scenes_by_id.get(sid)
            if s is None:
                warnings.append(f"scene id not in scan: {sid!r} (device {d!r}); dropped")
                continue
            if not _scene_applies_to(s, d):
                warnings.append(
                    f"SCENE_DEVICE_MISMATCH: scene {sid!r} not applicable to device {d!r} (kept)"
                )
            kept.append(sid)
        if not kept:
            warnings.append(f"DEVICE_NO_SCENES_SELECTED: device {d!r} has 0 scenes; removed")
            sbd.pop(d, None)
        else:
            sbd[d] = kept

    if not sbd:
        errors.append("EMPTY_SCENE: selection yields no scenes (all devices empty)")

    selected_scene_ids: list[str] = []
    for ids in sbd.values():
        for sid in ids:
            if sid not in selected_scene_ids:
                selected_scene_ids.append(sid)
    return errors, warnings, sbd, selected_scene_ids


def _quant_combos_from_scenes(
    selected_scene_ids: list[str], scan: dict
) -> list[dict]:
    """Distinct (mode, width) derived from selected quant scenes."""
    scenes_by_id = _scenes_by_id(scan)
    combos: list[dict] = []
    seen: set[tuple] = set()
    for sid in selected_scene_ids:
        s = scenes_by_id.get(sid)
        if not s:
            continue
        mode = s.get("quant_mode")
        if mode is None:
            continue
        width = s.get("quant_width")
        key = (mode, width)
        if key in seen:
            continue
        seen.add(key)
        combos.append({"mode": mode, "width": width})
    return combos


def _render_directive_v2(
    scan: dict,
    scenes_by_device: dict[str, list[str]],
    selected_scene_ids: list[str],
    quant_combos: list[dict],
) -> str:
    """Render the v2 directive: per-device listing + quant branch + machine block."""
    scenes_by_id = _scenes_by_id(scan)

    dev_sections: list[str] = []
    for d, ids in scenes_by_device.items():
        lines = [f"**{d}**:"]
        for sid in ids:
            s = scenes_by_id.get(sid, {})
            cat = s.get("category", "")
            name = s.get("name", sid)
            desc = s.get("description", "")
            lines.append(f"- {sid} {cat}: {name} — {desc}")
        dev_sections.append("\n".join(lines))
    listing = "\n\n".join(dev_sections) if dev_sections else "(无)"

    n = len(quant_combos)
    if n == 1:
        m = quant_combos[0]["mode"]
        w = quant_combos[0].get("width")
        width_str = w if w is not None else "null（非量化/无位宽细分）"
        quant_block = (
            "本次仅提取以下单个量化场景的约束，屏蔽其他所有场景（单选）：\n"
            f"- 量化方式：{m}\n"
            f"- 量化位宽组合：{width_str}\n"
            "- 选定 (方式, 位宽) 组合：\n"
            f"  - ({m}, {w if w is not None else 'null'})"
        )
        req1 = (
            "1. 仅保留符合上述组合的参数存在性路径。例如选定量化时：`scaleOptional`"
            "/`offsetOptional` 视为可能存在；`antiquantScaleOptional`"
            "/`antiquantOffsetOptional` 视为必 None（屏蔽），其 `presence_dependency`"
            " 不产出。`perTokenScaleOptional` 按“动态”是否在选定组合内决定。"
        )
    elif n == 0:
        quant_block = "未选量化场景，不剪枝；非量化/布局/分离/MASK/MLA 场景仅作上下文。"
        req1 = "1. 无量化剪枝：所有 Optional 参数存在性路径原样保留，不因量化场景屏蔽。"
    else:
        combo_lines = "\n".join(
            f"  - ({c['mode']}, {c.get('width') if c.get('width') is not None else 'null'})"
            for c in quant_combos
        )
        quant_block = (
            "多量化并选，按并集保留：任一选定量化场景在场的 Optional 参数视为可能存在，"
            "仅屏蔽所有选定场景均不在场的专属参数。\n"
            "- 选定 quant_combos：\n"
            f"{combo_lines}"
        )
        req1 = (
            "1. 按并集保留：任一选定量化场景在场的 Optional 参数"
            "（`scaleOptional`/`offsetOptional`/`antiquantScaleOptional`/"
            "`antiquantOffsetOptional`/`perTokenScaleOptional` 等）视为可能存在；"
            "仅屏蔽所有选定场景均不在场的专属参数。非量化/布局/分离/MASK/MLA 场景"
            "仅作上下文，不触发 presence 剪枝。"
        )

    machine = json.dumps(
        {
            "device_types": list(scenes_by_device.keys()),
            "scenes_by_device": scenes_by_device,
            "quant_combos": quant_combos,
        },
        ensure_ascii=False,
    )

    return f"""## 场景指令（run 级，本次提取范围）

由 `scripts/render_scene_directive.py` 渲染；源 `inputs/scene_scan.json` + 用户选择。
提取器必须读本文件并据此屏蔽非选定场景的参数与约束。

### 选定场景（逐设备）

{listing}

### 量化剪枝

{quant_block}

提取要求：
{req1}
2. `constraints_in_parameters` 仅保留与选定场景一致的约束行；与未选场景绑定的
   专属约束（如伪量化 weight=INT4 仅 perchannel、A16W4 非对称仅 perchannel）不产出。
3. 与所有场景通用的约束（`shape_equality`、维度、`dtype`、`format`、`groupType`
   等）原样保留，不得因场景删除。
4. `allowed_range_value` 中与本场景无关的枚举候选（如未选位宽、未选场景专属值）
   剔除；保留通用候选。
5. 不得臆造文档未声明的限制；场景仅做"屏蔽"，不做"扩展"。提取结果必须仍满足
   `OperatorRule` 与 `scripts/validate_artifacts.py constraints` 校验。
6. 落盘后照常跑 `normalize_constraints.py` + `validate_artifacts.py constraints`。

<!-- scene: {machine} -->
"""


def _scene_payload_v2(
    scope: str,
    scan_path: Path,
    scan: dict,
    scenes_by_device: dict[str, list[str]] | None = None,
    selected_scene_ids: list[str] | None = None,
    quant_combos: list[dict] | None = None,
    directive_path: Path | None = None,
) -> dict:
    quant_combos = quant_combos or []
    qm: str | None = None
    qw: str | None = None
    if len(quant_combos) == 1:
        qm = quant_combos[0]["mode"]
        qw = quant_combos[0].get("width")
    return {
        "enabled": scope != "off",
        "scope": scope,
        "schema_version": 2,
        "device_types": list((scenes_by_device or {}).keys()),
        "scenes_by_device": scenes_by_device or {},
        "selected_scene_ids": selected_scene_ids or [],
        "quant_combos": quant_combos,
        "quant_mode": qm,
        "quant_width": qw,
        "valid_combos": quant_combos,  # alias for v1 downstream compatibility
        "directive": str(directive_path) if directive_path else "",
        "scan": str(scan_path),
    }


def _write_run_state_scene(run_dir: Path, scene_payload: dict) -> None:
    """Merge scene into run_state.json, bumping updated_at; preserve other fields."""
    state_path = run_dir / "run_state.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"run_state.json not found: {state_path}")
    state = _load_json(state_path)
    if not isinstance(state, dict):
        raise ValueError("run_state.json root must be an object")
    state["scene"] = scene_payload
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _is_v2(scan: dict) -> bool:
    return scan.get("schema_version") == 2


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Render the scene directive and persist the selection to run_state."
            " Called by the orchestrator's SCENE_SCAN sub-step (after scene-scan"
            " and the user's AskUserQuestion answers)."
        )
    )
    p.add_argument("--scan", required=True, help="path to inputs/scene_scan.json")
    p.add_argument(
        "--selection",
        help=(
            "v2: path to selection JSON {device_types, scenes_by_device}; "
            "v1: {quant_mode, quant_width}. Required for --scope subset, ignored otherwise."
        ),
    )
    p.add_argument("--run-dir", required=True, help="run directory (contains run_state.json + inputs/)")
    p.add_argument(
        "--scope",
        choices=("subset", "all", "off"),
        required=True,
        help="subset=user-chosen scenes (write directive); all=full scan (no directive); off=disabled (no directive)",
    )
    args = p.parse_args()

    run_dir = Path(args.run_dir).resolve()
    scan_path = Path(args.scan).resolve()
    if not scan_path.is_file():
        print(json.dumps(
            {"ok": False, "code": "SCENE_SCAN_NOT_FOUND", "path": str(scan_path)},
            ensure_ascii=False,
        ))
        return 2
    scan = _load_json(scan_path)

    inputs_dir = run_dir / "inputs"
    directive_path = inputs_dir / "scene_directive.md"

    # ----- off ------------------------------------------------------------- #
    if args.scope == "off":
        if _is_v2(scan):
            _write_run_state_scene(
                run_dir, _scene_payload_v2("off", scan_path, scan)
            )
        else:
            _write_run_state_scene(run_dir, _scene_payload("off", scan_path, scan))
        print(json.dumps(
            {"ok": True, "scope": "off", "directive": "", "scene_scan": str(scan_path)},
            ensure_ascii=False,
        ))
        return 0

    # ----- all ------------------------------------------------------------ #
    if args.scope == "all":
        if _is_v2(scan):
            scan_devices = _concrete_devices(scan)
            sbd = {d: _ids_for_device(scan, d) for d in scan_devices}
            sel_ids: list[str] = []
            for ids in sbd.values():
                for sid in ids:
                    if sid not in sel_ids:
                        sel_ids.append(sid)
            quant_combos = _quant_combos_from_scenes(sel_ids, scan)
            _write_run_state_scene(
                run_dir,
                _scene_payload_v2(
                    "all", scan_path, scan, sbd, sel_ids, quant_combos
                ),
            )
            print(json.dumps(
                {"ok": True, "scope": "all", "directive": "",
                 "n_scenes": len(sel_ids), "n_devices": len(scan_devices),
                 "n_quant_combos": len(quant_combos), "scene_scan": str(scan_path)},
                ensure_ascii=False,
            ))
        else:
            valid_combos = list(scan.get("valid_combos") or [])
            _write_run_state_scene(
                run_dir,
                _scene_payload(
                    "all", scan_path, scan,
                    sel_mode=None, sel_width=None, valid_combos=valid_combos,
                ),
            )
            print(json.dumps(
                {"ok": True, "scope": "all", "directive": "", "n_combos": len(valid_combos),
                 "scene_scan": str(scan_path)},
                ensure_ascii=False,
            ))
        return 0

    # ----- subset ---------------------------------------------------------- #
    if not args.selection:
        print(json.dumps(
            {"ok": False, "code": "SELECTION_REQUIRED",
             "message": "--selection is required for --scope subset"},
            ensure_ascii=False,
        ))
        return 2
    sel_path = Path(args.selection)
    if str(sel_path) == "-":
        selection = json.loads(sys.stdin.read())
    elif sel_path.is_file():
        selection = _load_json(sel_path)
    else:
        print(json.dumps(
            {"ok": False, "code": "SELECTION_NOT_FOUND", "path": str(sel_path)},
            ensure_ascii=False,
        ))
        return 2

    if not _is_v2(scan):
        # v1 path (unchanged)
        errors, warnings, sel_mode, sel_width = _validate_selection(scan, selection)
        if errors:
            print(json.dumps(
                {"ok": False, "code": "INVALID_SELECTION",
                 "errors": errors, "warnings": warnings},
                ensure_ascii=False,
            ))
            return 2
        selected, dropped = _match_selection(
            scan.get("valid_combos") or [], sel_mode, sel_width
        )
        if not selected:
            print(json.dumps(
                {"ok": False, "code": "EMPTY_SCENE",
                 "message": "selection yields no valid (mode, width) combo",
                 "warnings": warnings},
                ensure_ascii=False,
            ))
            return 2
        for d in dropped:
            warnings.append(
                f"dropped combo not in selection: ({d.get('mode')}, {d.get('width')})"
            )
        directive_text = _render_directive(sel_mode, sel_width, selected)
        inputs_dir.mkdir(parents=True, exist_ok=True)
        directive_path.write_text(directive_text, encoding="utf-8")
        _write_run_state_scene(
            run_dir,
            _scene_payload(
                "subset", scan_path, scan,
                sel_mode=sel_mode, sel_width=sel_width,
                valid_combos=selected, directive_path=directive_path,
            ),
        )
        print(json.dumps(
            {"ok": True, "scope": "subset", "directive": str(directive_path),
             "n_combos": len(selected), "n_dropped": len(dropped),
             "warnings": warnings, "scene_scan": str(scan_path)},
            ensure_ascii=False,
        ))
        return 0

    # v2 path
    errors, warnings, sbd, sel_ids = _resolve_selection_v2(scan, selection)
    if errors:
        code = "EMPTY_SCENE" if any("EMPTY_SCENE" in e for e in errors) else "INVALID_SELECTION"
        print(json.dumps(
            {"ok": False, "code": code,
             "errors": errors, "warnings": warnings},
            ensure_ascii=False,
        ))
        return 2

    quant_combos = _quant_combos_from_scenes(sel_ids, scan)
    directive_text = _render_directive_v2(scan, sbd, sel_ids, quant_combos)
    inputs_dir.mkdir(parents=True, exist_ok=True)
    directive_path.write_text(directive_text, encoding="utf-8")
    _write_run_state_scene(
        run_dir,
        _scene_payload_v2(
            "subset", scan_path, scan, sbd, sel_ids, quant_combos, directive_path
        ),
    )
    print(json.dumps(
        {"ok": True, "scope": "subset", "directive": str(directive_path),
         "n_scenes": len(sel_ids), "n_devices": len(sbd),
         "n_quant_combos": len(quant_combos),
         "warnings": warnings, "scene_scan": str(scan_path)},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
