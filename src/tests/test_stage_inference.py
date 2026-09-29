"""共享判据测试（progress_rules v2）：主路径 + 评审要求的全部反例。

基线随规则走——本文件断言的是正确语义，不迁就旧推断。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.adapters import progress_rules as pr
from workbench.adapters.stage_inference import AGENTS_FLOW, infer_agents


def _view(n, exists=None, **sections):
    base_exists = {name: False for name in (
        "constraints.json", "constraint_check.json", "cases.json", "execution_result.json",
        "analysis.json", "quality_gate.json", "generation_summary.json", "generation_progress.json",
        "generation_status.json", "constraint_update.json", "regression_check.json",
        "extraction_provenance.json", "constraints.json.pre_repair_local",
        "source_raw.json", "source_evidence.json", "constraints_patch.json",
        "prompt_update_proposal.json",
    )}
    base_exists.update(exists or {})
    view = {
        "n": n,
        "dir": "iter_%03d" % n,
        "exists": base_exists,
        "mtimes": {},
        "scene_subdirs": [],
        "constraint_check": None,
        "generation": None,
        "generation_status": None,
        "execution": None,
        "quality_gate": None,
        "regression": None,
        "analysis": None,
        "constraint_update": None,
        "source_raw": None,
        "source_evidence": None,
        "constraints_patch": None,
        "prompt_update_proposal": None,
        "constraints_origins": {},
        "cases_count": None,
    }
    view.update(sections)
    return view


def _run_state(**overrides):
    state = {
        "state": "MAX_ITERATIONS",
        "current_iteration": 2,
        "scene": {"enabled": False},
        "history": [],
    }
    state.update(overrides)
    return state


def _inputs(**entries):
    return entries


class TestFlowOrder(unittest.TestCase):
    def test_flow_order_fixed(self):
        names = [s["name"] for s in AGENTS_FLOW]
        self.assertEqual(names[0], "scene-scanner")
        self.assertEqual(names[6], "case-generator")
        self.assertEqual(names[-1], "prompt-optimizer")
        self.assertEqual(len(names), 12)


class TestGenerationVerdict(unittest.TestCase):
    def test_failed_status_with_old_summary_rejected(self):
        """status=failed + 旧摘要 → rejected（权威失败，旧摘要不救）。"""
        view = _view(2, exists={"generation_status.json": True, "generation_summary.json": True},
                     generation={"summary": {"total": 10}},
                     generation_status={"status": "ok", "state": "failed"})
        self.assertEqual(pr.generation_verdict(view)["status"], "rejected")

    def test_in_progress_with_linked_failure_rejected(self):
        """in_progress + 可关联的后续 progress.failed + 旧摘要 → rejected。"""
        view = _view(2, exists={"generation_status.json": True, "generation_progress.json": True,
                                "generation_summary.json": True},
                     generation={"summary": {"total": 10}, "progress": {"state": "failed", "pid_alive": False}},
                     generation_status={"status": "ok", "state": "in_progress"},
                     mtimes={"generation_progress.json": "2026-01-02T00:00:00",
                             "generation_status.json": "2026-01-01T00:00:00"})
        self.assertEqual(pr.generation_verdict(view)["status"], "rejected")

    def test_in_progress_with_stale_failure_unconfirmed(self):
        """in_progress 与旧 progress=failed 并存、归属不明 → unconfirmed。"""
        view = _view(2, exists={"generation_status.json": True, "generation_progress.json": True},
                     generation={"progress": {"state": "failed", "pid_alive": False}},
                     generation_status={"status": "ok", "state": "in_progress"},
                     mtimes={"generation_progress.json": "2026-01-01T00:00:00",
                             "generation_status.json": "2026-01-02T00:00:00"})
        self.assertEqual(pr.generation_verdict(view)["status"], "unconfirmed")

    def test_in_progress_blocks_old_summary(self):
        """in_progress + 旧摘要 → 不得判完成。"""
        view = _view(2, exists={"generation_status.json": True, "generation_summary.json": True},
                     generation={"summary": {"total": 10}},
                     generation_status={"status": "ok", "state": "in_progress"})
        self.assertEqual(pr.generation_verdict(view)["status"], "unconfirmed")

    def test_progress_failed_without_status_rejected(self):
        """progress=failed 无更强完成证据 → rejected（监听器失败证据）。"""
        view = _view(2, exists={"generation_progress.json": True},
                     generation={"progress": {"state": "failed", "pid_alive": False}})
        self.assertEqual(pr.generation_verdict(view)["status"], "rejected")

    def test_complete_with_pid_alive_passed(self):
        """complete + pid_alive=true 为正常组合，不降级待确认。"""
        view = _view(2, exists={"generation_status.json": True},
                     generation_status={"status": "ok", "state": "complete"},
                     generation={"progress": {"state": "complete", "pid_alive": True}})
        self.assertEqual(pr.generation_verdict(view)["status"], "passed")

    def test_failed_with_pid_alive_rejected(self):
        """failed + pid_alive=true → rejected（存活不推翻失败）。"""
        view = _view(2, exists={"generation_status.json": True, "generation_progress.json": True},
                     generation={"progress": {"state": "running", "pid_alive": True}},
                     generation_status={"status": "ok", "state": "failed"})
        self.assertEqual(pr.generation_verdict(view)["status"], "rejected")

    def test_running_progress_with_new_complete_status(self):
        """running 旧进度 + 新落盘 complete 状态文件 → passed（新证据不被覆盖）。"""
        view = _view(2, exists={"generation_status.json": True, "generation_progress.json": True},
                     generation={"progress": {"state": "running", "pid_alive": True}},
                     generation_status={"status": "ok", "state": "complete"})
        self.assertEqual(pr.generation_verdict(view)["status"], "passed")

    def test_summary_only_passed_by_convention(self):
        """仅有效 summary、无冲突进度证据 → passed（约定判据）。"""
        view = _view(2, exists={"generation_summary.json": True},
                     generation={"summary": {"total": 10}})
        self.assertEqual(pr.generation_verdict(view)["status"], "passed")

    def test_running_progress(self):
        view = _view(2, exists={"generation_progress.json": True},
                     generation={"progress": {"state": "running", "pid_alive": True}})
        self.assertEqual(pr.generation_verdict(view)["status"], "running")

    def test_broken_status_unconfirmed(self):
        """状态文件损坏 → unconfirmed，不按空文件处理。"""
        view = _view(2, exists={"generation_status.json": True},
                     generation_status={"status": "broken", "state": None})
        self.assertEqual(pr.generation_verdict(view)["status"], "unconfirmed")

    def test_no_artifacts_pending_or_running(self):
        view = _view(2)
        self.assertEqual(pr.generation_verdict(view)["status"], "pending")
        running = pr.generation_verdict(_view(2), _run_state(state="GENERATE", current_iteration=2))
        self.assertEqual(running["status"], "running")


class TestCheckRepairVerdicts(unittest.TestCase):
    def test_missing_check_pending(self):
        view = _view(2)
        self.assertEqual(pr.check_verdict(view)["status"], "pending")
        self.assertEqual(pr.repair_verdict(view)["status"], "pending")

    def test_broken_check_unconfirmed(self):
        """检查文件损坏 → unconfirmed（非 pending）。"""
        view = _view(2, exists={"constraint_check.json": True},
                     constraint_check={"_error": "JSON 解析失败"})
        self.assertEqual(pr.check_verdict(view)["status"], "unconfirmed")
        self.assertEqual(pr.repair_verdict(view)["status"], "unconfirmed")

    def test_open_issues_repairer_not_asserted(self):
        """round=2 + fixed=3 + open=2 → checker rejected、repairer unconfirmed
        （不能据此断言修复者已运行）。"""
        view = _view(2, exists={"constraint_check.json": True},
                     constraint_check={"status": "failed", "current_round": 2, "max_rounds": 3,
                                       "issues_open": 2, "issues_fixed": 3, "issues_unfixed": 0})
        self.assertEqual(pr.check_verdict(view)["status"], "rejected")
        self.assertEqual(pr.repair_verdict(view)["status"], "unconfirmed")

    def test_passed_with_repair_evidence(self):
        """round=2 + status=passed + open=0 → 双 passed（靠结果证据而非轮次本身）。"""
        view = _view(2, exists={"constraint_check.json": True},
                     constraint_check={"status": "passed", "current_round": 2, "max_rounds": 3,
                                       "issues_open": 0, "issues_fixed": 3, "issues_unfixed": 0})
        self.assertEqual(pr.check_verdict(view)["status"], "passed")
        repairer = pr.repair_verdict(view)
        self.assertEqual(repairer["status"], "passed")
        self.assertIn("复检通过", repairer["basis"])

    def test_first_round_pass_repairer_skipped(self):
        view = _view(2, exists={"constraint_check.json": True},
                     constraint_check={"status": "passed", "current_round": 1, "max_rounds": 3,
                                       "issues_open": 0, "issues_fixed": 0, "issues_unfixed": 0})
        self.assertEqual(pr.repair_verdict(view)["status"], "skipped")

    def test_round_up_without_result_unconfirmed(self):
        """仅轮次增加、无明确结果 → unconfirmed（current_round 本身不当通过条件）。"""
        view = _view(2, exists={"constraint_check.json": True},
                     constraint_check={"status": "", "current_round": 2, "max_rounds": 3,
                                       "issues_open": 0, "issues_fixed": 0, "issues_unfixed": 0})
        self.assertEqual(pr.check_verdict(view)["status"], "unconfirmed")
        self.assertEqual(pr.repair_verdict(view)["status"], "unconfirmed")


class TestSourceAnalysis(unittest.TestCase):
    def _inputs_full(self):
        return _inputs(**{name: {"status": "ok", "size": 100} for name in
                          ("supplementary-doc.md", "uncertain-doc.md", "conflict-doc.md", "conflict_candidates.json")})

    def test_initial_artifacts_complete_without_evidence(self):
        """全初始产物（无 source_evidence）→ passed（不要求诊断证据）。"""
        views = [_view(1, exists={"source_raw.json": True}, source_raw={"status": "ok"})]
        state = _run_state(operator_src_snapshot="inputs/src_snapshot")
        self.assertEqual(pr.source_extract_verdict(state, views, self._inputs_full())["status"], "passed")

    def test_source_raw_only_unconfirmed(self):
        views = [_view(1, exists={"source_raw.json": True}, source_raw={"status": "ok"})]
        state = _run_state(operator_src_snapshot="inputs/src_snapshot")
        self.assertEqual(pr.source_extract_verdict(state, views, _inputs())["status"], "unconfirmed")

    def test_inputs_docs_without_source_raw_unconfirmed(self):
        """inputs 文档存在但无 source_raw → 归属待确认。"""
        views = [_view(1)]
        state = _run_state(operator_src_snapshot="inputs/src_snapshot")
        entry = pr.source_extract_verdict(state, views, self._inputs_full())
        self.assertEqual(entry["status"], "unconfirmed")
        self.assertIn("归属", entry["basis"])

    def test_knowledge_flag_is_not_execution(self):
        """source_analysis_knowledge 仅知识加载，不等于执行分析。"""
        views = [_view(1)]
        state = _run_state(source_analysis_knowledge=True)
        self.assertEqual(pr.source_extract_verdict(state, views, _inputs())["status"], "not_involved")

    def test_diagnose_domain_independent(self):
        view = _view(2, exists={"source_evidence.json": True}, source_evidence={"status": "ok"})
        self.assertEqual(pr.source_diagnose_verdict(view)["status"], "passed")
        self.assertIsNone(pr.source_diagnose_verdict(_view(1)))


class TestSupplement(unittest.TestCase):
    def test_nonempty_patch_passed_application_pending(self):
        """有效非空补丁 → passed，应用情况"待确认"（合并归主协调器）。"""
        view = _view(2, exists={"constraints_patch.json": True},
                     constraints_patch={"status": "ok", "empty": False, "count": 3})
        entry = pr.supplement_verdict(view, _inputs())
        self.assertEqual(entry["status"], "passed")
        self.assertEqual(entry["application"], "非空补丁应用结果待确认")

    def test_empty_patch_valid_no_merge_needed(self):
        """合法空补丁 → passed"无需合并"，不算 broken。"""
        view = _view(2, exists={"constraints_patch.json": True},
                     constraints_patch={"status": "ok", "empty": True, "count": 0})
        entry = pr.supplement_verdict(view, _inputs())
        self.assertEqual(entry["status"], "passed")
        self.assertEqual(entry["application"], "空补丁无需应用")

    def test_broken_patch_unconfirmed(self):
        view = _view(2, exists={"constraints_patch.json": True},
                     constraints_patch={"status": "broken", "empty": None, "count": None, "error": "缺少 op 字段"})
        self.assertEqual(pr.supplement_verdict(view, _inputs())["status"], "unconfirmed")

    def test_input_without_patch_unconfirmed(self):
        """有补充材料（非空文档）无补丁 → unconfirmed；空文档不算材料。"""
        view = _view(2)
        with_input = _inputs(**{"supplementary-doc.md": {"status": "ok", "size": 500}})
        self.assertEqual(pr.supplement_verdict(view, with_input)["status"], "unconfirmed")
        empty_input = _inputs(**{"supplement_constraints.md": {"status": "ok", "size": 0}})
        self.assertEqual(pr.supplement_verdict(view, empty_input)["status"], "not_involved")

    def test_origin_counts_auxiliary_only(self):
        """origin 计数只作辅助信息，不改变状态。"""
        view = _view(2, exists={"constraints_patch.json": True},
                     constraints_patch={"status": "ok", "empty": False, "count": 2},
                     constraints_origins={"supplement": 5})
        entry = pr.supplement_verdict(view, _inputs())
        self.assertEqual(entry["origins"]["origin_supplement"], 5)


class TestOptimizer(unittest.TestCase):
    def test_proposal_passed_with_broken_decisions(self):
        """有效提案 + 裁决损坏 → 提案仍 passed、裁决区显示读取异常（不互相推翻）。"""
        views = [_view(2, exists={"prompt_update_proposal.json": True},
                       prompt_update_proposal={"status": "ok"})]
        inputs = _inputs(**{"prompt_update_decisions.json": {"status": "broken", "error": "decisions 必须是数组"}})
        entry = pr.optimizer_verdict(_run_state(), views, inputs)
        self.assertEqual(entry["status"], "passed")
        self.assertEqual(entry["decisions"]["status"], "broken")

    def test_decisions_not_replacing_proposal(self):
        """裁决完好且非空 → 独立展示计数。"""
        views = [_view(2, exists={"prompt_update_proposal.json": True},
                       prompt_update_proposal={"status": "ok"})]
        inputs = _inputs(**{"prompt_update_decisions.json": {"status": "ok", "count": 2}})
        entry = pr.optimizer_verdict(_run_state(), views, inputs)
        self.assertEqual(entry["decisions"]["count"], 2)

    def test_explicitly_not_triggered_not_involved(self):
        """无提案 + 诊断明确不走优化路径 → not_involved（成功/止损同理）。"""
        views = [_view(2, exists={"analysis.json": True},
                       analysis={"root_cause": "executor_bug", "overall_action": "STOP"})]
        self.assertEqual(pr.optimizer_verdict(_run_state(), views, _inputs())["status"], "not_involved")
        self.assertEqual(pr.optimizer_verdict(_run_state(state="SUCCESS"), [], _inputs())["status"], "not_involved")

    def test_triggered_but_missing_proposal_unconfirmed(self):
        views = [_view(2, exists={"analysis.json": True},
                       analysis={"root_cause": "constraint_extraction", "overall_action": "UPDATE_CONSTRAINTS"})]
        self.assertEqual(pr.optimizer_verdict(_run_state(), views, _inputs())["status"], "unconfirmed")

    def test_no_evidence_unconfirmed_not_skipped(self):
        """证据不全（非终态、无诊断）→ unconfirmed，"没读到证据"≠"明确不满足"。"""
        self.assertEqual(pr.optimizer_verdict(_run_state(state="DIAGNOSE"), [], _inputs())["status"],
                         "unconfirmed")

    def test_empty_decisions_counted_honestly(self):
        """decisions 空数组 → 如实计 0 条，不判跳过。"""
        inputs = _inputs(**{"prompt_update_decisions.json": {"status": "ok", "count": 0}})
        entry = pr.optimizer_verdict(_run_state(state="SUCCESS"), [], inputs)
        self.assertEqual(entry["status"], "not_involved")
        self.assertEqual(entry["decisions"]["count"], 0)


class TestSceneVerdict(unittest.TestCase):
    def test_disabled_not_involved(self):
        self.assertEqual(pr.scene_verdict(_run_state(scene={"enabled": False}), _inputs())["status"],
                         "not_involved")

    def test_valid_scan_passed_without_scenarios(self):
        """has_scenarios=false 空设备列表也是合法完成。"""
        inputs = _inputs(**{"scene_scan.json": {"status": "ok", "has_scenarios": False, "device_types": []}})
        self.assertEqual(pr.scene_verdict(_run_state(scene={"enabled": True}), inputs)["status"], "passed")

    def test_broken_or_missing_scan_unconfirmed(self):
        broken = _inputs(**{"scene_scan.json": {"status": "broken"}})
        self.assertEqual(pr.scene_verdict(_run_state(scene={"enabled": True}), broken)["status"],
                         "unconfirmed")
        self.assertEqual(pr.scene_verdict(_run_state(scene={"enabled": True}), _inputs())["status"],
                         "unconfirmed")


class TestPerRoundStates(unittest.TestCase):
    def _sample_round(self, n=2):
        return _view(n,
                     exists={"constraint_check.json": True, "generation_summary.json": True,
                             "execution_result.json": True, "quality_gate.json": True,
                             "analysis.json": True},
                     constraint_check={"status": "passed", "current_round": 1, "max_rounds": 3,
                                       "issues_open": 0, "issues_fixed": 0, "issues_unfixed": 0},
                     generation={"summary": {"total": 10}, "progress": {"state": "complete", "pid_alive": False}},
                     execution={"status_raw": "success", "verdict": "partial_pass",
                                "passed": 5, "failed": 5, "total": 10, "engine_error": None},
                     quality_gate={"status": "pass", "status_raw": "passed", "next_state": "DIAGNOSE",
                                   "blocking_issues": [], "checks": []},
                     analysis={"overall_action": "UPDATE_CONSTRAINTS", "root_cause": "constraint_extraction",
                               "failure_cluster_count": 1, "constraint_finding_count": 2})

    def test_round_states_complete_and_inferred(self):
        states = pr.per_round_states(_run_state(), [self._sample_round(2)], _inputs())
        self.assertIn(2, states)
        self.assertEqual(len(states[2]), 12)
        for name, entry in states[2].items():
            self.assertIn(entry["status"], ("pending", "running", "passed", "rejected",
                                            "skipped", "unconfirmed", "not_involved"))
            self.assertTrue(entry["basis"], name)
            self.assertTrue(entry["inferred"])

    def test_updater_unconfirmed_when_analysis_says_update(self):
        """有诊断建议更新但无更新报告 → unconfirmed（旧版判 skipped 属弱证据）。"""
        states = pr.per_round_states(_run_state(), [self._sample_round(2)], _inputs())
        self.assertEqual(states[2]["constraint-updater"]["status"], "unconfirmed")

    def test_extractor_not_involved_after_round_one(self):
        states = pr.per_round_states(_run_state(), [self._sample_round(2)], _inputs())
        self.assertEqual(states[2]["constraint-extractor"]["status"], "not_involved")

    def test_historical_running_becomes_unconfirmed(self):
        """历史轮次的 running 证据 → 待确认（遗留），不用任务终局覆盖历史。"""
        view = _view(1, exists={"generation_progress.json": True},
                     generation={"progress": {"state": "running", "pid_alive": True}})
        states = pr.per_round_states(_run_state(current_iteration=2), [view, self._sample_round(2)], _inputs())
        self.assertEqual(states[1]["case-generator"]["status"], "unconfirmed")

    def test_infer_agents_uses_shared_states(self):
        agents = infer_agents(_run_state(), [self._sample_round(2)], _inputs())
        by_name = {a["name"]: a for a in agents}
        self.assertEqual(len(agents), 12)
        self.assertEqual(by_name["constraint-updater"]["status"], "unconfirmed")
        self.assertTrue(all(a.get("ruleset") == "v2" for a in agents))

    def test_run_level_roles_scoped_to_acting_round(self):
        """任务级角色（场景扫描）只在首次发生轮体现：体现参与流程而非参与力度，
        后续轮次即使使用其产物也不显示为参与。"""
        scene_inputs = _inputs(**{"scene_scan.json": {"status": "ok", "has_scenarios": True,
                                                      "device_types": ["A2"]}})
        state = _run_state(scene={"enabled": True}, current_iteration=2)
        states = pr.per_round_states(state, [self._sample_round(1), self._sample_round(2)],
                                     scene_inputs)
        self.assertEqual(states[1]["scene-scanner"]["status"], "passed")
        self.assertEqual(states[2]["scene-scanner"]["status"], "not_involved")
        self.assertIn("仅在", states[2]["scene-scanner"]["basis"])

    def test_optimizer_scoped_to_proposal_round(self):
        """提示词优化只在提案轮体现；无提案时挂在当前轮。"""
        r1 = self._sample_round(1)
        r2 = self._sample_round(2)
        r2["exists"]["prompt_update_proposal.json"] = True
        r2["prompt_update_proposal"] = {"status": "ok"}
        states = pr.per_round_states(_run_state(current_iteration=2), [r1, r2], _inputs())
        self.assertEqual(states[2]["prompt-optimizer"]["status"], "passed")
        self.assertEqual(states[1]["prompt-optimizer"]["status"], "not_involved")


class TestReplayOrdering(unittest.TestCase):
    def _round(self, n):
        view = TestPerRoundStates()._sample_round(n)
        view["exists"]["constraint_update.json"] = True
        view["constraint_update"] = {"status": "updated", "change_count": 3, "changes": []}
        view["mtimes"] = {"constraint_update.json": "2026-01-0%dT00:00:00" % n}
        return view

    def test_updater_is_round_opening_event(self):
        """约束更新是轮次开场动作：本轮事件序列中排首位。"""
        from workbench.adapters import replay as replay_adapter
        views = [self._round(2), self._round(3)]
        events = replay_adapter.build_replay(_run_state(current_iteration=3), views, _inputs())
        round2 = [e["agent"] for e in events if e["iteration"] == 2]
        self.assertEqual(round2[0], "constraint-updater")

    def test_cross_round_updater_carries_from_source(self):
        """跨轮开场：第 3 轮 updater 带 from_agent/from_round 指回第 2 轮诊断。"""
        from workbench.adapters import replay as replay_adapter
        views = [self._round(2), self._round(3)]
        events = replay_adapter.build_replay(_run_state(current_iteration=3), views, _inputs())
        updater3 = next(e for e in events
                        if e["agent"] == "constraint-updater" and e["iteration"] == 3)
        self.assertEqual(updater3["from_agent"], "failure-analyst")
        self.assertEqual(updater3["from_round"], 2)
        analyst2 = next(e for e in events
                        if e["agent"] == "failure-analyst" and e["iteration"] == 2)
        self.assertEqual(analyst2["to_agent"], "constraint-updater")
        self.assertEqual(analyst2["to_round"], 3)


class TestCheckRepairExpansion(unittest.TestCase):
    """检查-修复循环按 current_round / issues 时间线展开为逐次处理实例。"""

    def _round(self, current_round, issues, status="passed", **counts):
        view = _view(2, exists={"constraint_check.json": True, "generation_summary.json": True},
                     constraint_check={"status": status, "current_round": current_round,
                                       "max_rounds": 3, "issues": issues, **counts})
        return view

    def test_two_round_loop_expands_to_instances(self):
        """round=2 + issue 第 1 次发现、第 2 次确认修复 → 检查①退回→修复→复检②通过→生成。"""
        from workbench.adapters import replay as replay_adapter
        view = self._round(2, [{"id": "CR-001", "status": "fixed",
                                "found_round": 1, "last_checked_round": 2}],
                           issues_open=0, issues_fixed=1, issues_unfixed=0)
        events = replay_adapter.build_replay(_run_state(current_iteration=2), [view], _inputs())
        chain = [(e["agent"], e["action"], e.get("to_agent"))
                 for e in events if e["agent"] in ("constraint-checker", "constraint-repairer")]
        self.assertEqual(chain, [
            ("constraint-checker", "rejected", "constraint-repairer"),
            ("constraint-repairer", "passed", "constraint-checker"),
            ("constraint-checker", "passed", "case-generator"),
        ])

    def test_single_round_not_expanded(self):
        """current_round=1：不展开；跳过的修复员不生成节点事件（本轮没参与不显示）。"""
        from workbench.adapters import replay as replay_adapter
        view = self._round(1, [], issues_open=0, issues_fixed=0, issues_unfixed=0)
        events = replay_adapter.build_replay(_run_state(current_iteration=2), [view], _inputs())
        chain = [e["agent"] for e in events
                 if e["agent"] in ("constraint-checker", "constraint-repairer")]
        self.assertEqual(chain, ["constraint-checker"])
        # 跳过不出现在回放（横条状态仍保留"跳过"文字解释）
        self.assertFalse(any(e["action"] == "skipped" for e in events))

    def test_no_evidence_between_rounds_marks_unconfirmed(self):
        """两次检查之间无 issue 由 open 转 fixed → 修复实例标待确认，不臆造。"""
        from workbench.adapters import replay as replay_adapter
        view = self._round(2, [{"id": "CR-001", "status": "unfixed",
                                "found_round": 1, "last_checked_round": 2}],
                           status="failed", issues_open=1, issues_fixed=0, issues_unfixed=1)
        events = replay_adapter.build_replay(_run_state(current_iteration=2), [view], _inputs())
        repair = [e for e in events if e["agent"] == "constraint-repairer"]
        self.assertEqual(repair[0]["action"], "unconfirmed")
        self.assertIn("无修复证据", repair[0]["basis"])


class TestHandoffTargets(unittest.TestCase):
    def test_explicit_targets_only(self):
        view = self._sample = TestPerRoundStates()._sample_round(2)
        passed_entry = {"status": "passed"}
        self.assertEqual(pr.handoff_target(view, "case-generator", passed_entry), "case-executor")
        self.assertEqual(pr.handoff_target(view, "quality-reviewer", passed_entry), "failure-analyst")
        self.assertEqual(pr.handoff_target(view, "failure-analyst", passed_entry), "constraint-updater")

    def test_rejected_gets_no_target(self):
        view = TestPerRoundStates()._sample_round(2)
        self.assertIsNone(pr.handoff_target(view, "case-generator", {"status": "rejected"}))

    def test_reviewer_success_no_target(self):
        view = _view(2, exists={"quality_gate.json": True},
                     quality_gate={"status": "pass", "next_state": "SUCCESS", "blocking_issues": []})
        self.assertIsNone(pr.handoff_target(view, "quality-reviewer", {"status": "passed"}))


if __name__ == "__main__":
    unittest.main()
