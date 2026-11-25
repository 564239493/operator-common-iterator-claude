#!/usr/bin/env python3
"""按用户选择扩容 constraint_check 轮次预算（CHECK/REPAIR 子循环）。

``constraint-checker`` 在 ``current_round == max_rounds`` 且仍有 open/unfixed 问题时按规则写 ``status=failed``；
该状态只表示**本 iteration 的检查轮次预算耗尽**，不代表问题不可修复。用户选择「继续修复」时，主协调器调用本脚本：

1. 校验报告确实处于「轮次用尽型 failed」；
2. 把报告 ``max_rounds`` 提升 ``--extra-rounds`` 轮、``status`` 改回``needs_repair``
（``current_round < max_rounds``，满足``validate_artifacts constraint_check`` 的状态规则）；
3. 同步 ``run_state.constraint_check`` 的 ``max_rounds/status`` 并刷新``updated_at``。
新预算随 run_state 保留（``--reset`` 继承），本次 run 内后续 iteration 同样生效。

随后按原 open/unfixed 继续 constraint-repairer → 新一轮 checker 复检。
``--dry-run`` 只输出将要发生的变更，不落盘。

用法:
  extend_constraint_check.py --run-dir <run-dir> [--extra-rounds 3] [--dry-run]

exit code: 0 成功（含 dry-run）；2 run/报告不可读或参数非法；3 报告不满足扩容前置条件（未落盘）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_state import save_run_state

# CHECK/REPAIR 子循环从属于 EXTRACT 或 UPDATE_CONSTRAINTS，其余阶段不可扩容
ALLOWED_CURRENT_STATES = ("EXTRACT", "UPDATE_CONSTRAINTS")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fail(code: int, error: str, **extra: Any) -> int:
    print(json.dumps({"ok": False, "error": error, **extra}, ensure_ascii=False))
    return code


def _load_json(path: Path, label: str) -> tuple[dict | None, str | None]:
    if not path.is_file():
        return None, f"{label} not found: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{label} unreadable: {path} ({exc})"
    if not isinstance(data, dict):
        return None, f"{label} root must be an object: {path}"
    return data, None


def _has_trailing_newline(path: Path) -> bool:
    data = path.read_bytes()
    return bool(data) and data.endswith(b"\n")


def _write_json(path: Path, data: dict) -> None:
    """落盘保持文件现有尾换行状态（不增不删）。"""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if _has_trailing_newline(path):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="扩容 constraint_check 轮次预算（用户选择继续修复时使用）")
    parser.add_argument("--run-dir", required=True, help="run 目录（含 run_state.json）")
    parser.add_argument("--extra-rounds", type=int, default=3,
                        help="本次追加的检查轮数（默认 3）")
    parser.add_argument("--dry-run", action="store_true", help="只输出变更，不落盘")
    args = parser.parse_args()

    if args.extra_rounds < 1:
        return _fail(2, "--extra-rounds must be a positive integer")

    run_dir = Path(args.run_dir)
    state_path = run_dir / "run_state.json"
    state, err = _load_json(state_path, "run_state.json")
    if err:
        return _fail(2, err)
    assert state is not None

    current_state = str(state.get("state") or "")
    if current_state not in ALLOWED_CURRENT_STATES:
        return _fail(
            3,
            f"run_state.state={current_state!r} 不是 CHECK/REPAIR 所属阶段"
            f"（仅 {' / '.join(ALLOWED_CURRENT_STATES)} 可扩容）")

    iteration = int(state.get("current_iteration") or 1)
    check = state.get("constraint_check")
    check = check if isinstance(check, dict) else {}
    report_rel = str(check.get("report") or f"iter_{iteration:03d}/constraint_check.json")
    report_path = Path(report_rel)
    if not report_path.is_absolute():
        report_path = run_dir / report_rel
    report, err = _load_json(report_path, "constraint_check report")
    if err:
        return _fail(2, err)
    assert report is not None

    report_iter = int(report.get("iteration") or 0)
    if report_iter != iteration:
        return _fail(
            3,
            f"report.iteration={report_iter} 与 run_state.current_iteration={iteration} 不一致")
    status = str(report.get("status") or "")
    if status != "failed":
        return _fail(
            3,
            f"report.status={status!r}：只有轮次用尽型 failed 报告可扩容"
            "（passed 无问题、needs_repair 预算未用尽，均无需扩容）")
    current_round = int(report.get("current_round") or 0)
    old_max = int(report.get("max_rounds") or 0)
    if current_round != old_max:
        return _fail(
            3,
            f"report.current_round={current_round} != max_rounds={old_max}，"
            "不是轮次用尽型失败")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    active = int(summary.get("open") or 0) + int(summary.get("unfixed") or 0)
    if active <= 0:
        return _fail(3, "report 没有 open/unfixed 问题，无继续修复对象")

    new_max = current_round + args.extra_rounds
    if not args.dry_run:
        report["status"] = "needs_repair"
        report["max_rounds"] = new_max
        _write_json(report_path, report)

        check["iteration"] = iteration
        check["current_round"] = current_round
        check["max_rounds"] = new_max
        check["status"] = "needs_repair"
        check.setdefault("report", report_rel)
        state["constraint_check"] = check
        state["updated_at"] = _now()
        save_run_state(state_path, state, _has_trailing_newline(state_path))

    print(json.dumps({
        "ok": True,
        "applied": not args.dry_run,
        "report": str(report_path),
        "iteration": iteration,
        "current_round": current_round,
        "old_max_rounds": old_max,
        "new_max_rounds": new_max,
        "extra_rounds": args.extra_rounds,
        "status": "needs_repair",
        "active_issues": active,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
