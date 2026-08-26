"""Render ``inputs/scene_directive.md`` from ``inputs/scene_scan.json`` + the
user's three-level selection, and persist the scene payload to
``run_state.json``.

Scene model (three-level): 设备类型 → 量化模板 → 特性参数. The scanner emits
``scene_scan.json`` with nested ``devices[].templates[].feature_params[].params[]``;
no ``通用`` wildcard group (``device_types`` come verbatim from the doc "产品支持
情况" table). The orchestrator asks the user Q1 (devices) → Q2 (templates) → Q3
(value-level config; "保持自动/未填写" = document-adaptive extraction),
producing ``selection.json``:
``{device_types:[...], selection:{device:{template: None|"fix_all_default"|{param:[values]}}}}``.

``_param_modes`` resolves explicitly selected feature parameters into two states:
``{"expand": [values]}`` (candidate set = the user's selected value subset) |
``{"fix": X}`` (single value — either the user's single-value input or ``values[0]``
under ``"fix_all_default"``). A missing key is not a third
pruning state: the extractor continues from the operator document and adapts the
parameter to the selected scene. If the selected scene forbids an Optional parameter,
the extractor must emit ``param is None``. The directive carries this policy in a
machine block ``<!-- scene: {device_types, selection, param_modes, selection_policy} -->``
(the extractor reads only the directive, never the scan).

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
SELECTION_POLICY = {
    "selected_param": "fix_or_expand",
    "unselected_param": "document_adaptive",
    "forbidden_optional": "emit_is_none",
}


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


def _scalar_eq(a: Any, b: Any) -> bool:
    """Type-aware scalar equality (bool is a subclass of int in Python, so
    ``True == 1`` / ``False == 0``; keep bool matching only bool to avoid a
    user-supplied ``1``/``0`` silently matching a bool ``true``/``false``
    param value, and vice-versa)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def _tpl_param_values(template: dict) -> dict[str, list]:
    """Map every param name in a template to its scan ``values`` list
    (across all feature_params). Used for value-subset validation."""
    out: dict[str, list] = {}
    for fp in (template.get("feature_params") or []):
        if not isinstance(fp, dict):
            continue
        for p in (fp.get("params") or []):
            if isinstance(p, dict) and isinstance(p.get("name"), str):
                out.setdefault(p["name"], p.get("values") or [])
    return out


def _resolve_selection(
    scan: dict, selection: dict
) -> tuple[list[str], list[str], list[str], dict]:
    """Validate + resolve a three-level **value-level** selection.

    selection = {"device_types": [...] | "全部",
                 "selection": {device: {template: <tpl_value>}}}

    where ``<tpl_value>`` per selected (device, template) is one of:

    - ``None`` / ``"全部"``      → preset 1: keep every parameter automatic;
      extract and adapt it from the document and selected scene.
    - ``"fix_all_default"``     → preset 2: fix **every** param to ``values[0]``
      (minimal coverage / smoke).
    - ``{param: [values]}``      → value-level (Other JSON): for each listed
      param, a single value → ``fix`` that value, multiple values → ``expand``
      that subset; params **not listed** remain automatic (document-adaptive).
      Listed values are validated to be ⊆ the param's scan ``values`` (type-aware
      so ``1`` does not match bool ``true``); invalid values are dropped with a
      warning; an empty list is treated as "not listed" (automatic/document-adaptive).

    Returns ``(errors, warnings, sel_devices, selection_resolved)`` where
    ``selection_resolved = {device: {template: None | "fix_all_default" |
    {param: [values]}}}`` with ``"全部"`` normalized to ``None``. A template
    absent from ``selection[device]`` = deselected (Q2 not chosen) → its params'
    constraints continue to be extracted from the document and adapted to the
    selected scenes (union semantics for explicitly selected values is resolved in
    ``_param_modes``).
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
        errors.append(
            "selection.selection must be a dict {device: {template: "
            "null|'fix_all_default'|{param:[values]}}}"
        )
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
        dev_resolved: dict[str, Any] = {}
        for t, feats in raw_dev.items():
            if t not in tpls:
                warnings.append(f"template {t!r} not in device {d!r} templates; ignored")
                continue
            if feats in ("全部", "all") or feats is None:
                dev_resolved[t] = None  # preset 1: automatic/document-adaptive
            elif isinstance(feats, str) and feats == "fix_all_default":
                dev_resolved[t] = "fix_all_default"  # preset 2: fix all to values[0]
            elif isinstance(feats, dict):
                tpl_param_values = _tpl_param_values(tpls[t])
                subset: dict[str, list] = {}
                for pname, vlist in feats.items():
                    if not isinstance(pname, str):
                        warnings.append(
                            f"selection[{d!r}][{t!r}] param key must be string; "
                            f"skipped {pname!r}"
                        )
                        continue
                    if pname not in tpl_param_values:
                        warnings.append(
                            f"param {pname!r} not in template {t!r} (device {d!r}); dropped"
                        )
                        continue
                    if not isinstance(vlist, list):
                        warnings.append(
                            f"selection[{d!r}][{t!r}][{pname!r}] must be a list; dropped"
                        )
                        continue
                    allowed = tpl_param_values[pname]
                    kept = [
                        v for v in vlist
                        if any(_scalar_eq(v, a) for a in allowed)
                    ]
                    dropped = [v for v in vlist if not any(_scalar_eq(v, a) for a in allowed)]
                    for v in dropped:
                        warnings.append(
                            f"value {v!r} not in param {pname!r} values {allowed} "
                            f"(template {t!r}, device {d!r}); dropped"
                        )
                    if not kept:
                        # empty / all-invalid list → treat as unmentioned → automatic
                        continue
                    subset[pname] = kept
                dev_resolved[t] = subset if subset else None
            else:
                warnings.append(
                    f"selection.selection[{d!r}][{t!r}] must be "
                    f"null/'fix_all_default'/dict; treated as expand-all"
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
    """Resolve per-device per-param expand/fix decision (value-level, union semantics).

    For each device, iterate its templates in scan order. Only an explicit value
    choice contributes a mode:

    - ``None``              → automatic: contribute no explicit mode.
    - ``"fix_all_default"`` → fix: contribute ``values[0]``.
    - ``{param: [values]}`` → listed param: single value contributes ``fix`` that
      value, multiple values contribute ``expand`` of that subset; an **unlisted**
      param contributes no explicit mode and remains document-adaptive.

    Reconcile across selected templates with **expand wins over fix, expand = union**:
    if a param is expanded in any selected template it ends up expanded (union of all
    expand subsets across templates; fix-only templates add no value); only if a param
    is fix-only in every selected template does it end up fix (first-seen value). A
    param appearing ONLY in deselected templates is absent from
    ``param_modes[device]``. Absence means document-adaptive extraction, not presence
    removal; selected-scene prohibitions must become explicit ``param is None``
    constraints.
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
                continue
            sel = sel_templates.get(tname)
            for fp in (t.get("feature_params") or []):
                if not isinstance(fp, dict):
                    continue
                for p in (fp.get("params") or []):
                    if not isinstance(p, dict):
                        continue
                    pname = p.get("name")
                    if not isinstance(pname, str) or not pname:
                        continue
                    vals = list(p.get("values") or [])
                    if not vals:
                        continue
                    # determine this template's contribution for pname
                    if sel is None:
                        continue
                    elif isinstance(sel, str) and sel == "fix_all_default":
                        contrib_mode, contrib_vals = "fix", vals[0]
                    elif isinstance(sel, dict):
                        if pname in sel:
                            subset = sel[pname]
                            if len(subset) == 1:
                                contrib_mode, contrib_vals = "fix", subset[0]
                            else:
                                contrib_mode, contrib_vals = "expand", list(subset)
                        else:
                            continue
                    else:
                        contrib_mode, contrib_vals = "expand", list(vals)
                    # reconcile into param_state (expand wins over fix; expand = union)
                    cur = param_state.get(pname)
                    if cur is None:
                        param_state[pname] = (
                            {"expand": list(contrib_vals)}
                            if contrib_mode == "expand"
                            else {"fix": contrib_vals}
                        )
                    elif contrib_mode == "expand":
                        if "expand" in cur:
                            for v in contrib_vals:
                                if not any(_scalar_eq(v, x) for x in cur["expand"]):
                                    cur["expand"].append(v)
                        else:
                            # was fix → promote to expand with this subset
                            param_state[pname] = {"expand": list(contrib_vals)}
                    else:  # fix contribution
                        # fix only sticks if no expand yet; first-seen fix kept
                        if "expand" not in cur and "fix" not in cur:
                            param_state[pname] = {"fix": contrib_vals}
                        # else: already expand or already fix → no change
        if param_state:
            out[d] = param_state
    return out


def _render_directive(
    scan: dict,
    sel_devices: list[str],
    selection_resolved: dict,
    param_modes: dict[str, dict],
    conflicts: list[dict] | None = None,
) -> str:
    """Render the directive: per-device per-template listing + selection
    masking prose + (optional) known-conflict listing + machine block
    ``{device_types, selection, param_modes, selection_policy, known_conflicts}``."""
    conflicts = conflicts or []
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
            lines.append(f"- **{tname}**: {t.get('definition', '')}")
            uf = t.get("unsupported_features") or []
            if uf:
                lines.append(f"  - 本模板不支持：{', '.join(str(x) for x in uf)}")
            pm_dev = param_modes.get(d, {})
            for fp in (t.get("feature_params") or []):
                if not isinstance(fp, dict):
                    continue
                feat = fp.get("feature", "")
                lines.append(f"  - **{feat}**:")
                for p in (fp.get("params") or []):
                    if not isinstance(p, dict):
                        continue
                    pname = p.get("name", "")
                    vals = p.get("values", [])
                    pm = pm_dev.get(pname)
                    if isinstance(pm, dict) and "expand" in pm:
                        tag = f"展开取值分支（取值清单 {pm['expand']}）"
                    elif isinstance(pm, dict) and "fix" in pm:
                        tag = f"固定取单值 {pm['fix']!r}"
                    else:
                        tag = "未显式选择；按文档和已选场景适配"
                    lines.append(
                        f"    - {pname}: 取值：{vals}；{tag}；描述：{p.get('description', '')}"
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

    conflict_lines: list[str] = []
    for c in conflicts:
        rule = c.get("rule", {}) if isinstance(c, dict) else {}
        if c.get("kind") == "required":
            rule_txt = f"要求 {c.get('target')} 取 {rule.get('required')}"
        else:
            rule_txt = f"禁止 {c.get('target')} 取 {rule.get('forbidden')}"
        ws = c.get("when_self")
        ws_txt = f"当 {c.get('param')}={ws} 时 " if ws else ""
        conflict_lines.append(
            f"- {c.get('device')}/{c.get('template')}: {ws_txt}"
            f"{c.get('param')}={c.get('param_values')} → {rule_txt}"
            f"（当前 {c.get('target')}={c.get('target_values')}）；"
            f"原因：{c.get('reason', '')}"
        )
    conflicts_section = (
        "\n\n### 已知特性参数冲突（用户确认强制继续）\n\n"
        + "\n".join(conflict_lines)
        if conflict_lines
        else ""
    )

    machine = json.dumps(
        {
            "device_types": sel_devices,
            "selection": selection_resolved,
            "param_modes": param_modes,
            "selection_policy": dict(SELECTION_POLICY),
            "known_conflicts": conflicts,
        },
        ensure_ascii=False,
    )

    return f"""## 场景指令（run 级，本次提取范围）

由 `scripts/render_scene_directive.py` 渲染；源 `inputs/scene_scan.json` + 用户三级选择
（设备类型 → 量化模板 → 特性参数）。提取器必须读本文件并据此按选定场景适配参数与约束。

### 选定模板（逐设备逐模板）

{listing}

### 特性参数剪枝（值级语义）

- **展开**（保留取值分支）：用户在 Q3 明确填写了多值取值清单的参数，仅保留该子集。
- **固定**（取单值不展开）：用户在 Q3 明确填写了单值（如 `[-1]`）的参数 → 该值；
  或选"全部固定默认值"的模板下各参数 → `values[0]`；仅产单值候选，不展开取值分支。
- **未显式选择**：继续根据算子文档和已选场景提取、适配该参数的约束，不得因为参数
  缺少 `param_modes` 键而删除其 presence、dtype、shape、value 等约束。
- **已选场景明确禁止的 Optional 参数**：必须显式产出 `param is None`；“不产
  `presence_dependency`”不等价于“不测试”，因为无 presence 约束的 Optional 参数会
  继续在存在/缺席之间生成。
- 展开参数：{expand_params or '(无)'}
- 固定参数：{fix_params or '(无)'}{conflicts_section}

提取要求：
1. **选择内容是基本限制，未提及的按文档原文提取**——先按机读块 `selection` 确定
   每个设备选中的模板，再按 `param_modes` 收窄用户明确配置的参数：
   `{{"expand": [取值清单]}}` → 所选清单是该特性参数的基本限制：凡依赖该参数取值的约束均按清单收窄——
   无论体现为该参数自身的 `allowed_range_value` 枚举候选，还是以该取值为条件的 `dtype`/`format`/`dimensions`
   分支或 `constraints_in_parameters` 行（保留命中清单内取值的候选/分支、丢弃绑定清单外取值者，清单为用户在 Q3 明确填写的取值子集）；`{{"fix": X}}` → 该参数取单值 `X`，依赖其取值的约束同此收窄；
   缺键 → 仅在机读块 `selection` 指定的模板范围内按文档继续提取适配，不得回退到
   未选模板的参数域，也不得解释为删除 presence。已选场景明确禁止
   某 Optional 参数时，必须产出该参数 `is None` 的可执行约束。
2. `constraints_in_parameters` 仅保留与选定模板一致的业务分支；未选模板自身的计算规则
   不产出，但用于保证已选模板合法的排除规则必须保留。例如已选 non-quant 时，quant /
   pseudo-quant 专属 Optional 参数必须产出 `is None`，不能随未选模板规则一起删除。
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
    known_conflicts: list[dict] | None = None,
) -> dict:
    return {
        "enabled": scope != "off",
        "scope": scope,
        "device_types": device_types or [],
        "selection": selection or {},
        "param_modes": param_modes or {},
        "selection_policy": dict(SELECTION_POLICY),
        "known_conflicts": known_conflicts or [],
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
            "{device_types, selection:{device:{template: None|\"fix_all_default\"|{param:[values]}}}}. "
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
        # all devices, all templates, Q3 skipped → document-adaptive (None)
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
    # lazy import: avoid a module-load cycle (check_scene_conflicts imports
    # helpers from this module at its top level).
    from check_scene_conflicts import detect_conflicts
    conflicts = detect_conflicts(scan, sel_devices, selection_resolved)
    directive_text = _render_directive(
        scan, sel_devices, selection_resolved, param_modes, conflicts
    )
    inputs_dir.mkdir(parents=True, exist_ok=True)
    directive_path.write_text(directive_text, encoding="utf-8")
    _write_run_state_scene(
        run_dir,
        _scene_payload(
            "subset", scan_path, scan, sel_devices, selection_resolved,
            param_modes, directive_path, conflicts
        ),
    )
    n_templates = sum(len(v) for v in selection_resolved.values())
    print(json.dumps(
        {"ok": True, "scope": "subset", "directive": str(directive_path),
         "n_devices": len(sel_devices), "n_templates": n_templates,
         "n_conflicts": len(conflicts), "known_conflicts": conflicts,
         "warnings": warnings, "scene_scan": str(scan_path)},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
