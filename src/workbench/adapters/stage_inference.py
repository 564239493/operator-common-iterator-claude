"""12 个 agent 的运行时状态推导引擎。

run_state.state 是粗粒度字段，history 无 iteration/ended_at，因此 agent 级状态
全部由「history 当前 state + 各轮产物存在性 + 文件内容」推导。每条结论必须带
basis（依据文本）与 inferred=True，UI 侧以"推导"徽标展示。

规则集版本见 config.INFERENCE_RULESET_VERSION。
"""
from .. import config

# 工程师带固定顺序（与 scripts/show_registry.py 的 Dispatch 拓扑对齐，属领域知识）
AGENTS_FLOW = [
    {"name": "scene-scanner", "role": "场景扫描", "optional": True, "stage": "SCENE_SCAN"},
    {"name": "constraint-extractor", "role": "约束提取", "optional": False, "stage": "EXTRACT"},
    {"name": "source-analyst", "role": "源码分析", "optional": True, "stage": "SOURCE_ANALYSIS"},
    {"name": "constraint-supplementer", "role": "约束补充", "optional": True, "stage": "SUPPLEMENT"},
    {"name": "constraint-checker", "role": "约束检查", "optional": False, "stage": "CHECK"},
    {"name": "constraint-repairer", "role": "约束修复", "optional": True, "stage": "REPAIR"},
    {"name": "case-generator", "role": "用例生成", "optional": False, "stage": "GENERATE"},
    {"name": "case-executor", "role": "用例执行", "optional": False, "stage": "EXECUTE"},
    {"name": "quality-reviewer", "role": "质量门禁", "optional": False, "stage": "GATE"},
    {"name": "failure-analyst", "role": "失败诊断", "optional": False, "stage": "DIAGNOSE"},
    {"name": "constraint-updater", "role": "约束更新", "optional": True, "stage": "UPDATE_CONSTRAINTS"},
    {"name": "prompt-optimizer", "role": "提示词优化", "optional": True, "stage": "OPTIMIZE"},
]


def _entry(name, status, basis, iteration=None):
    return {
        "name": name,
        "status": status,
        "basis": basis,
        "inferred": True,
        "iteration": iteration,
        "ruleset": config.INFERENCE_RULESET_VERSION,
    }


def _latest_iter(iter_views):
    return iter_views[-1] if iter_views else None


def _iter_with(iter_views, filename):
    """找最近一轮存在指定产物的 iter 视图。"""
    for view in reversed(iter_views):
        if view["exists"].get(filename):
            return view
    return None


def infer_agents(run_state, iter_views):
    # type: (dict, list) -> list
    """返回与 AGENTS_FLOW 同序的 agent 状态列表。

    run_state: run_state.json 内容（dict）
    iter_views: iterations.summarize_iteration 的列表（按轮次升序）
    """
    state = run_state.get("state")
    scene = run_state.get("scene") or {}
    latest = _latest_iter(iter_views)
    first = iter_views[0] if iter_views else None
    latest_n = latest["n"] if latest else None
    results = []

    for spec in AGENTS_FLOW:
        name = spec["name"]
        handler = _HANDLERS[name]
        results.append(handler(run_state, state, scene, iter_views, first, latest, latest_n))
    # 附带静态元信息
    for entry, spec in zip(results, AGENTS_FLOW):
        entry["role"] = spec["role"]
        entry["optional"] = spec["optional"]
        entry["stage"] = spec["stage"]
    return results


def _scene_scanner(run_state, state, scene, iter_views, first, latest, latest_n):
    if not scene.get("enabled"):
        return _entry("scene-scanner", "not_involved", "run_state.scene.enabled=false，场景扫描未启用")
    if scene.get("scan") or scene.get("directive"):
        return _entry("scene-scanner", "passed",
                      "run_state.scene 含 scan/directive 引用，场景扫描已完成")
    return _entry("scene-scanner", "pending", "scene.enabled=true 但未见 scene 产物引用")


def _extractor(run_state, state, scene, iter_views, first, latest, latest_n):
    cur = run_state.get("current_iteration") or latest_n or 1
    cur_view = next((v for v in iter_views if v["n"] == cur), latest)
    if state == "EXTRACT" and cur_view and not cur_view["exists"].get("constraints.json"):
        return _entry("constraint-extractor", "running",
                      "state=EXTRACT 且 iter_%03d/constraints.json 尚未落盘" % cur, cur)
    if first and first["exists"].get("extraction_provenance.json"):
        target = _iter_with(iter_views, "constraints.json")
        return _entry("constraint-extractor", "passed",
                      "iter_%03d/extraction_provenance.json 存在；最新约束见 iter_%03d"
                      % (first["n"], target["n"] if target else first["n"]),
                      target["n"] if target else first["n"])
    target = _iter_with(iter_views, "constraints.json")
    if target:
        return _entry("constraint-extractor", "passed",
                      "iter_%03d/constraints.json 存在" % target["n"], target["n"])
    return _entry("constraint-extractor", "pending", "尚无 constraints.json 产物")


def _source_analyst(run_state, state, scene, iter_views, first, latest, latest_n):
    enabled = bool(run_state.get("source_analysis_knowledge")) or bool(run_state.get("operator_src_snapshot"))
    if not enabled:
        return _entry("source-analyst", "skipped", "本 run 未启用源码分析（--source-analysis-knowledge / --src 均为空）")
    target = _iter_with(iter_views, "source_evidence.json")
    if target:
        return _entry("source-analyst", "passed", "iter_%03d/source_evidence.json 存在" % target["n"], target["n"])
    return _entry("source-analyst", "pending", "源码分析已启用但未见 source_evidence.json")


def _supplementer(run_state, state, scene, iter_views, first, latest, latest_n):
    supplement = run_state.get("supplement_constraints") or run_state.get("supplement_constraints_source")
    if not supplement:
        return _entry("constraint-supplementer", "skipped", "无补充约束输入（--supplement-constraints 为空）")
    return _entry("constraint-supplementer", "passed",
                  "补充约束输入存在：%s" % supplement, latest_n)


def _checker(run_state, state, scene, iter_views, first, latest, latest_n):
    target = _iter_with(iter_views, "constraint_check.json")
    if not target:
        return _entry("constraint-checker", "pending", "尚无 constraint_check.json")
    check = target.get("constraint_check") or {}
    if check.get("_error"):
        return _entry("constraint-checker", "pending",
                      "iter_%03d/constraint_check.json 解析失败：%s" % (target["n"], check["_error"]), target["n"])
    status = str(check.get("status") or "").lower()
    if status == "passed":
        return _entry("constraint-checker", "passed",
                      "iter_%03d/constraint_check.json status=passed（round %s/%s）"
                      % (target["n"], check.get("current_round"), check.get("max_rounds")), target["n"])
    if check.get("issues_open") or check.get("issues_unfixed"):
        return _entry("constraint-checker", "rejected",
                      "iter_%03d 检查发现 open=%s unfixed=%s 问题 → 触发 repairer"
                      % (target["n"], check.get("issues_open"), check.get("issues_unfixed")), target["n"])
    if state in ("EXTRACT", "PLAN"):
        return _entry("constraint-checker", "running",
                      "iter_%03d/constraint_check.json status=%s，检查进行中" % (target["n"], check.get("status")), target["n"])
    return _entry("constraint-checker", "passed",
                  "iter_%03d/constraint_check.json status=%s" % (target["n"], check.get("status")), target["n"])


def _repairer(run_state, state, scene, iter_views, first, latest, latest_n):
    for view in iter_views:
        if view["exists"].get("constraints.json.pre_repair_local"):
            check = view.get("constraint_check") or {}
            if str(check.get("status") or "").lower() == "passed":
                return _entry("constraint-repairer", "passed",
                              "iter_%03d 存在 constraints.json.pre_repair_local 且复检 passed" % view["n"], view["n"])
            return _entry("constraint-repairer", "passed",
                          "iter_%03d 存在 constraints.json.pre_repair_local（修复已发生）" % view["n"], view["n"])
    return _entry("constraint-repairer", "skipped", "无 .pre_repair_local 备份，checker 一轮通过、未触发修复")


def _generator(run_state, state, scene, iter_views, first, latest, latest_n):
    target = _iter_with(iter_views, "generation_summary.json") or _iter_with(iter_views, "generation_progress.json")
    gen = (target or {}).get("generation") or {}
    progress = gen.get("progress") or {}
    pstate = progress.get("state")
    if progress.get("pid_alive"):
        return _entry("case-generator", "running",
                      "iter_%03d generation_progress pid_alive=true，生成进程运行中" % target["n"], target["n"])
    if pstate == "complete":
        return _entry("case-generator", "passed",
                      "iter_%03d generation_progress.state=complete，total=%s"
                      % (target["n"], progress.get("total")), target["n"])
    if pstate == "failed":
        return _entry("case-generator", "rejected",
                      "iter_%03d generation_progress.state=failed" % target["n"], target["n"])
    if state == "GENERATE":
        return _entry("case-generator", "running", "state=GENERATE 且 summary 尚未落盘",
                      run_state.get("current_iteration"))
    if target:
        return _entry("case-generator", "passed",
                      "iter_%03d 生成产物存在（progress.state=%s）" % (target["n"], pstate), target["n"])
    return _entry("case-generator", "pending", "尚无生成产物")


def _executor(run_state, state, scene, iter_views, first, latest, latest_n):
    target = _iter_with(iter_views, "execution_result.json")
    if not target:
        if state == "EXECUTE":
            return _entry("case-executor", "running", "state=EXECUTE 且 execution_result.json 尚未落盘",
                          run_state.get("current_iteration"))
        return _entry("case-executor", "pending", "尚无 execution_result.json")
    exe = target.get("execution") or {}
    if exe.get("_error"):
        return _entry("case-executor", "pending",
                      "iter_%03d/execution_result.json 解析失败" % target["n"], target["n"])
    if exe.get("engine_error"):
        return _entry("case-executor", "rejected",
                      "iter_%03d engine_error 非空：%s" % (target["n"], exe["engine_error"]), target["n"])
    return _entry("case-executor", "passed",
                  "iter_%03d 执行完成：%s/%s 通过（verdict=%s，status 字段不作判据）"
                  % (target["n"], exe.get("passed"), exe.get("total"), exe.get("verdict")), target["n"])


def _reviewer(run_state, state, scene, iter_views, first, latest, latest_n):
    target = _iter_with(iter_views, "quality_gate.json")
    if not target:
        if state == "GATE":
            return _entry("quality-reviewer", "running", "state=GATE 且 quality_gate.json 尚未落盘",
                          run_state.get("current_iteration"))
        return _entry("quality-reviewer", "pending", "尚无 quality_gate.json")
    gate = target.get("quality_gate") or {}
    if gate.get("_error"):
        return _entry("quality-reviewer", "pending",
                      "iter_%03d/quality_gate.json 解析失败" % target["n"], target["n"])
    if gate.get("blocking_issues"):
        return _entry("quality-reviewer", "rejected",
                      "iter_%03d blocking_issues=%d 条，next_state=%s → 打回"
                      % (target["n"], len(gate["blocking_issues"]), gate.get("next_state")), target["n"])
    if gate.get("status") == "fail":
        return _entry("quality-reviewer", "rejected",
                      "iter_%03d gate status=fail，next_state=%s" % (target["n"], gate.get("next_state")), target["n"])
    return _entry("quality-reviewer", "passed",
                  "iter_%03d gate status=%s（next_state=%s）"
                  % (target["n"], gate.get("status_raw") or gate.get("status"), gate.get("next_state")), target["n"])


def _analyst(run_state, state, scene, iter_views, first, latest, latest_n):
    target = _iter_with(iter_views, "analysis.json")
    if not target:
        gate = (latest or {}).get("quality_gate") or {}
        if gate.get("next_state") == "SUCCESS":
            return _entry("failure-analyst", "not_involved", "全部通过，无需诊断")
        if state == "DIAGNOSE":
            return _entry("failure-analyst", "running", "state=DIAGNOSE 且 analysis.json 尚未落盘",
                          run_state.get("current_iteration"))
        return _entry("failure-analyst", "pending", "尚无 analysis.json")
    ana = target.get("analysis") or {}
    if ana.get("_error"):
        return _entry("failure-analyst", "pending", "iter_%03d/analysis.json 解析失败" % target["n"], target["n"])
    return _entry("failure-analyst", "passed",
                  "iter_%03d 诊断完成：overall_action=%s，失败簇 %s 个"
                  % (target["n"], ana.get("overall_action"), ana.get("failure_cluster_count")), target["n"])


def _updater(run_state, state, scene, iter_views, first, latest, latest_n):
    target = _iter_with(iter_views, "constraint_update.json")
    if target:
        upd = target.get("constraint_update") or {}
        return _entry("constraint-updater", "passed",
                      "iter_%03d/constraint_update.json：%s 处修改" % (target["n"], upd.get("change_count")), target["n"])
    # 有诊断产物但无更新产物 → 跳过（如 iter_004 实测：约束未变仅扩量）
    ana_view = _iter_with(iter_views, "analysis.json")
    if ana_view:
        prev = _iter_with(iter_views, "cases.json")
        basis = "iter_%03d 无 constraint_update.json" % ana_view["n"]
        if prev and prev.get("cases_count") is not None:
            basis += "（用例数 %s，疑似仅扩量未改约束）" % prev["cases_count"]
        return _entry("constraint-updater", "skipped", basis, ana_view["n"])
    return _entry("constraint-updater", "not_involved", "尚无诊断产物，未进入更新回路")


def _prompt_optimizer(run_state, state, scene, iter_views, first, latest, latest_n):
    proposals = run_state.get("prompt_update_proposals") or []
    if proposals:
        return _entry("prompt-optimizer", "passed", "prompt_update_proposals 共 %d 条" % len(proposals), latest_n)
    return _entry("prompt-optimizer", "skipped", "无 prompt 优化提案（本 run 未触发 OPTIMIZE）")


_HANDLERS = {
    "scene-scanner": _scene_scanner,
    "constraint-extractor": _extractor,
    "source-analyst": _source_analyst,
    "constraint-supplementer": _supplementer,
    "constraint-checker": _checker,
    "constraint-repairer": _repairer,
    "case-generator": _generator,
    "case-executor": _executor,
    "quality-reviewer": _reviewer,
    "failure-analyst": _analyst,
    "constraint-updater": _updater,
    "prompt-optimizer": _prompt_optimizer,
}
