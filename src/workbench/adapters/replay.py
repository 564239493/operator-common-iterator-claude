"""迭代回放事件流（谁 pass → 明确交给谁 → 谁打回）。

与横条共用 progress_rules.per_round_states 的同一结论——判据单一来源。
事件规则：状态条目生成节点；只有 progress_rules.handoff_target 给出的
明确交接关系生成箭头，相邻排列不构成交接证据。终态事件仅当 history
末事件状态属于终态集合才生成（任务进行中不画终局）。
"""
from . import progress_rules
from .stage_inference import AGENTS_FLOW

# 各角色节点的时间证据文件（仅用于时间轴展示，不作判据）
_MTIME_FILE = {
    "constraint-extractor": "constraints.json",
    "constraint-checker": "constraint_check.json",
    "constraint-repairer": "constraint_check.json",
    "constraint-supplementer": "constraints_patch.json",
    "case-generator": "generation_summary.json",
    "case-executor": "execution_result.json",
    "quality-reviewer": "quality_gate.json",
    "failure-analyst": "analysis.json",
    "constraint-updater": "constraint_update.json",
    "source-analyst": "source_raw.json",
    "prompt-optimizer": "prompt_update_proposal.json",
}


def _issue_open_at(issue, r):
    # type: (dict, int) -> bool
    """issue 在第 r 次检查时是否仍未解决（据 found_round / status / last_checked_round）。"""
    found = issue.get("found_round") or 1
    if found > r:
        return False
    if str(issue.get("status", "")).lower() == "fixed":
        last = issue.get("last_checked_round") or found
        return r < last  # fixed 是在 last 轮复检确认的，此前仍为未解决
    return True


def _expand_check_repair(view, n):
    # type: (dict, int) -> list
    """把检查-修复循环展开为逐次处理实例（复检/重试是新的实例，不折叠重复画线）。

    证据：constraint_check.json 的 current_round（检查次数）与 issues[] 的
    found_round / status / last_checked_round（逐项时间线）。证据不足的中间环节
    标 unconfirmed，不臆造。无可展开证据（current_round 缺失/为 1）返回 []。
    """
    check = view.get("constraint_check") or {}
    if check.get("_error"):
        return []
    current_round = check.get("current_round")
    if not isinstance(current_round, int) or current_round <= 1:
        return []
    issues = []
    for item in check.get("issues") or []:
        if isinstance(item, dict) and item.get("id"):
            issues.append(item)
    status = str(check.get("status") or "").lower()
    events = []
    for r in range(1, current_round + 1):
        open_ids = [i["id"] for i in issues if _issue_open_at(i, r)]
        final = (r == current_round)
        if open_ids:
            events.append({
                "iteration": n, "agent": "constraint-checker", "action": "rejected",
                "basis": "第 %s/%s 次检查存在未解决项：%s"
                         % (r, current_round, "、".join(open_ids)),
                "at": None, "inferred": True, "to_agent": "constraint-repairer", "to_round": n,
            })
        elif final and status == "passed":
            events.append({
                "iteration": n, "agent": "constraint-checker", "action": "passed",
                "basis": "第 %s/%s 次复检通过（current_round=%s，fixed=%s）"
                         % (r, current_round, current_round, check.get("issues_fixed")),
                "at": None, "inferred": True,
                "to_agent": "case-generator", "to_round": n,
            })
        elif final:
            events.append({
                "iteration": n, "agent": "constraint-checker", "action": status in ("failed",) and "rejected" or "unconfirmed",
                "basis": "第 %s/%s 次检查结论：%s" % (r, current_round, check.get("status")),
                "at": None, "inferred": True, "to_agent": "constraint-repairer", "to_round": n,
            })
        else:
            # 中途无未解决项却还有后续轮：记录矛盾，不臆造
            events.append({
                "iteration": n, "agent": "constraint-checker", "action": "unconfirmed",
                "basis": "第 %s/%s 次检查的逐项记录未保留，结论待确认" % (r, current_round),
                "at": None, "inferred": True,
            })
        if not final:
            # r → r+1 之间的修复实例：有"由 open 变 fixed"的 issue 才算发生
            fixed_between = [i["id"] for i in issues
                             if _issue_open_at(i, r) and not _issue_open_at(i, r + 1)]
            if fixed_between:
                events.append({
                    "iteration": n, "agent": "constraint-repairer", "action": "passed",
                    "basis": "第 %s 次修复：%s"
                             % (r, "、".join(fixed_between)),
                    "at": None, "inferred": True, "to_agent": "constraint-checker", "to_round": n,
                })
            else:
                events.append({
                    "iteration": n, "agent": "constraint-repairer", "action": "unconfirmed",
                    "basis": "第 %s→%s 次检查之间无修复证据，是否修复待确认" % (r, r + 1),
                    "at": None, "inferred": True,
                })
    return events


def build_replay(run_state, iter_views, inputs_files):
    # type: (dict, list, dict) -> list
    states = progress_rules.per_round_states(run_state, iter_views, inputs_files)
    events = []
    for view in iter_views:
        n = view["n"]
        mtimes = view.get("mtimes") or {}
        round_states = states.get(n) or {}
        # 检查-修复循环展开为逐次实例（current_round>1 且证据可用时）；
        # 展开后本轮回放不再单独发 checker/repairer 汇总事件。
        expanded = _expand_check_repair(view, n)
        # 轮次内时序：约束更新是本轮开场动作（用上一轮诊断结果改约束、改完过检查），
        # 置于首位；其余按角色流程顺序。
        ordered = ["constraint-updater"] + [s["name"] for s in AGENTS_FLOW
                                            if s["name"] != "constraint-updater"]
        for name in ordered:
            if expanded and name == "constraint-checker":
                # 逐次实例在检查的流程位展开（业务时序：…→检查①→修复①→复检②→…→生成），
                # 适用于每一轮（含首轮：提取→检查①→…）
                events.extend(expanded)
                continue
            if expanded and name == "constraint-repairer":
                continue  # 已由逐次实例替代
            entry = round_states.get(name)
            # 本轮没有处理实例就不显示节点：pending（未执行到）/ not_involved（未参与）/
            # skipped（跳过）/ no_instance（因"未见产物"而待确认——横条保留状态作解释）。
            # 产物存在但损坏/状态不明的待确认仍显示节点（实例发生过）。
            if not entry or entry.get("status") in ("pending", "not_involved", "skipped") \
                    or entry.get("no_instance"):
                continue
            event = {
                "iteration": n,
                "agent": name,
                "action": entry["status"],
                "basis": entry.get("basis"),
                "at": mtimes.get(_MTIME_FILE.get(name) or ""),
                "inferred": True,
            }
            for extra_key in ("application", "origins", "decisions"):
                if extra_key in entry:
                    event[extra_key] = entry[extra_key]
            target = progress_rules.handoff_target(view, name, entry)
            if target:
                # failure-analyst → 下一轮的 updater（跨轮交接：上轮出口、下轮开场）
                to_round = n + 1 if name == "failure-analyst" else n
                event["to_agent"] = target
                event["to_round"] = to_round
            events.append(event)

    # 跨轮开场的来源标注：本轮 updater 的 from_agent/from_round 指回上一轮诊断
    by_agent_round = {}
    for e in events:
        by_agent_round.setdefault((e["agent"], e["iteration"]), e)
    for e in events:
        if e["agent"] != "constraint-updater":
            continue
        source = next((h for h in events
                       if h.get("to_agent") == "constraint-updater"
                       and h.get("to_round") == e["iteration"]), None)
        if source:
            e["from_agent"] = source["agent"]
            e["from_round"] = source["iteration"]

    # 终态事件：仅当 history 末事件状态属于终态集合才生成
    history = (run_state or {}).get("history") or []
    if history and isinstance(history[-1], dict):
        last = history[-1]
        if last.get("state") in progress_rules.TERMINAL_STATES:
            events.append({
                "iteration": (run_state or {}).get("current_iteration"),
                "agent": "run",
                "action": "terminal",
                "state": last.get("state"),
                "basis": "history 末事件：%s%s" % (
                    last.get("state"),
                    ("（%s）" % last["event"]) if last.get("event") else ""),
                "at": last.get("at"),
                "inferred": False,
            })
    return events
