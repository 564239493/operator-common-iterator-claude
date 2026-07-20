#!/usr/bin/env python3
"""把用户对 conflict-doc.md 的裁决确定性合并进 constraints.json。

输入两个文件（均落 inputs/）：

- ``conflict_candidates.json``: source-analyst 产的结构化冲突候选，每项
  ``{conflict_id, target_platform, doc_expr, proposed_source, source_location,
     error_string}``。``doc_expr`` = 文档提取的原 expr 精确文本（replace 的
  match_expr）；``proposed_source`` = ``{expr_type, expr, relation_params}``
  （源码版约束）。
- ``conflict_resolution.json``: 用户裁决，每项
  ``{conflict_id, winner: "source"|"doc", note}``。

source-wins 的条目 → ``replace_constraint`` patch（match_expr=doc_expr，
proposed=proposed_source，origin="conflict_resolution"），复用
``apply_supplement_constraints.apply_patch`` 合并 + 重跑 normalize+validate。
doc-wins 丢弃（保留文档约束不动）。

确定性合并，不做业务推理。冲突必须人工裁决（source-wins/doc-wins），本脚本
不替用户决定。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# 复用 apply_supplement_constraints 的合并器（add/replace + revalidate 已实现）。
sys.path.insert(0, str(SCRIPTS))
from apply_supplement_constraints import apply_patch  # noqa: E402


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def build_patch(candidates: list[dict], resolution: list[dict]) -> tuple[list[dict], list[str]]:
    """join candidates × resolution on conflict_id，source-wins 转 replace patch。

    返回 (patch_items, log)。doc-wins 与未裁决项跳过（log 记录）。
    """
    reso_map = {item["conflict_id"]: item for item in resolution if isinstance(item, dict)}
    patch: list[dict] = []
    log: list[str] = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        cid = cand.get("conflict_id")
        if cid is None:
            continue
        ritem = reso_map.get(cid)
        if ritem is None:
            log.append(f"skip {cid}: 未裁决（保留文档约束）")
            continue
        winner = str(ritem.get("winner", "")).strip().lower()
        if winner == "doc":
            log.append(f"skip {cid}: doc-wins（保留文档约束）")
            continue
        if winner != "source":
            log.append(f"skip {cid}: winner 非法 {winner!r}（须 source/doc）")
            continue
        proposed = cand.get("proposed_source")
        if not isinstance(proposed, dict):
            log.append(f"skip {cid}: proposed_source 缺失或非对象")
            continue
        patch.append({
            "op": "replace_constraint",
            "target_platform": cand.get("target_platform", "all"),
            "match_expr": cand.get("doc_expr", ""),
            "proposed": {
                "expr_type": proposed.get("expr_type", ""),
                "expr": proposed.get("expr", ""),
                "relation_params": proposed.get("relation_params", []),
            },
            "basis": f"conflict_resolution {cid}: {ritem.get('note', '')}".strip(),
        })
        log.append(f"replace {cid}: source-wins")
    return patch, log


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把用户对 conflict-doc.md 的裁决合并进 constraints.json。"
    )
    parser.add_argument("constraints", help="constraints.json 路径（原地合并改写）")
    parser.add_argument("--candidates", required=True,
                        help="conflict_candidates.json 路径（source-analyst 产）")
    parser.add_argument("--resolution", required=True,
                        help="conflict_resolution.json 路径（用户裁决）")
    parser.add_argument("--skip-revalidate", action="store_true",
                        help="仅合并，不重跑 normalize+validate（调试用）")
    args = parser.parse_args()

    constraints_path = Path(args.constraints).resolve()
    candidates_path = Path(args.candidates).resolve()
    resolution_path = Path(args.resolution).resolve()
    for label, p in (("constraints", constraints_path),
                     ("candidates", candidates_path),
                     ("resolution", resolution_path)):
        if not p.is_file():
            print(json.dumps({"ok": False, "code": f"{label.upper()}_NOT_FOUND",
                              label: str(p)}, ensure_ascii=False))
            return 2

    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    if not isinstance(constraints, dict):
        print(json.dumps({"ok": False, "code": "CONSTRAINTS_NOT_OBJECT"}, ensure_ascii=False))
        return 2
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    if not isinstance(candidates, list):
        print(json.dumps({"ok": False, "code": "CANDIDATES_NOT_LIST"}, ensure_ascii=False))
        return 2
    resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
    if not isinstance(resolution, list):
        print(json.dumps({"ok": False, "code": "RESOLUTION_NOT_LIST"}, ensure_ascii=False))
        return 2

    patch, log = build_patch(candidates, resolution)
    if not patch:
        print(json.dumps({"ok": True, "applied": 0, "ops": log,
                          "constraints": str(constraints_path)}, ensure_ascii=False))
        return 0

    # 备份留痕（与 .pre_supplement 分开，记录冲突合并前的状态）。
    backup = constraints_path.with_name(constraints_path.stem + ".json.pre_conflict")
    if not backup.exists():
        shutil.copy2(constraints_path, backup)

    try:
        constraints, apply_log = apply_patch(constraints, patch, origin="conflict_resolution")
    except ValueError as exc:
        print(json.dumps({"ok": False, "code": "PATCH_APPLY_FAILED",
                          "error": str(exc)}, ensure_ascii=False))
        return 2

    constraints_path.write_text(
        json.dumps(constraints, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if not args.skip_revalidate:
        norm_rc, norm_out = _run([sys.executable, str(SCRIPTS / "normalize_constraints.py"),
                                  str(constraints_path)])
        if norm_rc != 0:
            sys.stderr.write(norm_out)
            print(json.dumps({"ok": False, "code": "NORMALIZE_FAILED",
                              "constraints": str(constraints_path)}, ensure_ascii=False))
            return 2
        val_rc, val_out = _run([sys.executable, str(SCRIPTS / "validate_artifacts.py"),
                                "constraints", str(constraints_path)])
        if val_rc != 0:
            sys.stderr.write(val_out)
            print(json.dumps({"ok": False, "code": "VALIDATE_FAILED",
                              "constraints": str(constraints_path)}, ensure_ascii=False))
            return 2

    print(json.dumps({"ok": True, "applied": len(apply_log),
                      "ops": apply_log + log,
                      "constraints": str(constraints_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
