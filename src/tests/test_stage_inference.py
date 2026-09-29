"""agent 状态推导规则测试（对应计划边界#3/#4 与规则表）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.adapters.stage_inference import AGENTS_FLOW, infer_agents


def _view(n, exists=None, **sections):
    base_exists = {name: False for name in (
        "constraints.json", "constraint_check.json", "cases.json", "execution_result.json",
        "analysis.json", "quality_gate.json", "generation_summary.json", "generation_progress.json",
        "constraint_update.json", "regression_check.json", "extraction_provenance.json",
        "constraints.json.pre_repair_local",
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
        "execution": None,
        "quality_gate": None,
        "regression": None,
        "analysis": None,
        "constraint_update": None,
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


def _by_name(agents):
    return {a["name"]: a for a in agents}


class TestInferAgents(unittest.TestCase):
    def test_flow_order_fixed(self):
        names = [s["name"] for s in AGENTS_FLOW]
        self.assertEqual(names[0], "scene-scanner")
        self.assertEqual(names[6], "case-generator")
        self.assertEqual(names[-1], "prompt-optimizer")
        self.assertEqual(len(names), 12)

    def test_checker_passed(self):
        view = _view(1, exists={"constraint_check.json": True},
                     constraint_check={"status": "passed", "current_round": 1, "max_rounds": 3,
                                       "issues_open": 0, "issues_unfixed": 0})
        agents = _by_name(infer_agents(_run_state(), [view]))
        self.assertEqual(agents["constraint-checker"]["status"], "passed")
        self.assertTrue(agents["constraint-checker"]["inferred"])
        self.assertIn("iter_001", agents["constraint-checker"]["basis"])

    def test_checker_rejected_with_open_issues(self):
        view = _view(1, exists={"constraint_check.json": True},
                     constraint_check={"status": "failed", "current_round": 2, "max_rounds": 3,
                                       "issues_open": 1, "issues_unfixed": 0})
        agents = _by_name(infer_agents(_run_state(), [view]))
        self.assertEqual(agents["constraint-checker"]["status"], "rejected")

    def test_repairer_skipped_without_backup(self):
        view = _view(1, exists={"constraint_check.json": True},
                     constraint_check={"status": "passed", "current_round": 1, "max_rounds": 3})
        agents = _by_name(infer_agents(_run_state(), [view]))
        self.assertEqual(agents["constraint-repairer"]["status"], "skipped")

    def test_repairer_passed_with_backup(self):
        view = _view(1, exists={"constraint_check.json": True, "constraints.json.pre_repair_local": True},
                     constraint_check={"status": "passed", "current_round": 2, "max_rounds": 3})
        agents = _by_name(infer_agents(_run_state(), [view]))
        self.assertEqual(agents["constraint-repairer"]["status"], "passed")

    def test_executor_engine_error_rejected(self):
        view = _view(1, exists={"execution_result.json": True},
                     execution={"verdict": "engine_error", "engine_error": "ATK 远程执行失败",
                                "passed": 0, "failed": 0, "total": 10})
        agents = _by_name(infer_agents(_run_state(), [view]))
        self.assertEqual(agents["case-executor"]["status"], "rejected")

    def test_executor_status_field_not_verdict(self):
        """status=success 但 failed>0 时 executor 仍 passed（用例级失败由 analyst 处理）。"""
        view = _view(1, exists={"execution_result.json": True},
                     execution={"verdict": "partial_pass", "engine_error": None,
                                "passed": 5, "failed": 5, "total": 10})
        agents = _by_name(infer_agents(_run_state(), [view]))
        exe = agents["case-executor"]
        self.assertEqual(exe["status"], "passed")
        self.assertIn("partial_pass", exe["basis"])

    def test_updater_skipped_when_analysis_only(self):
        """iter_004 实测形态：有 analysis 无 constraint_update → skipped（仅扩量）。"""
        view = _view(4, exists={"analysis.json": True, "cases.json": True},
                     analysis={"overall_action": "UPDATE_CONSTRAINTS", "failure_cluster_count": 1},
                     cases_count=100)
        agents = _by_name(infer_agents(_run_state(), [view]))
        upd = agents["constraint-updater"]
        self.assertEqual(upd["status"], "skipped")
        self.assertIn("100", upd["basis"])

    def test_scene_scanner_not_involved(self):
        agents = _by_name(infer_agents(_run_state(scene={"enabled": False}), []))
        self.assertEqual(agents["scene-scanner"]["status"], "not_involved")

    def test_extractor_running_when_extract_state(self):
        agents = _by_name(infer_agents(
            _run_state(state="EXTRACT", current_iteration=1), [_view(1)]))
        self.assertEqual(agents["constraint-extractor"]["status"], "running")

    def test_generator_running_when_pid_alive(self):
        view = _view(1, exists={"generation_progress.json": True},
                     generation={"progress": {"state": "running", "pid_alive": True, "total": 0}})
        agents = _by_name(infer_agents(_run_state(state="GENERATE"), [view]))
        self.assertEqual(agents["case-generator"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
