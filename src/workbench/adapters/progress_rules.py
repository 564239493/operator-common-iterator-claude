"""共享判据（规则集 v2）：状态推导与交接回放共用的唯一规则集。

只消费 build_evidence 装配好的 (run_state, iter_views, inputs_files)，不自行读文件。
所有结论带 basis 与 inferred=True；证据不足一律 unconfirmed（待确认），
绝不把"不知道"解释成"跳过/完成"。

状态枚举：pending / running / passed / rejected / skipped / unconfirmed / not_involved
"""
from .. import config

TERMINAL_STATES = config.TERMINAL_STATES
INFERENCE_RULESET_VERSION = config.INFERENCE_RULESET_VERSION


def _entry(status, basis, iteration=None, **extra):
    out = {"status": status, "basis": basis, "inferred": True, "iteration": iteration}
    out.update(extra)
    return out


def _is_current(run_state, n):
    return bool(run_state) and n == run_state.get("current_iteration")


# ---------------------------------------------------------------- 生成
def generation_verdict(view, run_state=None):
    """汇总生成证据后下结论（非按序命中即返回）。

    generation_status.json 为权威状态文件（in_progress/complete/failed）；
    in_progress 只阻止复用旧摘要，不屏蔽同一次生成的后续失败/完成证据；
    pid_alive 与 complete/failed 并存为正常组合，不降级待确认。
    """
    ex = view["exists"]
    has_summary = ex.get("generation_summary.json")
    has_prog = ex.get("generation_progress.json")
    gs = view.get("generation_status")
    has_status = ex.get("generation_status.json")
    n = view.get("n")

    if not (has_summary or has_prog or has_status):
        if run_state and run_state.get("state") == "GENERATE" and _is_current(run_state, n):
            return _entry("running", "state=GENERATE 且当前轮尚无生成产物", n)
        return _entry("pending", "尚无生成产物", n)

    gen = view.get("generation") or {}
    prog = gen.get("progress") or {}
    pstate = prog.get("state")
    pid_alive = bool(prog.get("pid_alive"))
    status_failed = bool(gs and gs.get("status") == "ok" and gs.get("state") == "failed")
    status_complete = bool(gs and gs.get("status") == "ok" and gs.get("state") == "complete")
    status_in_progress = bool(gs and gs.get("status") == "ok" and gs.get("state") == "in_progress")
    prog_failed = bool(has_prog and pstate == "failed")
    prog_complete = bool(has_prog and pstate == "complete")
    prog_running = bool(has_prog and (pstate == "running" or pid_alive))
    prog_unknown = bool(has_prog and pstate not in ("failed", "complete", "running") and not pid_alive)
    status_broken = bool(gs and gs.get("status") == "broken")
    mt = view.get("mtimes") or {}
    pm = mt.get("generation_progress.json") or ""
    sm = mt.get("generation_status.json") or ""

    # 1. 权威失败：status=failed（pid_alive 不推翻失败）
    if status_failed:
        return _entry("rejected", "generation_status.state=failed（权威失败证据）", n)
    # 2. 权威完成：status=complete（进程仍存活为正常组合）
    if status_complete:
        return _entry("passed", "generation_status.state=complete（pid_alive=%s 并存不降级）" % pid_alive, n)
    # 3. in_progress：阻止旧摘要判完成，但同次生成的失败/完成证据有效（mtime 仅辅助关联）
    if status_in_progress:
        if prog_failed:
            if pm >= sm:
                return _entry("rejected", "in_progress 后 progress=failed（进度较新，可关联同次生成）", n)
            return _entry("unconfirmed", "in_progress 与旧 progress=failed 并存，归属不明", n)
        if prog_complete:
            if pm >= sm:
                return _entry("passed", "in_progress 后 progress=complete（可关联同次生成）", n)
            return _entry("unconfirmed", "in_progress 与旧 progress=complete 并存，归属不明", n)
        if prog_running:
            return _entry("running", "generation_status=in_progress，进度显示运行中", n)
        if has_summary:
            return _entry("unconfirmed", "generation_status=in_progress，旧摘要不得据此判完成", n)
        return _entry("unconfirmed", "generation_status=in_progress，无更新的进度证据", n)
    # 4. 进度失败且无更强完成证据（无 status 佐证时独立成立）
    if prog_failed:
        return _entry("rejected", "generation_progress.state=failed 且无更新的完成证据", n)
    # 5. 进度完成
    if prog_complete:
        return _entry("passed", "generation_progress.state=complete", n)
    # 6. 进度运行中（不得永久覆盖 1-5 的新落盘证据——顺序已保证）
    if prog_running:
        return _entry("running", "生成进程运行中（pid_alive=%s，state=%s）" % (pid_alive, pstate), n)
    # 7. 仅有效摘要、无冲突进度证据 → 约定判据
    if has_summary and gen.get("summary"):
        return _entry("passed", "generation_summary 有效，无冲突进度证据（约定判据）", n)
    # 8. 损坏 / 状态未知
    if status_broken or prog_unknown or has_prog or has_status:
        return _entry("unconfirmed", "生成产物存在但状态未知或损坏，无法区分旧产物与本次生成", n)
    return _entry("unconfirmed", "生成证据不足", n)


# ---------------------------------------------------------------- 检查与修复（结果语义）
def check_verdict(view):
    """checker 结论：结果语义，非动作语义。"""
    n = view.get("n")
    if not view["exists"].get("constraint_check.json"):
        return _entry("pending", "尚无 constraint_check.json", n)
    check = view.get("constraint_check") or {}
    if check.get("_error"):
        return _entry("unconfirmed", "检查报告损坏，无法下结论", n)
    status = str(check.get("status") or "").lower()
    current_round = check.get("current_round")
    fixed = check.get("issues_fixed") or 0
    unresolved = (check.get("issues_open") or 0) + (check.get("issues_unfixed") or 0)
    if status == "passed":
        return _entry("passed", "constraint_check status=passed（round %s/%s）"
                      % (current_round, check.get("max_rounds")), n)
    if unresolved > 0:
        return _entry("rejected", "检查存在 open=%s unfixed=%s 未解决项"
                      % (check.get("issues_open"), check.get("issues_unfixed")), n)
    if status:
        return _entry("unconfirmed", "检查 status=%s 且无未解决项，仅轮次增加无明确结果" % status, n)
    return _entry("unconfirmed", "检查报告缺少明确 status，无法下结论", n)


def repair_verdict(view):
    """repairer 结论：只有"结果通过 + 修复/复检证据"才算 passed。"""
    n = view.get("n")
    if not view["exists"].get("constraint_check.json"):
        return _entry("pending", "尚无检查报告，修复状态无从判断", n)
    check = view.get("constraint_check") or {}
    if check.get("_error"):
        return _entry("unconfirmed", "检查报告损坏，修复状态无从判断", n)
    status = str(check.get("status") or "").lower()
    current_round = check.get("current_round") or 1
    fixed = check.get("issues_fixed") or 0
    unresolved = (check.get("issues_open") or 0) + (check.get("issues_unfixed") or 0)
    if status == "passed" and (current_round > 1 or fixed > 0):
        return _entry("passed", "复检通过且 %s 项已修复（current_round=%s）——修复发生过并通过"
                      % (fixed, current_round), n)
    if status == "passed":
        return _entry("skipped", "首轮检查通过，未见修复证据", n)
    if unresolved > 0:
        # 检查未通过不能据此断言修复者已运行
        return _entry("unconfirmed", "检查未通过，不能据此断言修复者已运行", n, no_instance=True)
    return _entry("unconfirmed", "检查结果不明（status=%s），修复状态无从判断" % status or "缺失", n)


# ---------------------------------------------------------------- 源码分析（extract / diagnose 两域）
_SOURCE_INPUTS = ("supplementary-doc.md", "uncertain-doc.md", "conflict-doc.md", "conflict_candidates.json")


def source_extract_verdict(run_state, iter_views, inputs_files):
    """extract 域（初始源码分析）。启用条件 = operator_src_snapshot 非空
    （source_analysis_knowledge 仅代表知识加载，不等于执行分析）。"""
    if not (run_state or {}).get("operator_src_snapshot"):
        return _entry("not_involved", "无源码快照（operator_src_snapshot 为空），未启用源码分析", None)
    raw_views = [v for v in iter_views if v["exists"].get("source_raw.json")]
    source_raw = raw_views[0] if raw_views else None
    inputs_present = all(
        (inputs_files.get(name) or {}).get("status") not in (None, "missing")
        for name in _SOURCE_INPUTS
    )
    if source_raw is None:
        if any((inputs_files.get(name) or {}).get("status") not in (None, "missing") for name in _SOURCE_INPUTS):
            return _entry("unconfirmed", "存在判读文档但未见 source_raw.json，历史归属待确认", None)
        return _entry("pending", "源码分析已启用但尚无初始分析产物", None)
    raw_state = (source_raw.get("source_raw") or {}).get("status")
    if raw_state != "ok":
        return _entry("unconfirmed", "source_raw.json 存在但损坏", source_raw["n"])
    if not inputs_present:
        return _entry("unconfirmed", "确定性提取已完成，语义整理结果待确认（判读文档不齐）", source_raw["n"])
    return _entry("passed", "初始源码分析产物齐备且有效（source_raw + 三个判读文档 + 冲突候选）", source_raw["n"])


def source_diagnose_verdict(view):
    """diagnose 域（失败诊断）：source_evidence.json 属诊断阶段证据，独立展示。"""
    n = view.get("n")
    if not view["exists"].get("source_evidence.json"):
        return None
    evidence_state = (view.get("source_evidence") or {}).get("status")
    if evidence_state != "ok":
        return _entry("unconfirmed", "source_evidence.json 存在但损坏", n)
    return _entry("passed", "诊断证据已生成（source_evidence.json）", n)


# ---------------------------------------------------------------- 补充约束（补充结果与应用情况分开）
def supplement_verdict(view, inputs_files):
    """supplementer 只负责产出补丁；合并归主协调器，不作为完成条件。"""
    n = view.get("n")
    patch = view.get("constraints_patch")
    # 输入判据：源码 supplementary-doc.md 或人工补充文档，空文档不算有待处理材料
    has_input = any(
        (inputs_files.get(name) or {}).get("status") == "ok" and (inputs_files.get(name) or {}).get("size", 0) > 0
        for name in ("supplementary-doc.md", "supplement_constraints.md")
    )
    origins = view.get("constraints_origins") or {}
    aux = {"origin_supplement": origins.get("supplement", 0),
           "origin_conflict_resolution": origins.get("conflict_resolution", 0)}
    if patch is None:
        if has_input:
            return _entry("unconfirmed", "有补充材料，未见补丁产物", n, application=None, origins=aux, no_instance=True)
        return _entry("not_involved", "无补充材料输入", n, application=None, origins=aux)
    if patch.get("status") == "broken":
        return _entry("unconfirmed", "补丁文件异常（%s）" % patch.get("error", ""), n,
                      application=None, origins=aux)
    application = ("空补丁无需应用" if patch.get("empty")
                   else "非空补丁应用结果待确认")
    text = "已生成空补丁（无需合并新增内容）" if patch.get("empty") else \
        "补充结果已生成（%s 条）" % patch.get("count")
    return _entry("passed", text, n, application=application, origins=aux)


# ---------------------------------------------------------------- 提示词优化（触发条件为纲）
_OPTIMIZE_PATHS = ("UPDATE_CONSTRAINTS",)


def optimizer_verdict(run_state, iter_views, inputs_files):
    """提案与裁决互不推翻；裁决文件损坏不推翻'提案已生成'。"""
    decisions = inputs_files.get("prompt_update_decisions.json") or {}
    dec_info = {"status": decisions.get("status"), "count": decisions.get("count"),
                "error": decisions.get("error")}
    proposal_views = [v for v in iter_views
                      if (v.get("prompt_update_proposal") or {}).get("status") == "ok"]
    if proposal_views:
        view = proposal_views[-1]
        return _entry("passed", "提示词优化提案已生成（iter_%03d，不暗示试验通过或用户批准）" % view["n"],
                      view["n"], decisions=dec_info)
    # 无提案：按触发条件判断
    analyses = [v for v in iter_views if v["exists"].get("analysis.json") and not (v.get("analysis") or {}).get("_error")]
    state = (run_state or {}).get("state")
    if analyses:
        ana = analyses[-1].get("analysis") or {}
        root_cause = ana.get("root_cause")
        if root_cause == "constraint_extraction":
            return _entry("unconfirmed", "诊断指向约束提取（root_cause=constraint_extraction），"
                                      "满足触发条件但未见提案产物", None, decisions=dec_info, no_instance=True)
        return _entry("not_involved", "诊断明确不走优化路径（root_cause=%s）" % root_cause,
                      None, decisions=dec_info)
    if state in ("STOP_GENERATOR_BUG", "STOP_EXECUTOR_BUG"):
        return _entry("not_involved", "终态=%s，明确不属优化路径" % state, None, decisions=dec_info)
    if state == "SUCCESS":
        return _entry("not_involved", "任务成功完成，无优化触发条件", None, decisions=dec_info)
    if state in TERMINAL_STATES:
        return _entry("not_involved", "终态=%s，未见优化触发证据" % state, None, decisions=dec_info)
    return _entry("unconfirmed", "尚无诊断结果，无法判断是否应触发优化（缺诊断证据，非明确不满足）",
                  None, decisions=dec_info, no_instance=True)


# ---------------------------------------------------------------- 场景扫描（职责仅扫描）
def scene_verdict(run_state, inputs_files):
    scene = (run_state or {}).get("scene") or {}
    if not scene.get("enabled"):
        return _entry("not_involved", "run_state.scene.enabled=false，场景扫描未启用", None)
    scan = inputs_files.get("scene_scan.json") or {}
    if scan.get("status") == "ok" and isinstance(scan.get("has_scenarios"), bool):
        return _entry("passed", "scene_scan 有效（has_scenarios=%s，%s 类设备；仅证明扫描完成）"
                      % (scan.get("has_scenarios"), len(scan.get("device_types") or [])), None)
    if scan.get("status") == "broken":
        # 文件存在但损坏：扫描实例可能发生过，保留节点展示损坏证据
        return _entry("unconfirmed", "scene_scan.json 损坏", None)
    if scan.get("status") is None:
        return _entry("unconfirmed", "场景已启用但未见 scene_scan.json", None, no_instance=True)
    return _entry("unconfirmed", "scene_scan.json 存在但字段不可读", None)


# ---------------------------------------------------------------- 每轮角色
def _extractor_verdict(view, run_state):
    n = view.get("n")
    if n == 1:
        if view["exists"].get("constraints.json"):
            proven = "，知识应用见 extraction_provenance" if view["exists"].get("extraction_provenance.json") else ""
            return _entry("passed", "首轮约束已提交（constraints.json%s）" % proven, n)
        if run_state and run_state.get("state") == "EXTRACT" and _is_current(run_state, n):
            return _entry("running", "state=EXTRACT 且首轮 constraints.json 尚未落盘", n)
        return _entry("pending", "首轮尚无 constraints.json", n)
    return _entry("not_involved", "完整提取仅首轮发生（后续轮为最小更新）", n)


def _executor_verdict(view, run_state):
    n = view.get("n")
    if not view["exists"].get("execution_result.json"):
        if run_state and run_state.get("state") == "EXECUTE" and _is_current(run_state, n):
            return _entry("running", "state=EXECUTE 且当前轮 execution_result 尚未落盘", n)
        return _entry("pending", "本轮尚无执行结果", n)
    exe = view.get("execution") or {}
    if exe.get("_error"):
        return _entry("unconfirmed", "execution_result.json 损坏", n)
    if exe.get("engine_error"):
        return _entry("rejected", "engine_error 非空：%s" % exe["engine_error"], n)
    return _entry("passed", "执行完成：%s/%s 通过（verdict=%s；status 字段不作判据）"
                  % (exe.get("passed"), exe.get("total"), exe.get("verdict")), n)


def _reviewer_verdict(view, run_state):
    n = view.get("n")
    if not view["exists"].get("quality_gate.json"):
        if run_state and run_state.get("state") == "GATE" and _is_current(run_state, n):
            return _entry("running", "state=GATE 且当前轮 quality_gate 尚未落盘", n)
        return _entry("pending", "本轮尚无 quality_gate.json", n)
    gate = view.get("quality_gate") or {}
    if gate.get("_error"):
        return _entry("unconfirmed", "quality_gate.json 损坏", n)
    if gate.get("blocking_issues"):
        return _entry("rejected", "blocking_issues=%d 条，next_state=%s"
                      % (len(gate["blocking_issues"]), gate.get("next_state")), n)
    if gate.get("status") == "fail":
        return _entry("rejected", "gate status=fail，next_state=%s" % gate.get("next_state"), n)
    # 「不认识的取值绝不算通过」（与 quality_gate 适配器同一原则）：
    # 只有明确 pass 才算通过；unknown/warn 等一律待确认，不与详情面板打架。
    if gate.get("status") != "pass":
        return _entry("unconfirmed", "gate status=%s 不可识别（无阻断项但不默认通过），next_state=%s"
                      % (gate.get("status_raw") or gate.get("status"), gate.get("next_state")), n)
    return _entry("passed", "gate status=%s，next_state=%s"
                  % (gate.get("status_raw") or gate.get("status"), gate.get("next_state")), n)


def _analyst_verdict(view, run_state):
    n = view.get("n")
    if view["exists"].get("analysis.json"):
        ana = view.get("analysis") or {}
        if ana.get("_error"):
            return _entry("unconfirmed", "analysis.json 损坏", n)
        return _entry("passed", "诊断完成：overall_action=%s，失败簇 %s 个"
                      % (ana.get("overall_action"), ana.get("failure_cluster_count")), n)
    gate = view.get("quality_gate") or {}
    if gate and not gate.get("_error") and gate.get("next_state") == "SUCCESS":
        return _entry("not_involved", "本轮评审通过（next_state=SUCCESS），无需诊断", n)
    if run_state and run_state.get("state") == "DIAGNOSE" and _is_current(run_state, n):
        return _entry("running", "state=DIAGNOSE 且当前轮 analysis 尚未落盘", n)
    return _entry("pending", "本轮尚无诊断结果", n)


def _updater_verdict(view):
    n = view.get("n")
    if view["exists"].get("constraint_update.json"):
        upd = view.get("constraint_update") or {}
        if upd.get("_error"):
            # 文件存在但损坏：更新实例发生过，保留节点展示损坏证据
            return _entry("unconfirmed", "constraint_update.json 损坏", n)
        return _entry("passed", "约束更新完成：%s 处最小修改" % upd.get("change_count"), n)
    ana = view.get("analysis")
    if ana and not ana.get("_error"):
        action = ana.get("overall_action")
        if action == "UPDATE_CONSTRAINTS":
            # 本轮没有更新实例（如 iter_004 仅扩量）：横条保留待确认，图上不显示节点
            return _entry("unconfirmed", "诊断建议更新但未见 constraint_update.json（可能仅扩量或人工裁决中）",
                          n, no_instance=True)
        if action in ("NEEDS_HUMAN_EVIDENCE", "MIXED_FAILURE_REVIEW"):
            return _entry("unconfirmed", "诊断建议人工处理（overall_action=%s），未进入自动更新" % action,
                          n, no_instance=True)
        if action:
            return _entry("not_involved", "诊断结论不走更新回路（overall_action=%s）" % action, n)
    return _entry("pending", "本轮尚无诊断，未进入更新回路", n)


# ---------------------------------------------------------------- 汇总：带轮次的角色状态
def per_round_states(run_state, iter_views, inputs_files):
    # type: (dict, list, dict) -> dict
    """返回 {轮次 n: {agent 名: 状态条目}}，横条与回放共用同一结果。

    体现参与流程而非参与力度：任务级角色（场景扫描 / 源码分析 extract 域 /
    提示词优化 / 补充约束）只在**实际发生的轮次**体现，其余轮次为 not_involved
    （即使本轮流程使用了它们的产物）；不用任务终局状态判定历史轮次的参与情况。
    """
    scene_state = scene_verdict(run_state, inputs_files)
    extract_state = source_extract_verdict(run_state, iter_views, inputs_files)
    optimizer_state = optimizer_verdict(run_state, iter_views, inputs_files)

    current = (run_state or {}).get("current_iteration")
    first_n = iter_views[0]["n"] if iter_views else None
    # 任务级角色的"实际发生轮"
    scene_round = first_n  # 场景扫描发生在首轮开场之前
    source_round = next((v["n"] for v in iter_views if v["exists"].get("source_raw.json")), None)
    proposal_round = None
    for v in iter_views:
        if (v.get("prompt_update_proposal") or {}).get("status") == "ok":
            proposal_round = v["n"]
    optimizer_round = proposal_round if proposal_round is not None else current
    supplement_rounds = set(v["n"] for v in iter_views if v["exists"].get("constraints_patch.json"))
    has_supplement_input = any(
        (inputs_files.get(name) or {}).get("status") == "ok"
        and (inputs_files.get(name) or {}).get("size", 0) > 0
        for name in ("supplementary-doc.md", "supplement_constraints.md")
    )

    def scoped(entry, acting_round, what):
        """任务级角色按发生轮收窄：未发生轮次一律 not_involved。"""
        if entry["status"] == "not_involved":
            return dict(entry, iteration=n)
        if acting_round is None or n == acting_round:
            # 发生轮未知（如 run_state 缺 current_iteration）时不能断言本轮未参与：
            # 保留原结论只挂轮次，避免把 pending/unconfirmed 误吞成 not_involved。
            return dict(entry, iteration=n)
        return _entry(
            "not_involved",
            "任务级动作：%s仅在第 %s 轮发生，本轮未参与" % (what, acting_round),
            n,
        )

    states = {}
    for view in iter_views:
        n = view["n"]
        cur = _is_current(run_state, n)
        round_states = {
            "scene-scanner": scoped(scene_state, scene_round, "场景扫描"),
            "constraint-extractor": _extractor_verdict(view, run_state),
            "constraint-checker": check_verdict(view),
            "constraint-repairer": repair_verdict(view),
            "case-generator": generation_verdict(view, run_state),
            "case-executor": _executor_verdict(view, run_state),
            "quality-reviewer": _reviewer_verdict(view, run_state),
            "failure-analyst": _analyst_verdict(view, run_state),
            "constraint-updater": _updater_verdict(view),
        }
        # 源码分析：本轮有 diagnose 证据按诊断展示；extract 域仅在 source_raw 所在轮
        #（无产物时挂在当前轮，让"待确认"有落点）
        diag = source_diagnose_verdict(view)
        if diag:
            round_states["source-analyst"] = diag
        else:
            acting = source_round if source_round is not None else (current if extract_state["status"] != "not_involved" else None)
            round_states["source-analyst"] = scoped(extract_state, acting, "初始源码分析")
        round_states["prompt-optimizer"] = scoped(optimizer_state, optimizer_round, "提示词优化")
        # 补充约束：补丁所在轮体现；有材料无补丁时仅在当前轮挂"待确认"
        if n in supplement_rounds or (has_supplement_input and n == current):
            round_states["constraint-supplementer"] = supplement_verdict(view, inputs_files)
        else:
            round_states["constraint-supplementer"] = _entry(
                "not_involved",
                "未在本轮发生%s" % ("（补丁见第 %s 轮）" % min(supplement_rounds) if supplement_rounds else ""),
                n)
        # 历史轮次的 running 证据：本轮已过去，降为待确认（可能为遗留）
        if not cur:
            for name in ("case-generator", "case-executor", "quality-reviewer",
                         "failure-analyst", "constraint-extractor"):
                entry = round_states.get(name)
                if entry and entry["status"] == "running":
                    round_states[name] = _entry(
                        "unconfirmed",
                        "历史轮次存在运行中证据（%s），可能为遗留" % entry["basis"], n)
        states[n] = round_states
    return states


# ---------------------------------------------------------------- 交接目标（业务流明确约定，非相邻猜测）
HANDOFF_TARGETS = {
    "scene-scanner": "constraint-extractor",
    "constraint-extractor": "constraint-checker",
    "constraint-repairer": "constraint-checker",
    "constraint-supplementer": "constraint-checker",
    "constraint-checker": "case-generator",
    "case-generator": "case-executor",
    "case-executor": "quality-reviewer",
    "constraint-updater": "constraint-checker",
}


def handoff_target(view, agent, entry):
    """passed 时的明确交接目标；条件型交接（reviewer/analyst）按产物结论判定。
    无目标返回 None——相邻排列不构成交接证据。"""
    if entry.get("status") != "passed":
        return None
    if agent == "quality-reviewer":
        gate = view.get("quality_gate") or {}
        if (gate.get("next_state") or "") == "DIAGNOSE":
            return "failure-analyst"
        return None
    if agent == "failure-analyst":
        ana = view.get("analysis") or {}
        if (ana.get("overall_action") or "") == "UPDATE_CONSTRAINTS":
            return "constraint-updater"
        return None
    return HANDOFF_TARGETS.get(agent)
