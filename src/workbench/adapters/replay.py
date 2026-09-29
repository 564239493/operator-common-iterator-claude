"""GET /api/runs/{id}/replay：迭代回放事件流（谁 pass → 交给谁 → 谁打回）。

事件全部由产物与 history 推导合成，每条带 basis 与 inferred=True。
"""


def _evt(iteration, agent, action, basis, at=None, to_agent=None):
    evt = {
        "iteration": iteration,
        "agent": agent,
        "action": action,  # passed / rejected / handoff / skipped
        "basis": basis,
        "at": at,
        "inferred": True,
    }
    if to_agent:
        evt["to_agent"] = to_agent
    return evt


def build_replay(run_state, iter_views):
    # type: (dict, list) -> list
    events = []
    for view in iter_views:
        n = view["n"]
        ex = view["exists"]
        mt = view["mtimes"]

        # 提取 / 更新入口
        if ex.get("extraction_provenance.json"):
            events.append(_evt(n, "constraint-extractor", "passed",
                               "iter_%03d/extraction_provenance.json（完整提取）" % n,
                               mt.get("extraction_provenance.json")))
        elif ex.get("constraints.json") and n == 1:
            events.append(_evt(n, "constraint-extractor", "passed",
                               "iter_%03d/constraints.json" % n, mt.get("constraints.json")))
        if ex.get("constraint_update.json"):
            upd = view.get("constraint_update") or {}
            events.append(_evt(n, "constraint-updater", "passed",
                               "constraint_update.json：%s 处最小修改" % upd.get("change_count"),
                               mt.get("constraint_update.json"), to_agent="constraint-checker"))
        elif ex.get("analysis.json") and not ex.get("constraint_update.json") and n > 1:
            events.append(_evt(n, "constraint-updater", "skipped",
                               "无 constraint_update.json（约束未变，可能仅扩量）"))

        # 检查 / 修复
        check = view.get("constraint_check") or {}
        if ex.get("constraint_check.json"):
            status = str(check.get("status") or "").lower()
            if status == "passed":
                events.append(_evt(n, "constraint-checker", "passed",
                                   "constraint_check status=passed（round %s/%s）"
                                   % (check.get("current_round"), check.get("max_rounds")),
                                   mt.get("constraint_check.json"), to_agent="case-generator"))
            else:
                events.append(_evt(n, "constraint-checker", "rejected",
                                   "发现 open=%s unfixed=%s 问题"
                                   % (check.get("issues_open"), check.get("issues_unfixed")),
                                   mt.get("constraint_check.json"), to_agent="constraint-repairer"))
        if ex.get("constraints.json.pre_repair_local"):
            events.append(_evt(n, "constraint-repairer", "passed",
                               "存在 .pre_repair_local 备份（修复已执行）",
                               mt.get("constraints.json.pre_repair_local"), to_agent="constraint-checker"))

        # 生成
        gen = view.get("generation") or {}
        progress = gen.get("progress") or {}
        if ex.get("generation_summary.json") or ex.get("generation_progress.json"):
            if progress.get("state") == "failed":
                events.append(_evt(n, "case-generator", "rejected",
                                   "generation_progress.state=failed", mt.get("generation_progress.json")))
            else:
                events.append(_evt(n, "case-generator", "passed",
                                   "生成 %s 用例（progress.state=%s）"
                                   % (progress.get("total") or view.get("cases_count"), progress.get("state")),
                                   mt.get("generation_summary.json") or mt.get("generation_progress.json"),
                                   to_agent="case-executor"))

        # 执行
        exe = view.get("execution") or {}
        if ex.get("execution_result.json"):
            if exe.get("engine_error"):
                events.append(_evt(n, "case-executor", "rejected",
                                   "engine_error：%s" % exe["engine_error"],
                                   mt.get("execution_result.json")))
            else:
                events.append(_evt(n, "case-executor", "passed",
                                   "%s/%s 通过（verdict=%s）" % (exe.get("passed"), exe.get("total"), exe.get("verdict")),
                                   mt.get("execution_result.json"), to_agent="quality-reviewer"))

        # 门禁
        gate = view.get("quality_gate") or {}
        if ex.get("quality_gate.json"):
            if gate.get("blocking_issues"):
                events.append(_evt(n, "quality-reviewer", "rejected",
                                   "blocking_issues=%d 条" % len(gate["blocking_issues"]),
                                   mt.get("quality_gate.json"), to_agent="failure-analyst"))
            else:
                events.append(_evt(n, "quality-reviewer", "passed",
                                   "gate status=%s，next_state=%s" % (gate.get("status_raw") or gate.get("status"), gate.get("next_state")),
                                   mt.get("quality_gate.json"),
                                   to_agent="failure-analyst" if gate.get("next_state") == "DIAGNOSE" else None))

        # 诊断
        ana = view.get("analysis") or {}
        if ex.get("analysis.json"):
            events.append(_evt(n, "failure-analyst", "passed",
                               "overall_action=%s，失败簇 %s 个"
                               % (ana.get("overall_action"), ana.get("failure_cluster_count")),
                               mt.get("analysis.json"),
                               to_agent="constraint-updater" if ana.get("overall_action") == "UPDATE_CONSTRAINTS" else None))

    # 终态事件（来自 history 最后一个事件）
    history = run_state.get("history") or []
    if history and isinstance(history[-1], dict):
        last = history[-1]
        events.append({
            "iteration": run_state.get("current_iteration"),
            "agent": "run",
            "action": "terminal",
            "state": last.get("state"),
            "basis": "history 末事件：%s%s" % (last.get("state"),
                                              ("（%s）" % last["event"]) if last.get("event") else ""),
            "at": last.get("at"),
            "inferred": False,
        })
    return events
