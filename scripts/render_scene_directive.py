"""Render ``inputs/scene_directive.md`` from ``inputs/scene_scan.json`` + the
user's three-level selection, and persist the scene payload to
``run_state.json``.

Scene model (three-level): 设备类型 → 量化模板 → 特性参数. The scanner emits
``scene_scan.json`` with nested ``devices[].templates[].feature_params[].params[]``;
no ``通用`` wildcard group (``device_types`` come verbatim from the doc "产品支持
情况" table). The orchestrator asks the user Q1 (devices) → Q2 (templates) → Q3
(feature params; "可以不选择" = expand all), producing ``selection.json``:
``{device_types:[...]|"全部", selection:{device:{template:[features]|None}}}``.

``_param_modes`` resolves that selection into per-device per-param three states:
``{"expand": [values]}`` (candidate set = order-preserving de-duplicated union of
the param's ``values`` across the selected templates that expand it — NOT the doc
full enum; the extractor must not fall back to the doc) | ``{"fix": X}`` (single
value, ``values[0]``) | missing key (Optional param under a deselected template →
no ``presence_dependency``, presence dropped). The directive carries these in a
machine block ``<!-- scene: {device_types, param_modes} -->`` (the extractor reads
only the directive, never the scan).

Scope (decided by the orchestrator from ``--scene`` + ``has_scenarios``):

- ``off``  — scene disabled; write run_state.scene(enabled=false, scope=off),
  do NOT write a directive file. Extractor sees no directive → no pruning.
- ``all``  — all scenarios in the doc; write run_state.scene(scope=all) with
  the full per-device scene set, do NOT write a directive file (no pruning).
- ``subset`` — user-chosen per-device scenes; write run_state.scene(scope=
  subset, device_types, selection, param_modes) AND write
  inputs/scene_directive.md with the pruning instructions.

The directive file is written ONLY for ``subset``. Existence of
``inputs/scene_directive.md`` is the extractor's signal to prune.
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
# Scene model: device → 量化模板 → 特性参数 (three-level)
# --------------------------------------------------------------------------- #

def _devices_by_name(scan: dict) -> dict[str, dict]:
    return {
        d.get("device"): d
        for d in (scan.get("devices") or [])
        if isinstance(d, dict) and d.get("device") is not None
    }


def _templates_by_name(device: dict) -> dict[str, dict]:
    return {
        t.get("template"): t
        for t in (device.get("templates") or [])
        if isinstance(t, dict) and t.get("template") is not None
    }


def _feature_names(template: dict) -> list[str]:
    return [
        fp.get("feature")
        for fp in (template.get("feature_params") or [])
        if isinstance(fp, dict) and fp.get("feature")
    ]


def _resolve_selection(
    scan: dict, selection: dict
) -> tuple[list[str], list[str], list[str], dict]:
    """Validate + resolve a three-level selection.

    selection = {"device_types": [...] | "全部",
                 "selection": {device: {template: [features] | None | "全部"}}}

    Returns (errors, warnings, sel_devices, selection_resolved) where
    selection_resolved = {device: {template: [features] | None}} with "全部"
    normalized to None (expand all features). A template absent from
    selection[device] = deselected (Q2 not chosen) → its params' presence is
    dropped (unless the param also appears in a selected template — union
    semantics resolved in ``_param_modes``).
    """
    errors: list[str] = []
    warnings: list[str] = []
    devices_by_name = _devices_by_name(scan)
    scan_devices = [d for d in (scan.get("device_types") or []) if isinstance(d, str)]

    raw_devices = selection.get("device_types")
    if raw_devices in ("全部", "all"):
        sel_devices = list(scan_devices)
    elif isinstance(raw_devices, list):
        sel_devices = [d for d in raw_devices if isinstance(d, str)]
    else:
        errors.append("selection.device_types must be a list or '全部'")
        sel_devices = []
    for d in sel_devices:
        if d not in scan_devices:
            errors.append(f"selected device not in scan device_types: {d!r}")

    raw_sel = selection.get("selection")
    if raw_sel in ("全部", "all"):
        raw_sel = {d: "全部" for d in sel_devices}
    if not isinstance(raw_sel, dict):
        errors.append("selection.selection must be a dict {device: {template: features|None}}")
        raw_sel = {}

    resolved: dict[str, dict] = {}
    for d in sel_devices:
        dev_entry = devices_by_name.get(d)
        if dev_entry is None:
            continue  # device not in scan devices (already errored above)
        tpls = _templates_by_name(dev_entry)
        raw_dev = raw_sel.get(d)
        if raw_dev in ("全部", "all"):
            raw_dev = {t: "全部" for t in tpls}
        if raw_dev is None:
            raw_dev = {}
        if not isinstance(raw_dev, dict):
            warnings.append(
                f"selection.selection[{d!r}] must be a dict or '全部'; treated as no templates"
            )
            raw_dev = {}
        dev_resolved: dict[str, list[str] | None] = {}
        for t, feats in raw_dev.items():
            if t not in tpls:
                warnings.append(f"template {t!r} not in device {d!r} templates; ignored")
                continue
            if feats in ("全部", "all") or feats is None:
                dev_resolved[t] = None  # expand all features (Q3 skipped / 全选)
            elif isinstance(feats, list):
                feat_set = _feature_names(tpls[t])
                for f in feats:
                    if isinstance(f, str) and f not in feat_set:
                        warnings.append(
                            f"feature {f!r} not in template {t!r} (device {d!r}); dropped"
                        )
                dev_resolved[t] = [f for f in feats if isinstance(f, str)]
            else:
                warnings.append(
                    f"selection.selection[{d!r}][{t!r}] must be list/None/'全部'; "
                    f"treated as expand-all"
                )
                dev_resolved[t] = None
        if not dev_resolved:
            warnings.append(f"DEVICE_NO_TEMPLATES_SELECTED: device {d!r} has 0 selected templates")
        else:
            resolved[d] = dev_resolved

    if not resolved:
        errors.append("EMPTY_SCENE: selection yields no selected templates (all devices empty)")

    return errors, warnings, sel_devices, resolved


def _param_modes(
    scan: dict, sel_devices: list[str], selection_resolved: dict
) -> dict[str, dict]:
    """Resolve per-device per-param expand/fix decision (union semantics).

    For each device, iterate its templates in scan order. A param appearing in
    ANY selected template under an expanded feature (Q3 selected the feature, or
    the template's Q3 was skipped/全选 = expand all) → ``{"expand": [values]}``
    where ``[values]`` is the union of the param's ``values`` across expanding
    selected templates. A param
    appearing only under unexpanded features of selected templates →
    ``{"fix": <values[0]>}`` (first-seen default kept). A param appearing ONLY in
    deselected templates is absent from ``param_modes[device]`` → presence drop.

    ``expand`` wins over ``fix``: if a param is expanded in any selected
    template it is expanded, and only the expanding selected templates
    contribute values (a fix-only template does not add its value).
    """
    devices_by_name = _devices_by_name(scan)
    out: dict[str, dict] = {}
    for d in sel_devices:
        dev = devices_by_name.get(d)
        if dev is None:
            continue
        sel_templates = selection_resolved.get(d, {})
        param_state: dict[str, object] = {}
        for t in (dev.get("templates") or []):
            if not isinstance(t, dict):
                continue
            tname = t.get("template")
            if tname not in sel_templates:
                continue  # deselected template
            feats = sel_templates.get(tname)
            for fp in (t.get("feature_params") or []):
                if not isinstance(fp, dict):
                    continue
                expand_this = (feats is None) or (fp.get("feature") in (feats or []))
                for p in (fp.get("params") or []):
                    if not isinstance(p, dict):
                        continue
                    pname = p.get("name")
                    if not isinstance(pname, str) or not pname:
                        continue
                    vals = p.get("values") or []
                    if expand_this:
                        cur = param_state.get(pname)
                        if isinstance(cur, dict) and "expand" in cur:
                            for v in vals:
                                if v not in cur["expand"]:
                                    cur["expand"].append(v)
                        else:
                            param_state[pname] = {"expand": list(vals)}
                    elif pname not in param_state:
                        default = vals[0] if vals else None
                        param_state[pname] = {"fix": default}
        if param_state:
            out[d] = param_state
    return out


def _render_directive(
    scan: dict,
    sel_devices: list[str],
    selection_resolved: dict,
    param_modes: dict[str, dict],
) -> str:
    """Render the directive: per-device per-template listing + three-state
    masking prose + machine block ``{device_types, param_modes}``."""
    devices_by_name = _devices_by_name(scan)
    dev_sections: list[str] = []
    for d in sel_devices:
        dev = devices_by_name.get(d)
        if dev is None:
            continue
        sel_templates = selection_resolved.get(d, {})
        lines = [f"**{d}**:"]
        for t in (dev.get("templates") or []):
            if not isinstance(t, dict):
                continue
            tname = t.get("template")
            if tname not in sel_templates:
                continue
            feats = sel_templates.get(tname)
            lines.append(f"- **{tname}**: {t.get('definition', '')}")
            uf = t.get("unsupported_features") or []
            if uf:
                lines.append(f"  - 本模板不支持：{', '.join(str(x) for x in uf)}")
            for fp in (t.get("feature_params") or []):
                if not isinstance(fp, dict):
                    continue
                feat = fp.get("feature", "")
                expand_this = (feats is None) or (feat in (feats or []))
                tag = "展开取值分支" if expand_this else "固定取默认值"
                lines.append(f"  - **{feat}** （{tag}）:")
                for p in (fp.get("params") or []):
                    if not isinstance(p, dict):
                        continue
                    pname = p.get("name", "")
                    vals = p.get("values", [])
                    lines.append(
                        f"    - {pname}: 取值：{vals}；描述：{p.get('description', '')}"
                    )
        dev_sections.append("\n".join(lines))
    listing = "\n\n".join(dev_sections) if dev_sections else "(无)"

    expand_params: list[str] = []
    fix_params: list[str] = []
    for d in sel_devices:
        pm = param_modes.get(d, {})
        for p, v in pm.items():
            if isinstance(v, dict) and "expand" in v:
                expand_params.append(f"{d}/{p}")
            elif isinstance(v, dict) and "fix" in v:
                fix_params.append(f"{d}/{p}={v.get('fix')}")

    machine = json.dumps(
        {"device_types": sel_devices, "param_modes": param_modes},
        ensure_ascii=False,
    )

    return f"""## 场景指令（run 级，本次提取范围）

由 `scripts/render_scene_directive.py` 渲染；源 `inputs/scene_scan.json` + 用户三级选择
（设备类型 → 量化模板 → 特性参数）。提取器必须读本文件并据此屏蔽非选定场景的参数与约束。

### 选定模板（逐设备逐模板）

{listing}

### 特性参数剪枝（三级语义）

- **展开**（保留取值分支）：选中模板下、Q3 选中特性（或 Q3 跳过/全选=全展开）的特性参数，
  保留其枚举/分档取值分支参与组合生成。
- **固定**（取默认值不展开）：选中模板下、Q3 未选中的特性参数，取该参数 `values[0]` 为默认值，
  仅产单值候选，不展开取值分支。
- **丢弃 presence**（不测试）：未选模板（Q2 未选）的专属参数，其 `presence_dependency` 不产出
  （若该参数同时出现在已选模板下，按已选模板的展开/固定决策处理——并集语义）。
- 展开参数：{expand_params or '(无)'}
- 固定参数：{fix_params or '(无)'}

提取要求：
1. **选择内容是基本限制，未提及的按文档原文提取**——按机读块 `param_modes` 三态收窄：
   `{{"expand": [取值清单]}}` → 所选清单是该特性参数的基本限制：凡依赖该参数取值的约束均按清单收窄——
   无论体现为该参数自身的 `allowed_range_value` 枚举候选，还是以该取值为条件的 `dtype`/`format`/`dimensions`
   分支或 `constraints_in_parameters` 行（保留命中清单内取值的候选/分支、丢弃绑定清单外取值者，清单为所选模板
   values 并集，**禁止回文档拉全集**）；`{{"fix": X}}` → 该参数取单值 `X`，依赖其取值的约束同此收窄；
   缺键（仅出现在未选模板下的 Optional 参数）→ 不产 `presence_dependency`（presence 丢）。
2. `constraints_in_parameters` 仅保留与选定模板一致的约束行，未选模板绑定的专属约束不产出；其内以取值为条件的分支按条 1 随选择收窄。
3. 与参数取值**无关**的通用约束（`shape_equality`、维度、`groupType` 等）原样保留，不得因场景删除；
   `dtype`/`format`/`dimensions` 以取值为条件分支者按条 1 收窄，无条件者原样保留。
4. `product_support` 按机读块 `device_types` 与文档"产品支持情况"√ 行取交集（无"通用"展开）。
   不得臆造文档未声明的限制；提取结果必须仍满足 `OperatorRule` 与
   `scripts/validate_artifacts.py constraints` 校验。
5. 落盘后照常跑 `normalize_constraints.py` + `validate_artifacts.py constraints`。

<!-- scene: {machine} -->
"""


def _scene_payload(
    scope: str,
    scan_path: Path,
    scan: dict,
    device_types: list[str] | None = None,
    selection: dict | None = None,
    param_modes: dict[str, dict] | None = None,
    directive_path: Path | None = None,
) -> dict:
    return {
        "enabled": scope != "off",
        "scope": scope,
        "device_types": device_types or [],
        "selection": selection or {},
        "param_modes": param_modes or {},
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
            "{device_types, selection:{device:{template:[features]|None}}}. "
            "Required for --scope subset, ignored otherwise."
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
        _write_run_state_scene(run_dir, _scene_payload("off", scan_path, scan))
        print(json.dumps(
            {"ok": True, "scope": "off", "directive": "", "scene_scan": str(scan_path)},
            ensure_ascii=False,
        ))
        return 0

    # ----- all ------------------------------------------------------------ #
    if args.scope == "all":
        scan_devices = [d for d in (scan.get("device_types") or []) if isinstance(d, str)]
        # all devices, all templates, Q3 skipped → expand all features (None)
        dev_map = _devices_by_name(scan)
        full_sel: dict[str, dict] = {}
        for d in scan_devices:
            dev = dev_map.get(d)
            if not dev:
                continue
            full_sel[d] = {
                t.get("template"): None
                for t in (dev.get("templates") or [])
                if isinstance(t, dict) and t.get("template")
            }
        param_modes = _param_modes(scan, scan_devices, full_sel)
        _write_run_state_scene(
            run_dir,
            _scene_payload(
                "all", scan_path, scan, scan_devices, full_sel, param_modes
            ),
        )
        print(json.dumps(
            {"ok": True, "scope": "all", "directive": "",
             "n_devices": len(scan_devices), "scene_scan": str(scan_path)},
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

    errors, warnings, sel_devices, selection_resolved = _resolve_selection(
        scan, selection
    )
    if errors:
        code = (
            "EMPTY_SCENE"
            if any("EMPTY_SCENE" in e for e in errors)
            else "INVALID_SELECTION"
        )
        print(json.dumps(
            {"ok": False, "code": code,
             "errors": errors, "warnings": warnings},
            ensure_ascii=False,
        ))
        return 2
    param_modes = _param_modes(scan, sel_devices, selection_resolved)
    directive_text = _render_directive(
        scan, sel_devices, selection_resolved, param_modes
    )
    inputs_dir.mkdir(parents=True, exist_ok=True)
    directive_path.write_text(directive_text, encoding="utf-8")
    _write_run_state_scene(
        run_dir,
        _scene_payload(
            "subset", scan_path, scan, sel_devices, selection_resolved,
            param_modes, directive_path
        ),
    )
    n_templates = sum(len(v) for v in selection_resolved.values())
    print(json.dumps(
        {"ok": True, "scope": "subset", "directive": str(directive_path),
         "n_devices": len(sel_devices), "n_templates": n_templates,
         "warnings": warnings, "scene_scan": str(scan_path)},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
