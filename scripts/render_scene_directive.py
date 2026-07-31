#!/usr/bin/env python3
"""Render the run-level scene directive and persist the scene selection.

Deterministic glue between the scene-scan Agent output and the
constraint-extractor Agent input. It does three things:

1. Validates the user's selection (a single quant mode + a single width)
   against what the scene-scan found in the operator doc — the selected
   (mode, width) must be one of ``scene_scan.json``'s ``valid_combos``.
   An inconsistent selection exits 2 and blocks EXTRACT (no silent fallback).
   Scene selection is single-select: exactly one quant mode and at most one
   width.
2. Computes the effective ``valid_combos`` = user selection ∩ scan (a single
   combo for scope=subset), logs any dropped combos as warnings (no silent
   truncation), and renders ``inputs/scene_directive.md`` — the prose the
   extractor reads to scope presence/Constraints to the chosen scene.
3. Writes the ``scene`` field back into ``run_state.json`` (the single source
   of truth), bumping ``updated_at``. Other run_state fields are preserved.

Scope (decided by the orchestrator from ``--scene`` + ``has_quant``):

- ``off``  — scene disabled; write run_state.scene(enabled=false, scope=off,
  quant_mode=null, quant_width=null), do NOT write a directive file.
  Extractor sees no directive → no pruning.
- ``all``  — all quant scenarios in the doc; write run_state.scene(scope=all,
  quant_mode=null, quant_width=null, valid_combos = full scan copy), do NOT
  write a directive file (no pruning).
- ``subset`` — user-chosen single (mode, width); write run_state.scene(
  scope=subset, quant_mode=<sel>, quant_width=<sel>, valid_combos=[1 combo])
  AND write inputs/scene_directive.md with the pruning instructions.

The directive file is written ONLY for ``subset``. Existence of
``inputs/scene_directive.md`` is the extractor's signal to prune.

``scene_token`` is written into ``run_state.scene`` for every scope:
``quant-{width}``/``quant`` (量化), ``dequant-{width}``/``dequant``
(伪量化), ``noquant`` (非量化) for ``subset``; ``null`` for ``all``/``off``.
It is a deterministic mapping from the single-select (mode, width), consumed
later when ``generator.py`` adds ``--scene`` support; the render/extract path
does not use it.
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


def _scene_token(mode: str | None, width: str | None) -> str | None:
    """Map (quant_mode, quant_width) → generator ``--scene`` token.

    量化→ ``quant-{width}``（width 为 null 时 ``quant``）；伪量化→
    ``dequant-{width}``（null 时 ``dequant``）；非量化→ ``noquant``。
    mode=None（scope=all/off）→ None。

    Token 落 ``run_state.scene.scene_token``；generator.py 改进支持 ``--scene``
    后由执行链路透传，本脚本只负责确定性地算出值，render/extract 路径不消费。
    """
    if mode == "非量化":
        return "noquant"
    if mode == "量化":
        return f"quant-{width}" if width else "quant"
    if mode == "伪量化":
        return f"dequant-{width}" if width else "dequant"
    return None


def _match_selection(
    scan_combos: list[dict],
    sel_mode: str,
    sel_width: str | None,
) -> tuple[list[dict], list[dict]]:
    """Return (selected_combos, dropped_combos) from scan.valid_combos.

    Single-select: a scan combo {mode, width} is "selected" when its mode
    equals sel_mode AND (its width is None OR its width equals sel_width).
    The width=None case covers mode-only scenarios like 非量化 that carry no
    width, and the case where the doc does not subdivide widths for that mode.
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
    """Return (errors, warnings, sel_mode, sel_width). errors non-empty → abort.

    Selection is single-select: exactly one quant_mode (str, required) and at
    most one quant_width (str or null).
    """
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

    # Selected width (if not None) must be a real width for the selected mode
    # in the scan.
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
    """Render the prose directive consumed by constraint-extractor."""
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


def _write_run_state_scene(
    run_dir: Path, scene_payload: dict
) -> None:
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
        "scene_token": _scene_token(sel_mode, sel_width),
        "valid_combos": valid_combos or [],
        "directive": str(directive_path) if directive_path else "",
        "scan": str(scan_path),
    }


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
            "path to selection JSON {quant_mode, quant_width} (single-select);"
            " required for --scope subset, ignored otherwise"
        ),
    )
    p.add_argument("--run-dir", required=True, help="run directory (contains run_state.json + inputs/)")
    p.add_argument(
        "--scope",
        choices=("subset", "all", "off"),
        required=True,
        help="subset=user-chosen single (mode,width) (write directive); all=full scan (no directive); off=disabled (no directive)",
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

    if args.scope == "off":
        _write_run_state_scene(
            run_dir, _scene_payload("off", scan_path, scan)
        )
        print(json.dumps(
            {"ok": True, "scope": "off", "directive": "", "scene_scan": str(scan_path)},
            ensure_ascii=False,
        ))
        return 0

    if args.scope == "all":
        valid_combos = list(scan.get("valid_combos") or [])
        _write_run_state_scene(
            run_dir,
            _scene_payload(
                "all", scan_path, scan,
                sel_mode=None, sel_width=None,
                valid_combos=valid_combos,
            ),
        )
        # No directive file for scope=all (no pruning). Extractor sees no directive.
        print(json.dumps(
            {"ok": True, "scope": "all", "directive": "", "n_combos": len(valid_combos),
             "scene_scan": str(scan_path)},
            ensure_ascii=False,
        ))
        return 0

    # scope == subset
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


if __name__ == "__main__":
    raise SystemExit(main())
