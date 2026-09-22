#!/usr/bin/env python3
"""确定性状态推进器（flow control hard guard）。

主协调器在每个阶段完成后调用 ``advance``；本脚本读取 run_state.json 与当前
iteration 的落盘产物，按内置迁移规则表（docs/WORKFLOW.md §3 状态机）裁决去向，
并经 ``run_state.save_run_state`` 落盘。模型不决定"迁到哪"，只负责调用。

子命令:
  advance --run-dir <run-dir> [--dry-run] [--user-decision approve|stop]
  evaluate --run-dir <run-dir>            # 等价 advance --dry-run

决策语义:
  TRANSIT  条件满足，迁移到 to_state（--dry-run 时不落盘）
  HOLD     条件未满足的合法等待（如 check 进行中），不迁移
  NOOP     已处于终态，无迁移
  NO_ROUTE 当前状态不在规则表中（需人工）

exit code: TRANSIT/HOLD/NOOP = 0; NO_ROUTE = 4; 输入/产物读取异常 = 2。

落盘约束（与 run_state.py cmd_set_state 语义对齐）:
  - 写 ``state`` 并 append history 条目 ``{state, code?, event?, at}``；
  - UPDATE_CONSTRAINTS / MIXED_FAILURE_REVIEW→UPDATE_CONSTRAINTS 迁移同时
    ``current_iteration += 1``（对应 set-state --bump-iteration）；
  - 不刷新 ``updated_at``；尾换行保持原文件状态。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from run_state import ALLOWED_STATES, save_run_state
from validate_artifacts import (
    validate_analysis,
    validate_cases,
    validate_constraint_update,
    validate_constraints,
    validate_execution,
)

TERMINAL_STATES = frozenset({
    "SUCCESS",
    "BLOCKED",
    "MAX_ITERATIONS",
    "STOP_GENERATOR_BUG",
    "STOP_EXECUTOR_BUG",
    "STOPPED_BY_USER",
})

# 静态边表：合法后继（名字级）。advance 用条件表裁决去向；
# run_state.py 兜底拦截用本表拒绝非法边（条件裁决以 advance 为准）。
EDGES: dict[str, frozenset[str]] = {
    "PLAN": frozenset({"EXTRACT"}),
    "EXTRACT": frozenset({"GENERATE", "SUCCESS", "BLOCKED"}),
    "GENERATE": frozenset({"EXECUTE", "BLOCKED"}),
    "EXECUTE": frozenset({"GATE", "BLOCKED"}),
    "GATE": frozenset({"SUCCESS", "DIAGNOSE", "BLOCKED"}),
    "DIAGNOSE": frozenset({
        "UPDATE_CONSTRAINTS",
        "MAX_ITERATIONS",
        "MIXED_FAILURE_REVIEW",
        "HUMAN_CHECKPOINT",
        "STOP_GENERATOR_BUG",
        "STOP_EXECUTOR_BUG",
        "BLOCKED",
    }),
    "UPDATE_CONSTRAINTS": frozenset({"GENERATE", "BLOCKED"}),
    "MIXED_FAILURE_REVIEW": frozenset({"UPDATE_CONSTRAINTS", "STOPPED_BY_USER"}),
    "NEEDS_HUMAN_EVIDENCE": frozenset({"HUMAN_CHECKPOINT"}),
    "HUMAN_CHECKPOINT": frozenset({"DIAGNOSE", "STOPPED_BY_USER"}),
}
for _terminal in TERMINAL_STATES:
    EDGES[_terminal] = frozenset()


def successors(from_state: str) -> frozenset[str] | None:
    """名字级合法后继；未知状态返回 None（调用方应拒绝一切迁移）。"""
    if from_state not in ALLOWED_STATES:
        return None
    return EDGES.get(from_state, frozenset())


# ---------------------------------------------------------------------------
# 决策上下文与产物读取
# ---------------------------------------------------------------------------


class Ctx:
    """一次评估的只读上下文。"""

    def __init__(self, run_dir: Path, data: dict[str, Any], user_decision: str | None):
        self.run_dir = run_dir
        self.data = data
        self.user_decision = user_decision
        self.state = str(data.get("state") or "")
        self.iteration = int(data.get("current_iteration") or 1)
        self.max_iterations = int(data.get("max_iterations") or 5)
        self.run_scope = str(data.get("run_scope") or "full")
        self.test_framework = str(data.get("test_framework") or "")
        self.iter_dir = run_dir / f"iter_{self.iteration:03d}"

    def read(self, relpath: str) -> tuple[Any | None, str | None]:
        """读 JSON 产物；不存在/坏 JSON 返回 (None, 原因)。"""
        path = self.run_dir / relpath
        if not path.is_file():
            return None, f"missing: {relpath}"
        try:
            return json.loads(path.read_text(encoding="utf-8")), None
        except (OSError, ValueError) as exc:
            return None, f"unreadable: {relpath} ({exc})"

    def check(self) -> dict[str, Any]:
        data, _ = self.read(f"iter_{self.iteration:03d}/constraint_check.json")
        return data if isinstance(data, dict) else {}

    def supplement_changed(self) -> tuple[bool, str]:
        """人工补充事实是否已追加（HUMAN_CHECKPOINT → DIAGNOSE 判据）。

        判据：update_supplement_state.py 已识别到新版本（revision>=2）且
        当前 supplement_hash 尚未被 --consume 消费。
        """
        revision = int(self.data.get("supplement_revision") or 0)
        current_hash = str(self.data.get("supplement_hash") or "")
        consumed = str(self.data.get("last_consumed_supplement_hash") or "")
        if not current_hash:
            return False, "supplement_constraints 未配置，无人工补充通道"
        changed = revision >= 2 and current_hash != consumed
        detail = (
            f"revision={revision} hash_changed_vs_consumed={current_hash != consumed}"
        )
        return changed, detail


def _cond(cid: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": cid, "passed": passed, "detail": detail}


def _decision(
    decision: str,
    to_state: str | None,
    conditions: list[dict[str, Any]],
    *,
    code: str = "",
    event: str = "",
    bump_iteration: bool = False,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "decision": decision,
        "to_state": to_state,
        "conditions": conditions,
        "code": code,
        "event": event,
        "bump_iteration": bump_iteration,
        "notes": notes,
    }


def _hold(conditions: list[dict[str, Any]], notes: str = "") -> dict[str, Any]:
    return _decision("HOLD", None, conditions, notes=notes)


# ---------------------------------------------------------------------------
# 各状态裁决器（依序评估，先命中先生效）
# ---------------------------------------------------------------------------


def decide_PLAN(ctx: Ctx) -> dict[str, Any]:
    prompt = ctx.run_dir / "inputs" / "prompt_v1.md"
    doc = str(ctx.data.get("operator_doc") or "")
    inputs_dir = ctx.run_dir / "inputs"
    if doc:
        doc_ok = (ctx.run_dir / doc).is_file() or Path(doc).is_file()
    else:
        doc_ok = inputs_dir.is_dir() and any(inputs_dir.glob("*.md"))
    prompt_ok = prompt.is_file()
    conditions = [_cond("prompt_snapshot_exists", prompt_ok, str(prompt)),
                  _cond("doc_snapshot_exists", doc_ok, doc or "inputs/*.md")]
    if prompt_ok and doc_ok:
        return _decision("TRANSIT", "EXTRACT", conditions)
    return _hold(conditions, "初始化产物缺失，无法推进（正常不应出现）")


def decide_EXTRACT(ctx: Ctx) -> dict[str, Any]:
    check = ctx.check()
    status = str(check.get("status") or "")
    check_iter = int(check.get("iteration") or 0)
    current_round = int(check.get("current_round") or 0)
    configured = ctx.data.get("constraint_check")
    configured = configured if isinstance(configured, dict) else {}
    max_rounds = int(check.get("max_rounds") or configured.get("max_rounds") or 3)
    conditions: list[dict[str, Any]] = []

    # 1) check 终局失败 → BLOCKED（优先于一切，checker 已判定不可修复）
    if status == "failed":
        conditions.append(_cond("check_failed", True, f"status=failed iter={check_iter}"))
        return _decision("TRANSIT", "BLOCKED", conditions, code="CONSTRAINT_CHECK_FAILED")
    # 2) needs_repair 且轮次用尽 → BLOCKED
    if status == "needs_repair" and current_round >= max_rounds:
        conditions.append(_cond(
            "check_rounds_exhausted", True,
            f"status=needs_repair round={current_round}>={max_rounds}"))
        return _decision("TRANSIT", "BLOCKED", conditions, code="CONSTRAINT_CHECK_FAILED")

    # 3) constraints 有效 + check passed → GENERATE / SUCCESS(constraints-only)
    data, err = ctx.read(f"iter_{ctx.iteration:03d}/constraints.json")
    constraints_ok = False
    if err:
        conditions.append(_cond("constraints_valid", False, err))
    elif not isinstance(data, dict):
        conditions.append(_cond("constraints_valid", False, "constraints.json 根不是对象"))
    else:
        errors = validate_constraints(data)
        constraints_ok = not errors
        conditions.append(_cond(
            "constraints_valid", constraints_ok,
            "validate_constraints 通过" if constraints_ok else "; ".join(errors[:3])))
    check_passed = status == "passed" and check_iter == ctx.iteration
    conditions.append(_cond(
        "check_passed", check_passed,
        f"status={status or 'missing'} iteration={check_iter} (current={ctx.iteration})"))
    if constraints_ok and check_passed:
        if ctx.run_scope == "constraints_only":
            return _decision("TRANSIT", "SUCCESS", conditions,
                             event="CONSTRAINTS_ONLY_SUCCESS")
        return _decision("TRANSIT", "GENERATE", conditions)

    # 4) 其余：提取/检查进行中
    return _hold(conditions, "约束提取或语义检查未完成")


def decide_GENERATE(ctx: Ctx) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = []
    # 1) 生成进程失败信号 → BLOCKED
    for name in ("generation_status.json", "generation_progress.json"):
        data, err = ctx.read(f"iter_{ctx.iteration:03d}/{name}")
        if data is not None and isinstance(data, dict) and data.get("state") == "failed":
            conditions.append(_cond(
                "generation_failed", True,
                f"{name} state=failed error={str(data.get('error') or '')[:200]}"))
            return _decision("TRANSIT", "BLOCKED", conditions)

    # 2) summary 存在且 total>0 且 cases 有效 → EXECUTE
    summary, err = ctx.read(f"iter_{ctx.iteration:03d}/generation_summary.json")
    if err:
        conditions.append(_cond("generation_complete", False, err))
        return _hold(conditions, "用例生成进行中（无完成信号）")
    total = summary.get("total") if isinstance(summary, dict) else None
    total_ok = isinstance(total, int) and total > 0
    conditions.append(_cond("generation_complete", total_ok, f"summary.total={total}"))
    cases, cerr = ctx.read(f"iter_{ctx.iteration:03d}/cases.json")
    cases_ok = False
    if cerr:
        conditions.append(_cond("cases_valid", False, cerr))
    elif not isinstance(cases, list):
        conditions.append(_cond("cases_valid", False, "cases.json 根不是数组"))
    else:
        errors = validate_cases(cases)
        cases_ok = not errors
        conditions.append(_cond(
            "cases_valid", cases_ok,
            "validate_cases 通过" if cases_ok else "; ".join(errors[:3])))
    if total_ok and cases_ok:
        return _decision("TRANSIT", "EXECUTE", conditions)
    return _hold(conditions, "生成完成但产物未就绪或为空")


def decide_EXECUTE(ctx: Ctx) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = []
    data, err = ctx.read(f"iter_{ctx.iteration:03d}/execution_result.json")
    if err:
        conditions.append(_cond("execution_ready", False, err))
        return _hold(conditions, "用例执行进行中（无 execution_result）")
    if not isinstance(data, dict):
        conditions.append(_cond("execution_ready", False, "execution_result.json 根不是对象"))
        return _hold(conditions, "execution_result 格式异常")
    engine_error = str(data.get("engine_error") or "").strip()
    conditions.append(_cond(
        "engine_ok", not engine_error,
        "engine_error 为空" if not engine_error else f"engine_error={engine_error[:200]}"))
    if engine_error:
        return _decision("TRANSIT", "BLOCKED", conditions)
    errors = validate_execution(data)
    exec_ok = not errors
    conditions.append(_cond(
        "execution_valid", exec_ok,
        "validate_execution 通过" if exec_ok else "; ".join(errors[:3])))
    if exec_ok:
        return _decision("TRANSIT", "GATE", conditions)
    return _hold(conditions, "execution_result 校验未通过")


def decide_GATE(ctx: Ctx) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = []
    gate, err = ctx.read(f"iter_{ctx.iteration:03d}/quality_gate.json")
    if err:
        conditions.append(_cond("gate_report_ready", False, err))
        note = "质量门禁进行中"
        if ctx.test_framework == "ttk":
            note += "（TTK 若仅完成 command preparation 属正常等待）"
        return _hold(conditions, note)
    blocking = gate.get("blocking_issues") if isinstance(gate, dict) else None
    blocking_empty = isinstance(blocking, list) and not blocking
    conditions.append(_cond(
        "no_blocking", blocking_empty,
        "blocking_issues 为空" if blocking_empty else f"blocking={str(blocking)[:200]}"))
    if not blocking_empty:
        return _decision("TRANSIT", "BLOCKED", conditions)

    execution, eerr = ctx.read(f"iter_{ctx.iteration:03d}/execution_result.json")
    if eerr or not isinstance(execution, dict):
        conditions.append(_cond("failed_count", False, eerr or "execution_result 缺失"))
        return _hold(conditions, "门禁已过但执行统计不可读")
    failed = int(execution.get("failed") or 0)
    conditions.append(_cond("failed_count", True, f"execution failed={failed}"))
    check = ctx.check()
    check_passed = (
        str(check.get("status") or "") == "passed"
        and int(check.get("iteration") or 0) == ctx.iteration
    )
    conditions.append(_cond(
        "check_still_passed", check_passed,
        f"check status={check.get('status') or 'missing'} iteration={check.get('iteration')}"))
    if failed > 0:
        return _decision("TRANSIT", "DIAGNOSE", conditions)
    if check_passed:
        return _decision("TRANSIT", "SUCCESS", conditions)
    return _hold(conditions, "无失败用例但 check 子状态不一致，等待对齐")


def _recompute_overall_action(analysis: dict[str, Any]) -> str:
    """与 validate_artifacts.py schema 2.1 分支（~L1647-1685）同规则的重算。

    独立实现以便 flow_control 单独调用；规则变更需与 validate_artifacts 同步。
    """
    clusters = [c for c in analysis.get("failure_clusters", []) or [] if isinstance(c, dict)]
    causes = {c.get("root_cause") for c in clusters}
    findings = [f for f in analysis.get("constraint_findings", []) or [] if isinstance(f, dict)]
    constraint_ids = {
        c.get("id") for c in clusters if c.get("root_cause") == "constraint_extraction"
    }
    covered: set[Any] = set()
    for finding in findings:
        refs = finding.get("cluster_ids")
        refs = refs if isinstance(refs, list) else []
        covered |= {r for r in refs if isinstance(r, str)}
    covered &= constraint_ids
    if len(causes) > 1:
        return "MIXED_FAILURE_REVIEW"
    if causes == {"generator_bug"}:
        return "STOP_GENERATOR_BUG"
    if causes == {"executor_bug"}:
        return "STOP_EXECUTOR_BUG"
    if causes == {"constraint_extraction"} and constraint_ids and covered == constraint_ids:
        return "UPDATE_CONSTRAINTS"
    return "NEEDS_HUMAN_EVIDENCE"


def decide_DIAGNOSE(ctx: Ctx) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = []
    data, err = ctx.read(f"iter_{ctx.iteration:03d}/analysis.json")
    if err:
        conditions.append(_cond("analysis_ready", False, err))
        return _hold(conditions, "失败诊断进行中（无 analysis.json）")
    if not isinstance(data, dict):
        conditions.append(_cond("analysis_ready", False, "analysis.json 根不是对象"))
        return _hold(conditions, "analysis.json 格式异常")
    errors = validate_analysis(data)
    analysis_ok = not errors
    conditions.append(_cond(
        "analysis_valid", analysis_ok,
        "validate_analysis 通过" if analysis_ok else "; ".join(errors[:3])))
    declared = str(data.get("overall_action") or "")
    recomputed = _recompute_overall_action(data)
    action_ok = analysis_ok and declared == recomputed
    conditions.append(_cond(
        "overall_action_consistent", action_ok,
        f"declared={declared} recomputed={recomputed}"))
    if not action_ok:
        return _hold(conditions, "analysis 未过校验或 overall_action 与重算不一致，需修正后重评")

    iteration_lt_max = ctx.iteration < ctx.max_iterations
    conditions.append(_cond(
        "iteration_lt_max", iteration_lt_max,
        f"current={ctx.iteration} max={ctx.max_iterations}"))
    if recomputed == "UPDATE_CONSTRAINTS":
        if iteration_lt_max:
            return _decision("TRANSIT", "UPDATE_CONSTRAINTS", conditions, bump_iteration=True)
        return _decision("TRANSIT", "MAX_ITERATIONS", conditions)
    if recomputed == "MIXED_FAILURE_REVIEW":
        return _decision("TRANSIT", "MIXED_FAILURE_REVIEW", conditions)
    if recomputed == "NEEDS_HUMAN_EVIDENCE":
        return _decision("TRANSIT", "HUMAN_CHECKPOINT", conditions)
    return _decision("TRANSIT", recomputed, conditions)


def decide_UPDATE_CONSTRAINTS(ctx: Ctx) -> dict[str, Any]:
    if ctx.user_decision == "stop":
        return _decision(
            "TRANSIT", "BLOCKED",
            [_cond("user_stop", True, "--user-decision stop（回滚到 .pre_update 由调用方执行）")],
            code="CONSTRAINT_REGRESSION_USER_ABORT")
    if ctx.user_decision == "approve":
        return _hold([_cond("user_decision", False, "本状态不接受 approve")],
                     "UPDATE_CONSTRAINTS 无 approve 路由")

    conditions: list[dict[str, Any]] = []
    data, err = ctx.read(f"iter_{ctx.iteration:03d}/constraints.json")
    constraints_ok = False
    if err:
        conditions.append(_cond("new_constraints_valid", False, err))
    elif isinstance(data, dict):
        errors = validate_constraints(data)
        constraints_ok = not errors
        conditions.append(_cond(
            "new_constraints_valid", constraints_ok,
            "validate_constraints 通过" if constraints_ok else "; ".join(errors[:3])))
    else:
        conditions.append(_cond("new_constraints_valid", False, "constraints.json 根不是对象"))
    update, uerr = ctx.read(f"iter_{ctx.iteration:03d}/constraint_update.json")
    update_ok = False
    if uerr:
        conditions.append(_cond("update_report_valid", False, uerr))
    elif isinstance(update, dict):
        errors = validate_constraint_update(update)
        update_ok = not errors
        conditions.append(_cond(
            "update_report_valid", update_ok,
            "validate_constraint_update 通过" if update_ok else "; ".join(errors[:3])))
    else:
        conditions.append(_cond("update_report_valid", False, "constraint_update.json 根不是对象"))
    check = ctx.check()
    check_passed = (
        str(check.get("status") or "") == "passed"
        and int(check.get("iteration") or 0) == ctx.iteration
    )
    conditions.append(_cond(
        "check_repassed", check_passed,
        f"check status={check.get('status') or 'missing'} iteration={check.get('iteration')}"))
    if constraints_ok and update_ok and check_passed:
        return _decision("TRANSIT", "GENERATE", conditions)
    return _hold(conditions, "约束更新或其后的语义检查未完成")


def decide_MIXED_FAILURE_REVIEW(ctx: Ctx) -> dict[str, Any]:
    if ctx.user_decision == "approve":
        return _decision(
            "TRANSIT", "UPDATE_CONSTRAINTS",
            [_cond("user_approved", True, "--user-decision approve（应用约束 findings）")],
            bump_iteration=True)
    if ctx.user_decision == "stop":
        return _decision(
            "TRANSIT", "STOPPED_BY_USER",
            [_cond("user_stop", True, "--user-decision stop")])
    return _hold([_cond("user_decision", False, "未提供 --user-decision（沉默 = 原地等待）")],
                 "等待用户批准或停止；沉默不构成任何决定")


def decide_NEEDS_HUMAN_EVIDENCE(ctx: Ctx) -> dict[str, Any]:
    return _decision(
        "TRANSIT", "HUMAN_CHECKPOINT",
        [_cond("unconditional", True, "证据不足过路态，无条件转入人工检查点")])


def decide_HUMAN_CHECKPOINT(ctx: Ctx) -> dict[str, Any]:
    if ctx.user_decision == "stop":
        return _decision(
            "TRANSIT", "STOPPED_BY_USER",
            [_cond("user_stop", True, "--user-decision stop")])
    changed, detail = ctx.supplement_changed()
    conditions = [_cond("supplement_updated", changed, detail)]
    if changed:
        return _decision("TRANSIT", "DIAGNOSE", conditions,
                         notes="用户已补充事实 → 重新 failure-analyst（不重新完整提取）")
    return _hold(conditions, "等待用户补充事实（append 后需经 update_supplement_state 刷新）")


DECIDERS: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "PLAN": decide_PLAN,
    "EXTRACT": decide_EXTRACT,
    "GENERATE": decide_GENERATE,
    "EXECUTE": decide_EXECUTE,
    "GATE": decide_GATE,
    "DIAGNOSE": decide_DIAGNOSE,
    "UPDATE_CONSTRAINTS": decide_UPDATE_CONSTRAINTS,
    "MIXED_FAILURE_REVIEW": decide_MIXED_FAILURE_REVIEW,
    "NEEDS_HUMAN_EVIDENCE": decide_NEEDS_HUMAN_EVIDENCE,
    "HUMAN_CHECKPOINT": decide_HUMAN_CHECKPOINT,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_trailing_newline(path: Path) -> bool:
    data = path.read_bytes()
    return bool(data) and data.endswith(b"\n")


def run_flow(run_dir: Path, dry_run: bool, user_decision: str | None) -> int:
    state_path = run_dir / "run_state.json"
    if not state_path.is_file():
        print(json.dumps({"ok": False, "decision": "ERROR",
                          "error": f"run_state.json not found: {state_path}"},
                         ensure_ascii=False))
        return 2
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "decision": "ERROR",
                          "error": f"run_state.json unreadable: {exc}"},
                         ensure_ascii=False))
        return 2
    if not isinstance(data, dict):
        print(json.dumps({"ok": False, "decision": "ERROR",
                          "error": "run_state.json root must be object"},
                         ensure_ascii=False))
        return 2

    ctx = Ctx(run_dir, data, user_decision)
    current = ctx.state
    if current not in ALLOWED_STATES:
        result = {"ok": False, "from_state": current, "decision": "NO_ROUTE",
                  "applied": False, "error": f"unknown state: {current!r}"}
        print(json.dumps(result, ensure_ascii=False))
        return 4
    if current in TERMINAL_STATES:
        result = {"ok": True, "from_state": current, "decision": "NOOP",
                  "to_state": None, "conditions": [], "applied": False,
                  "notes": "已处于终态"}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    decider = DECIDERS.get(current)
    if decider is None:
        result = {"ok": True, "from_state": current, "decision": "NO_ROUTE",
                  "to_state": None, "conditions": [], "applied": False,
                  "error": "规则表未覆盖该状态，需人工"}
        print(json.dumps(result, ensure_ascii=False))
        return 4

    verdict = decider(ctx)
    decision = verdict["decision"]
    to_state = verdict.get("to_state")
    result = {
        "ok": True,
        "from_state": current,
        "decision": decision,
        "to_state": to_state,
        "user_decision": user_decision or "",
        "conditions": verdict["conditions"],
        "notes": verdict.get("notes", ""),
        "applied": False,
        "evaluated_at": _now(),
    }
    if decision == "TRANSIT" and to_state:
        successors_set = successors(current)
        if successors_set is None or to_state not in successors_set:
            result["decision"] = "NO_ROUTE"
            result["error"] = (
                f"规则表内部错误：{current} -> {to_state} 不在静态边表中")
            print(json.dumps(result, ensure_ascii=False))
            return 4
        result["code"] = verdict.get("code", "")
        result["event"] = verdict.get("event", "")
        result["bump_iteration"] = bool(verdict.get("bump_iteration"))

    if decision == "TRANSIT" and not dry_run:
        data["state"] = to_state
        entry: dict[str, Any] = {"state": to_state}
        if verdict.get("code"):
            entry["code"] = verdict["code"]
        if verdict.get("event"):
            entry["event"] = verdict["event"]
        entry["at"] = _now()
        history = data.setdefault("history", [])
        if not isinstance(history, list):
            print(json.dumps({"ok": False, "decision": "ERROR",
                              "error": "run_state.history must be a list"},
                             ensure_ascii=False))
            return 2
        history.append(entry)
        if verdict.get("bump_iteration"):
            data["current_iteration"] = ctx.iteration + 1
        save_run_state(state_path, data,
                       trailing_newline=_has_trailing_newline(state_path))
        result["applied"] = True
        result["history_appended"] = entry
        result["current_iteration"] = int(data["current_iteration"])

    # 决策留痕写入当前 iteration 目录（bump 前的 iteration，即决策发生处）
    if not dry_run:
        try:
            ctx.iter_dir.mkdir(parents=True, exist_ok=True)
            (ctx.iter_dir / "transition_decision.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        except OSError as exc:
            result["decision_file_error"] = str(exc)

    print(json.dumps(result, ensure_ascii=False))
    if result["decision"] == "NO_ROUTE":
        return 4
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="确定性状态推进器：评估当前阶段产物，按内置规则表裁决并执行迁移。")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--run-dir", required=True, help="run 目录（含 run_state.json）")
        p.add_argument("--dry-run", action="store_true", help="只评估不落盘")
        p.add_argument("--user-decision", choices=("approve", "stop"), default=None,
                       help="等待态的用户显式决定（沉默不传 = HOLD）")

    p_advance = sub.add_parser("advance", help="评估并推进到下一状态")
    add_common(p_advance)
    p_eval = sub.add_parser("evaluate", help="只评估打印决策，不落盘")
    add_common(p_eval)

    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    dry_run = bool(args.dry_run) or args.command == "evaluate"
    return run_flow(run_dir, dry_run=dry_run, user_decision=args.user_decision)


if __name__ == "__main__":
    raise SystemExit(main())
