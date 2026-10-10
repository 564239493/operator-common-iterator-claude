"""Render ``inputs/scene_directive.md`` from the user's text-input scene
selection (``inputs/selection.json``) and persist the scene payload to
``run_state.json``.

文本直输模式（唯一场景路径）：用户以文本给出场景描述（``--scenes "<场景描述>"``
或交互输入），主协调器做简单文字匹配（设备 / 量化场景 / 参数取值）对齐文档后写
``selection.json``：

``{device_types:[...], selection:{device:{template: None|{param:[values]}}}}``

``_param_modes_noscan`` resolves explicitly selected parameters into two states:
``{"expand": [values]}`` (candidate set = the user's selected value subset) |
``{"fix": X}`` (single value). A missing key is not a third pruning state: the
extractor continues from the operator document and adapts the parameter to the
selected scene. If the selected scene forbids an Optional parameter, the
extractor must emit ``param is None``. The directive carries this policy in a
machine block ``<!-- scene: {device_types, selection, param_modes, selection_policy} -->``
(the extractor reads only the directive).

设备类型是硬性必要项：``device_types`` 为空 → exit 2（``DEVICE_REQUIRED``）；
未选出设备不得渲染 directive、不得进入 EXTRACT。

Scope:

- ``off``    — scene disabled; write run_state.scene(enabled=false, scope=off),
  do NOT write a directive file. Extractor sees no directive → no pruning.
- ``subset`` — user-chosen scenes; write run_state.scene(scope=subset,
  device_types, selection, param_modes) AND write
  inputs/scene_directive.md with the pruning instructions.

> legacy 三级场景扫描流程（scene-scanner 扫 scene_scan.json + Q1→Q2→Q3 征询 +
> check_scene_conflicts 冲突识别）已整体下线：``--scan`` 参数与 ``--scope all``
> 分支已移除，完整原实现见 git 历史。

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

from run_state import save_run_state

SELECTION_POLICY = {
    "selected_param": "fix_or_expand",
    "unselected_param": "document_adaptive",
    "forbidden_optional": "emit_is_none",
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _scalar_eq(a: Any, b: Any) -> bool:
    """Type-aware scalar equality (bool is a subclass of int in Python, so
    ``True == 1`` / ``False == 0``; keep bool matching only bool to avoid a
    user-supplied ``1``/``0`` silently matching a bool ``true``/``false``
    param value, and vice-versa)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def _resolve_selection_noscan(
    selection: dict,
) -> tuple[list[str], list[str], list[str], dict]:
    """文本直输模式（无 scene_scan）：仅做结构校验，不做枚举交叉校验。

    selection 结构：``{"device_types": [...], "selection": {device:
    {template: None|{param: [values]}}}}``；tpl_value 仅支持 ``None``（保持自动）
    与 ``{param: [values]}``（显式取值，值不与枚举比对——由 extractor 提取阶段
    对齐文档参数表）。``"fix_all_default"`` 无场景枚举支撑，文本模式不支持。

    **设备类型是硬性必要项**：``device_types`` 非空字符串清单缺失 →
    ``DEVICE_REQUIRED`` 错误（exit 2），不得渲染 directive。

    Returns ``(errors, warnings, sel_devices, selection_resolved)``.
    """
    errors: list[str] = []
    warnings: list[str] = []
    raw_devices = selection.get("device_types")
    sel_devices = (
        [d for d in raw_devices if isinstance(d, str) and d.strip()]
        if isinstance(raw_devices, list)
        else []
    )
    dropped = [
        d for d in (raw_devices or [])
        if not (isinstance(d, str) and d.strip())
    ] if isinstance(raw_devices, list) else []
    if dropped:
        warnings.append(
            f"device_types contains empty/non-string entries; dropped: {dropped!r}"
        )
    if not sel_devices:
        errors.append(
            "DEVICE_REQUIRED: device_types must be a non-empty list of device "
            "full-name strings — 至少选择一个设备类型（必要项），未选出设备不得渲染 "
            "directive、不得进入 EXTRACT"
        )

    raw_sel = selection.get("selection")
    if not isinstance(raw_sel, dict) or not raw_sel:
        errors.append(
            "selection.selection must be a non-empty dict "
            "{device: {template: null|{param:[values]}}}"
        )
        raw_sel = {}

    resolved: dict[str, dict] = {}
    for d, raw_dev in raw_sel.items():
        if not isinstance(d, str) or not d:
            errors.append(f"selection key must be device name string: {d!r}")
            continue
        if d not in sel_devices:
            errors.append(f"selection device {d!r} not in device_types")
            continue
        if not isinstance(raw_dev, dict) or not raw_dev:
            errors.append(f"selection[{d!r}] must be a non-empty dict {{template: ...}}")
            continue
        dev_resolved: dict[str, Any] = {}
        for t, feats in raw_dev.items():
            if not isinstance(t, str) or not t:
                errors.append(
                    f"selection[{d!r}] template key must be non-empty string: {t!r}"
                )
                continue
            if feats is None:
                dev_resolved[t] = None
            elif isinstance(feats, dict):
                subset: dict[str, list] = {}
                for pname, vlist in feats.items():
                    if not isinstance(pname, str) or not pname:
                        errors.append(
                            f"selection[{d!r}][{t!r}] param key must be non-empty "
                            f"string: {pname!r}"
                        )
                        continue
                    if not isinstance(vlist, list) or not vlist:
                        errors.append(
                            f"selection[{d!r}][{t!r}][{pname!r}] must be a non-empty "
                            "list of values"
                        )
                        continue
                    subset[pname] = [
                        v if isinstance(v, (int, float, bool, str)) else str(v)
                        for v in vlist
                    ]
                dev_resolved[t] = subset if subset else None
            elif feats == "fix_all_default":
                errors.append(
                    f"selection[{d!r}][{t!r}]='fix_all_default' 文本直输模式不支持"
                    "（无场景枚举）；请显式列出各参数取值 {param:[values]}"
                )
            else:
                errors.append(
                    f"selection[{d!r}][{t!r}] must be null or {{param:[values]}}; "
                    f"got {feats!r}"
                )
        if dev_resolved:
            resolved[d] = dev_resolved

    if not resolved:
        errors.append("EMPTY_SCENE: selection yields no selected templates")

    return errors, warnings, sel_devices, resolved


def _param_modes_noscan(
    sel_devices: list[str], selection_resolved: dict
) -> dict[str, dict]:
    """文本直输模式：直接从 selection_resolved 派生 param_modes（无 scan 枚举）。

    单值 → fix；多值 → expand；None（保持自动）→ 无显式 mode（文档自适应）。
    """
    out: dict[str, dict] = {}
    for d in sel_devices:
        tmap = selection_resolved.get(d, {})
        param_state: dict[str, object] = {}
        for _t, sel in tmap.items():
            if not isinstance(sel, dict):
                continue
            for pname, subset in sel.items():
                if len(subset) == 1:
                    contrib_mode, contrib_vals = "fix", subset[0]
                else:
                    contrib_mode, contrib_vals = "expand", list(subset)
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
        if param_state:
            out[d] = param_state
    return out


def _render_directive_noscan(
    sel_devices: list[str],
    selection_resolved: dict,
    param_modes: dict[str, dict],
) -> str:
    """文本直输模式的 directive：机读块由 selection 派生（无 scene_scan 来源）。"""
    lines: list[str] = []
    for d in sel_devices:
        lines.append(f"**{d}**:")
        for t, sel in (selection_resolved.get(d, {}) or {}).items():
            lines.append(f"- **{t}**:")
            if sel is None:
                lines.append("  - 参数：保持自动（按文档和已选场景适配）")
                continue
            for pname, subset in sel.items():
                tag = (
                    f"固定取单值 {subset[0]!r}"
                    if len(subset) == 1
                    else f"展开取值分支（取值清单 {subset}）"
                )
                lines.append(f"  - {pname}: {tag}")
    listing = "\n".join(lines) if lines else "(无)"

    machine = json.dumps(
        {
            "device_types": sel_devices,
            "selection": selection_resolved,
            "param_modes": param_modes,
            "selection_policy": dict(SELECTION_POLICY, scope="exclusive"),
            "known_conflicts": [],
        },
        ensure_ascii=False,
    )

    return f"""## 场景指令（run 级，本次提取范围——文本直输模式）

由 `scripts/render_scene_directive.py` 渲染；来源：用户文本输入的场景描述
（无枚举交叉校验——参数名/取值以文档实际参数表为准）。

### 选定设备 / 量化场景 / 参数取值

{listing}

### 提取要求

1. **仅提取所选设备与所选场景**：`product_support` 按机读块 `device_types` 与文档
   "产品支持情况" √ 行取交集，未列设备不产出；约束条目以**所选场景可达**为准——
   触发条件在所选参数取值下恒为假的条目、以及未选量化场景专属的条目，不产出；
   与所选场景共用的基础约束（dtype 合法域、通用 shape 关系等）照常保留。
2. **选择内容是基本限制**：机读块 `param_modes` 中 `{{"fix": X}}` 参数取单值 X、
   `{{"expand": [取值清单]}}` 参数按清单收窄，依赖其取值的约束（自身
   `allowed_range_value`、`dtype`/`format`/`dimensions` 条件分支、
   `constraints_in_parameters` 行）同此收窄；缺键参数按文档和已选场景适配。
   已选场景明确禁止的 Optional 参数必须产出 `param is None`。
3. 与参数取值**无关**的通用约束（shape_equality、维度、groupType 等）原样保留，
   不得因场景删除。
4. 未选量化场景/分支的专属 Optional 参数（如已选非量化时的 quant / pseudo-quant
   专属参数）必须产出 `is None` 可执行约束，不能随未选场景规则一起删除。
5. 落盘后照常跑 `normalize_constraints.py` + `validate_artifacts.py constraints`；
   结果必须仍满足 `OperatorRule`。

<!-- scene: {machine} -->
"""


def _scene_payload(
    scope: str,
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
        "selection_policy": dict(SELECTION_POLICY),
        "known_conflicts": [],
        "directive": str(directive_path) if directive_path else "",
        "scan": "",
    }


def _write_run_state_scene(run_dir: Path, scene_payload: dict) -> None:
    """Merge scene into run_state.json, bumping updated_at; preserve other fields."""
    state_path = run_dir / "run_state.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"run_state.json not found: {state_path}")
    state = _load_json(state_path)
    if not isinstance(state, dict):
        raise ValueError("run_state.json root must be an object")
    source = (
        "scenes_param"
        if str(state.get("scenes") or "").strip()
        else "interactive_input"
    )
    scene_payload = dict(scene_payload)
    scene_payload["source"] = source
    state["scene"] = scene_payload
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_run_state(state_path, state, trailing_newline=False)


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Render the scene directive and persist the selection to run_state."
            " Called by the orchestrator's SCENE_SCAN sub-step (after the user's"
            " text scene description has been matched to the operator document)."
        )
    )
    p.add_argument(
        "--selection",
        help=(
            "{device_types, selection:{device:{template: None|{param:[values]}}}}. "
            "Required for --scope subset."
        ),
    )
    p.add_argument("--run-dir", required=True, help="run directory (contains run_state.json + inputs/)")
    p.add_argument(
        "--scope",
        choices=("subset", "off"),
        required=True,
        help="subset=user-chosen scenes (write directive); off=disabled (no directive)",
    )
    args = p.parse_args()

    run_dir = Path(args.run_dir).resolve()
    inputs_dir = run_dir / "inputs"
    directive_path = inputs_dir / "scene_directive.md"

    # ----- off ------------------------------------------------------------- #
    if args.scope == "off":
        _write_run_state_scene(run_dir, _scene_payload("off"))
        print(json.dumps(
            {"ok": True, "scope": "off", "directive": ""},
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

    errors, warnings, sel_devices, selection_resolved = _resolve_selection_noscan(
        selection
    )
    if errors:
        code = "INVALID_SELECTION"
        if any("DEVICE_REQUIRED" in e for e in errors):
            code = "DEVICE_REQUIRED"
        elif any("EMPTY_SCENE" in e for e in errors):
            code = "EMPTY_SCENE"
        print(json.dumps(
            {"ok": False, "code": code,
             "errors": errors, "warnings": warnings},
            ensure_ascii=False,
        ))
        return 2
    param_modes = _param_modes_noscan(sel_devices, selection_resolved)
    directive_text = _render_directive_noscan(
        sel_devices, selection_resolved, param_modes
    )
    inputs_dir.mkdir(parents=True, exist_ok=True)
    directive_path.write_text(directive_text, encoding="utf-8")
    _write_run_state_scene(
        run_dir,
        _scene_payload(
            "subset", sel_devices, selection_resolved, param_modes, directive_path
        ),
    )
    n_templates = sum(len(v) for v in selection_resolved.values())
    print(json.dumps(
        {"ok": True, "scope": "subset", "directive": str(directive_path),
         "n_devices": len(sel_devices), "n_templates": n_templates,
         "warnings": warnings},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
