"""Detect inter-parameter value conflicts in a user's three-level scene selection.

Called by the orchestrator's SCENE_SCAN sub-step **after** the user has answered
Q1/Q2/Q3 (selection.json assembled) and **before**
`render_scene_directive.py --scope subset` renders the directive. This is a
deterministic check over the structured ``value_conflicts`` rules that
scene-scanner extracted into ``scene_scan.json`` (see ``prompts/scan_scenes.md``
§4/§5). Conflicts are **advisory** (exit 0): the orchestrator asks the user
whether to revise the feature params or force-continue; on force-continue the
conflicts are stamped into ``scene_directive.md`` as ``known_conflicts`` by
``render_scene_directive.py`` (which imports ``detect_conflicts`` from here).

Conflict rule schema (per param, optional ``value_conflicts`` list):

    { "when_self": [v1, ...],   # optional: this param taking these values triggers
      "target": "<param>",      # required: a selectable param in the same template
      "forbidden": [v1, ...],   # mutually exclusive with required
      "required":  [v1, ...],   # mutually exclusive with forbidden
      "reason": "..." }

A conflict is flagged **only when both the param (``self``) and ``target`` are
explicitly chosen by the user** (fix or expand). If either is automatic /
document-adaptive, no conflict is raised here — the extractor adapts the
automatic side downstream to a compatible value.

Reuses ``render_scene_directive._resolve_selection`` (value-legality validation
+ selection resolution) so this script never re-implements the resolution logic.
Import direction: check → render (helpers); render → check (detect_conflicts,
lazy import inside the subset branch to avoid a module-load cycle).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from render_scene_directive import (
    _devices_by_name,
    _load_json,
    _resolve_selection,
    _scalar_eq,
    _tpl_param_values,
)


def _explicit_choice(
    sel: Any, pname: str, values: list
) -> list | None:
    """The user's explicitly chosen values for ``pname`` under a resolved
    template selection, or ``None`` when the param is left automatic.

    - ``sel is None``                       → template preset 1 (automatic)
    - ``sel == "fix_all_default"``          → ``[values[0]]`` (preset 2)
    - ``sel`` is a dict with ``pname`` key  → that subset (expand)
    - ``sel`` is a dict without ``pname``   → automatic (document-adaptive)
    """
    if sel is None:
        return None
    if isinstance(sel, str) and sel == "fix_all_default":
        return [values[0]] if values else None
    if isinstance(sel, dict):
        chosen = sel.get(pname)
        return chosen if chosen else None
    return None


def detect_conflicts(
    scan: dict, sel_devices: list[str], selection_resolved: dict
) -> list[dict]:
    """Return a list of conflict records for the given resolved selection.

    Each record::

        {device, template, param, param_values, target, target_values,
         kind: "forbidden"|"required", rule, when_self, reason}
    """
    conflicts: list[dict] = []
    devices_by_name = _devices_by_name(scan)
    for d in sel_devices:
        dev = devices_by_name.get(d)
        if not isinstance(dev, dict):
            continue
        sel_templates = selection_resolved.get(d, {})
        for t in (dev.get("templates") or []):
            if not isinstance(t, dict):
                continue
            tname = t.get("template")
            if not isinstance(tname, str) or tname not in sel_templates:
                continue
            sel = sel_templates.get(tname)
            param_values = _tpl_param_values(t)  # {pname: scan values}
            for fp in (t.get("feature_params") or []):
                if not isinstance(fp, dict):
                    continue
                for p in (fp.get("params") or []):
                    if not isinstance(p, dict):
                        continue
                    pname = p.get("name")
                    if not isinstance(pname, str):
                        continue
                    vc = p.get("value_conflicts")
                    if not isinstance(vc, list):
                        continue
                    p_chosen = _explicit_choice(sel, pname, param_values.get(pname, []))
                    if p_chosen is None:
                        continue  # self param automatic → nothing to conflict against
                    for entry in vc:
                        if not isinstance(entry, dict):
                            continue
                        target = entry.get("target")
                        if not isinstance(target, str) or not target.strip():
                            continue
                        when_self = entry.get("when_self")
                        if isinstance(when_self, list) and when_self:
                            # rule active only if a chosen self value is in when_self
                            if not any(
                                any(_scalar_eq(v, w) for w in when_self)
                                for v in p_chosen
                            ):
                                continue
                        target_chosen = _explicit_choice(
                            sel, target, param_values.get(target, [])
                        )
                        if target_chosen is None:
                            continue  # target automatic → extractor adapts downstream
                        forbidden = entry.get("forbidden")
                        required = entry.get("required")
                        if isinstance(forbidden, list) and forbidden:
                            hit = [
                                tv for tv in target_chosen
                                if any(_scalar_eq(tv, f) for f in forbidden)
                            ]
                            if not hit:
                                continue
                            conflicts.append({
                                "device": d,
                                "template": tname,
                                "param": pname,
                                "param_values": p_chosen,
                                "target": target,
                                "target_values": target_chosen,
                                "kind": "forbidden",
                                "rule": {"forbidden": forbidden},
                                "when_self": when_self if isinstance(when_self, list) and when_self else None,
                                "reason": entry.get("reason") or "",
                            })
                        elif isinstance(required, list) and required:
                            bad = [
                                tv for tv in target_chosen
                                if not any(_scalar_eq(tv, r) for r in required)
                            ]
                            if not bad:
                                continue
                            conflicts.append({
                                "device": d,
                                "template": tname,
                                "param": pname,
                                "param_values": p_chosen,
                                "target": target,
                                "target_values": target_chosen,
                                "kind": "required",
                                "rule": {"required": required},
                                "when_self": when_self if isinstance(when_self, list) and when_self else None,
                                "reason": entry.get("reason") or "",
                            })
    return conflicts


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Detect inter-parameter value conflicts in a three-level scene"
            " selection (advisory; exit 0 even when conflicts are found)."
            " Called by the orchestrator after Q3, before render_scene_directive."
        )
    )
    p.add_argument("--scan", required=True, help="path to inputs/scene_scan.json")
    p.add_argument(
        "--selection",
        help="path to selection.json (or - for stdin)",
    )
    p.add_argument("--run-dir", required=True, help="run directory (writes inputs/scene_conflicts.json)")
    args = p.parse_args()

    scan_path = Path(args.scan).resolve()
    if not scan_path.is_file():
        print(json.dumps(
            {"ok": False, "code": "SCENE_SCAN_NOT_FOUND", "path": str(scan_path)},
            ensure_ascii=False,
        ))
        return 2
    scan = _load_json(scan_path)

    sel_path = Path(args.selection) if args.selection else None
    if sel_path is None or str(sel_path) == "-":
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
            {"ok": False, "code": code, "errors": errors, "warnings": warnings},
            ensure_ascii=False,
        ))
        return 2

    conflicts = detect_conflicts(scan, sel_devices, selection_resolved)
    report = {
        "ok": True,
        "n_conflicts": len(conflicts),
        "conflicts": conflicts,
        "warnings": warnings,
    }
    out_dir = Path(args.run_dir).resolve() / "inputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "scene_conflicts.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())