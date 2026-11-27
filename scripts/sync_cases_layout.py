#!/usr/bin/env python3
"""Align a generated cases JSON's dim_axes layout attr to each case's tensor layout.

dim_axes plans tensor shapes/axis order (the ``layout`` field on tensor inputs), but
the combination/solve stage may have written a different ``inputLayout`` attr value.
This tool forces the attr to the actual per-case tensor layout so the case is
self-consistent (declared layout == real axis order). Idempotent.

Usage:
  python scripts/sync_cases_layout.py --cases <cases.json> --operator <aclnnName> \
      [--scene <scene> ...] [--config <dim_axes.json>] [--output <out.json>]

Without --scene, pass a single scene; the layout param must be unambiguous in the
operator's dim_axes profile.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import generate_cases as gc  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, help="input cases JSON (array)")
    parser.add_argument("--operator", required=True, help="aclnn operator name")
    parser.add_argument("--scene", action="append", default=None,
                        help="dim_axes scene name (repeatable); required for resolution")
    parser.add_argument("--config", default=None, help="dim_axes.json path (default)")
    parser.add_argument("--output", default=None,
                        help="output path (default: overwrite --cases)")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        print(json.dumps({"ok": False, "code": "CASES_NOT_ARRAY"}, ensure_ascii=False))
        return 2

    layout_param = gc._dim_axes_layout_param(args.operator, args.scene, args.config)
    if not layout_param:
        print(json.dumps({
            "ok": False,
            "code": "LAYOUT_PARAM_UNRESOLVED",
            "operator": args.operator,
            "scenes": args.scene,
        }, ensure_ascii=False))
        return 2

    changed = gc._sync_layout_param(cases, layout_param)
    output = Path(args.output) if args.output else cases_path
    output.write_text(
        json.dumps(cases, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    print(json.dumps({
        "ok": True,
        "cases": len(cases),
        "layout_param": layout_param,
        "changed": changed,
        "output": str(output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
