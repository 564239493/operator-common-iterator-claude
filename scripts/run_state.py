#!/usr/bin/env python3
"""Deterministic single writer for ``runs/<run-id>/run_state.json``.

所有对 run_state.json 的落盘都必须经过本模块：

- 既有脚本（init_run.py / render_scene_directive.py /
  update_supplement_state.py）import 库函数 ``write_initial`` /
  ``save_run_state`` 完成落盘；
- 主协调器在各触发节点调用 CLI 子命令（set-state / set-fields /
  set-constraint-check），不再手动 Edit 该文件。

设计约束（与手工写入时代逐点等价）：

- 写入时机与内容由调用方决定，本脚本只做机械写入，不含任何业务判断；
- 序列化固定为 ``json.dumps(..., ensure_ascii=False, indent=2)``；
  库函数按调用方现状显式决定尾换行（init_run / render 无、
  update_supplement_state 有），CLI 写回时保持文件原有的尾换行状态；
- ``updated_at`` 仅在 set-constraint-check 刷新（对应 SKILL 中
  constraint_check 子状态回写的要求）；set-state / set-fields 不刷新；
- 唯一附加行为是合法性校验（状态名 / check 状态枚举 / 字段名），
  非法输入 exit 2 且不落盘。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# WORKFLOW.md 状态机全集（含 mermaid 中间状态与全部终态），用于拦截拼写错误。
ALLOWED_STATES = frozenset({
    "PLAN",
    "EXTRACT",
    "GENERATE",
    "EXECUTE",
    "GATE",
    "DIAGNOSE",
    "UPDATE_CONSTRAINTS",
    "MIXED_FAILURE_REVIEW",
    "NEEDS_HUMAN_EVIDENCE",
    "HUMAN_CHECKPOINT",
    "SUCCESS",
    "BLOCKED",
    "MAX_ITERATIONS",
    "STOP_GENERATOR_BUG",
    "STOP_EXECUTOR_BUG",
    "STOPPED_BY_USER",
})

# run_state.constraint_check.status 在 SKILL / 校验器中出现的全部取值。
ALLOWED_CHECK_STATUSES = frozenset({
    "pending",
    "recheck_pending",
    "passed",
    "failed",
    "needs_repair",
})

# init_run.py 创建 run_state.json 时写入的全部顶层键（set-fields 的键名白名单）。
KNOWN_TOP_LEVEL_KEYS = frozenset({
    "run_id", "operator_doc_source", "operator_doc",
    "current_prompt_source", "current_prompt", "current_prompt_modules",
    "source_analysis_knowledge", "prompt_preanalysis",
    "prompt_preanalysis_sha256", "prompt_assembly_record",
    "prompt_assembly_record_sha256", "current_prompt_sha256",
    "prompt_update_decisions", "prompt_update_proposals",
    "supplement_constraints_source", "supplement_constraints",
    "supplement_revision", "supplement_hash",
    "last_consumed_supplement_hash", "supplement_updated_iteration",
    "operator_src_source", "operator_src_snapshot",
    "mode", "server_config", "max_iterations", "constraint_check",
    "case_count", "human_checkpoint_round",
    "human_checkpoint_resolved_iteration", "operator_family",
    "test_framework", "hs_scenario_mode", "run_scope", "scene",
    "execution_strategy", "operator_category", "operator_category_evidence",
    "current_iteration", "state", "history", "created_at", "updated_at",
})

# set-fields 不允许直写的键：state/history 有专用命令语义（set-state 负责
# append history），created_at/updated_at 是时间戳簿记（刷新点与现状一致，
# 不由调用方随意改）。
PROTECTED_KEYS = frozenset({"state", "history", "created_at", "updated_at"})

# constraint_check 子对象的合法子键。
CHECK_SUBKEYS = frozenset({
    "max_rounds", "iteration", "current_round", "status", "report",
})


# ---------------------------------------------------------------------------
# 库层：供既有脚本 import 的落盘函数（唯一 write_text 出口）
# ---------------------------------------------------------------------------

def _validate_state_value(state: dict) -> None:
    value = state.get("state")
    if value is not None and value not in ALLOWED_STATES:
        raise ValueError(f"unknown state value: {value!r}")


def write_initial(run_dir: Path, state: dict) -> None:
    """新建 run_state.json（与 init_run.py 历史落盘逐字节一致：无尾换行）。"""
    if not isinstance(state, dict):
        raise ValueError("run_state payload must be a dict")
    _validate_state_value(state)
    (Path(run_dir) / "run_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def save_run_state(path: Path, state: dict, trailing_newline: bool) -> None:
    """唯一 write_text 出口：尾换行按调用方现状显式传入。"""
    if not isinstance(state, dict):
        raise ValueError("run_state payload must be a dict")
    _validate_state_value(state)
    text = json.dumps(state, ensure_ascii=False, indent=2)
    if trailing_newline:
        text += "\n"
    Path(path).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI 层内部工具
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("run_state.json root must be an object")
    return state


def _has_trailing_newline(path: Path) -> bool:
    data = path.read_bytes()
    return bool(data) and data.endswith(b"\n")


def _save_cli(path: Path, state: dict) -> None:
    """CLI 写回保持文件现有尾换行状态（与手工 Edit 时代一致，不增不删）。"""
    trailing = _has_trailing_newline(path)
    save_run_state(path, state, trailing_newline=trailing)


def _state_path(args: argparse.Namespace) -> Path:
    run_dir = Path(args.run_dir)
    path = run_dir / "run_state.json"
    if not path.is_file():
        raise FileNotFoundError(f"run_state.json not found: {path}")
    return path


# ---------------------------------------------------------------------------
# 子命令：set-state —— 状态迁移（写 state + append history；不动 updated_at）
# ---------------------------------------------------------------------------

def cmd_set_state(args: argparse.Namespace) -> int:
    if args.to not in ALLOWED_STATES:
        raise ValueError(f"unknown state: {args.to!r}")
    path = _state_path(args)
    state = _load(path)
    state["state"] = args.to
    entry: dict[str, Any] = {"state": args.to}
    if args.code:
        entry["code"] = args.code
    if args.event:
        entry["event"] = args.event
    entry["at"] = _now()
    history = state.setdefault("history", [])
    if not isinstance(history, list):
        raise ValueError("run_state.history must be a list")
    history.append(entry)
    if args.bump_iteration:
        state["current_iteration"] = int(state.get("current_iteration", 1)) + 1
    _save_cli(path, state)
    print(json.dumps({
        "ok": True,
        "state": args.to,
        "history_appended": entry,
        "current_iteration": state["current_iteration"],
    }, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# 子命令：set-fields —— 顶层/嵌套字段写入（不动 updated_at）
# ---------------------------------------------------------------------------

def _parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _assign(state: dict, key_path: str, value: Any) -> None:
    parts = key_path.split(".")
    if len(parts) == 1:
        key = parts[0]
        if key not in KNOWN_TOP_LEVEL_KEYS:
            raise ValueError(f"unknown run_state key: {key!r}")
        if key in PROTECTED_KEYS:
            raise ValueError(
                f"key {key!r} must not be written via set-fields "
                "(use set-state / set-constraint-check)"
            )
        state[key] = value
        return
    if len(parts) == 2 and parts[0] == "constraint_check":
        sub = parts[1]
        if sub not in CHECK_SUBKEYS:
            raise ValueError(f"unknown constraint_check sub-key: {sub!r}")
        if sub == "status" and value not in ALLOWED_CHECK_STATUSES:
            raise ValueError(f"invalid constraint_check.status: {value!r}")
        check = state.setdefault("constraint_check", {})
        if not isinstance(check, dict):
            raise ValueError("run_state.constraint_check must be an object")
        check[sub] = value
        return
    raise ValueError(f"unsupported key path: {key_path!r}")


def cmd_set_fields(args: argparse.Namespace) -> int:
    path = _state_path(args)
    state = _load(path)
    updated: dict[str, Any] = {}
    for item in args.set:
        if "=" not in item:
            raise ValueError(f"--set expects KEY=VALUE, got: {item!r}")
        key_path, _, raw_value = item.partition("=")
        value = _parse_value(raw_value)
        _assign(state, key_path, value)
        updated[key_path] = value
    _save_cli(path, state)
    print(json.dumps({
        "ok": True,
        "updated": updated,
    }, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# 子命令：set-constraint-check —— check 子状态维护（刷新 updated_at）
# ---------------------------------------------------------------------------

def cmd_set_constraint_check(args: argparse.Namespace) -> int:
    if args.status is not None and args.status not in ALLOWED_CHECK_STATUSES:
        raise ValueError(f"invalid constraint_check.status: {args.status!r}")
    if args.reset and (args.status is not None or args.current_round is not None):
        raise ValueError("--reset conflicts with --status/--current-round")
    path = _state_path(args)
    state = _load(path)
    check = state.setdefault("constraint_check", {})
    if not isinstance(check, dict):
        raise ValueError("run_state.constraint_check must be an object")
    if args.reset:
        # 保留已配置的 max_rounds（旧 run 缺字段时补 3），其余重置到新 iteration。
        iteration = (
            args.iteration
            if args.iteration is not None
            else int(state.get("current_iteration", 1))
        )
        report = args.report or f"iter_{iteration:03d}/constraint_check.json"
        check = {
            "max_rounds": check.get("max_rounds", 3),
            "iteration": iteration,
            "current_round": 0,
            "status": "pending",
            "report": report,
        }
        state["constraint_check"] = check
    else:
        if args.iteration is not None:
            check["iteration"] = args.iteration
        if args.current_round is not None:
            check["current_round"] = args.current_round
        if args.status is not None:
            check["status"] = args.status
        if args.report is not None:
            check["report"] = args.report
    # SKILL：每次子状态回写同时更新 run_state.updated_at。
    state["updated_at"] = _now()
    _save_cli(path, state)
    print(json.dumps({
        "ok": True,
        "constraint_check": check,
        "updated_at": state["updated_at"],
    }, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="run_state.json 唯一写入器（确定性脚本，不调用 LLM）。"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_state = sub.add_parser(
        "set-state", help="状态迁移：写 state 并 append history（不动 updated_at）"
    )
    p_state.add_argument("--run-dir", required=True, help="run 目录（含 run_state.json）")
    p_state.add_argument("--to", required=True, help="目标状态名（WORKFLOW.md 状态集）")
    p_state.add_argument("--code", default="", help="history 条目附加 code（如 CONSTRAINT_CHECK_FAILED）")
    p_state.add_argument("--event", default="", help="history 条目附加 event（如 CONSTRAINTS_ONLY_SUCCESS）")
    p_state.add_argument(
        "--bump-iteration", action="store_true",
        help="同时 current_iteration += 1（UPDATE_CONSTRAINTS 路由用）",
    )
    p_state.set_defaults(func=cmd_set_state)

    p_fields = sub.add_parser(
        "set-fields", help="写入顶层/constraint_check 字段（不动 updated_at）"
    )
    p_fields.add_argument("--run-dir", required=True, help="run 目录（含 run_state.json）")
    p_fields.add_argument(
        "--set", action="append", required=True, metavar="KEY=VALUE",
        help="可重复；KEY 为顶层键名或 constraint_check.<sub>；VALUE 按 JSON 解析，失败按原始字符串",
    )
    p_fields.set_defaults(func=cmd_set_fields)

    p_check = sub.add_parser(
        "set-constraint-check", help="维护 constraint_check 子状态（刷新 updated_at）"
    )
    p_check.add_argument("--run-dir", required=True, help="run 目录（含 run_state.json）")
    p_check.add_argument("--reset", action="store_true",
                         help="重置到当前 iteration（保留 max_rounds，current_round=0，status=pending）")
    p_check.add_argument("--iteration", type=int, default=None,
                         help="覆盖 iteration（--reset 时缺省取 run_state.current_iteration）")
    p_check.add_argument("--current-round", type=int, default=None,
                         help="回写轮次（每轮 check 结束后）")
    p_check.add_argument("--status", default=None,
                         help="pending | recheck_pending | passed | failed | needs_repair")
    p_check.add_argument("--report", default=None,
                         help="report 路径（--reset 时缺省为 iter_00N/constraint_check.json）")
    p_check.set_defaults(func=cmd_set_constraint_check)

    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(json.dumps({
            "ok": False,
            "code": "RUN_STATE_WRITE_FAILED",
            "error": str(exc),
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
