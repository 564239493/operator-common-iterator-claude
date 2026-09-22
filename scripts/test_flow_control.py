"""flow_control 状态推进器的单元/集成测试。

跑法: python scripts/test_flow_control.py [-v]   （stdlib unittest，零新依赖）

覆盖:
- CLI 管道（subprocess）: exit code、stdout JSON、run_state.json 落盘效果
- 轻判据状态全链路（PLAN/GENERATE/EXECUTE/GATE/等待态/终态）: 真实夹具
- 重校验状态（EXTRACT/UPDATE_CONSTRAINTS/DIAGNOSE）: 进程内 monkeypatch 校验器
- run_state.py 兜底边校验: 合法边放行 / 非法边 exit 2 / --force 逃生口
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flow_control  # noqa: E402  （进程内测试需 monkeypatch 其校验器引用）

REPO = Path(__file__).resolve().parents[1]
FLOW = REPO / "scripts" / "flow_control.py"
RUN_STATE = REPO / "scripts" / "run_state.py"

EXEC_OK = {
    "status": "success",
    "mode": "mock",
    "passed": 2,
    "failed": 0,
    "total": 2,
    "records": [],
    "engine_error": "",
}


def run_cli(script: Path, *args: str):
    proc = subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True
    )
    return proc.returncode, proc.stdout.strip()


def flow(*args: str):
    rc, out = run_cli(FLOW, *args)
    try:
        payload = json.loads(out)
    except ValueError:
        payload = {"_raw": out}
    return rc, payload


def write_run(
    run_dir: Path,
    state: str,
    *,
    iteration: int = 1,
    max_iterations: int = 5,
    **extra,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": run_dir.name,
        "state": state,
        "current_iteration": iteration,
        "max_iterations": max_iterations,
        "run_scope": "full",
        "test_framework": "atk",
        "supplement_revision": 0,
        "supplement_hash": "",
        "last_consumed_supplement_hash": "",
        "constraint_check": {
            "max_rounds": 3,
            "iteration": 0,
            "current_round": 0,
            "status": "pending",
            "report": "",
        },
        "history": [],
    }
    data.update(extra)
    (run_dir / "run_state.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return run_dir


def write_iter(run_dir: Path, iteration: int, name: str, payload) -> None:
    iter_dir = run_dir / f"iter_{iteration:03d}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    (iter_dir / name).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def read_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))


class FlowControlCliTests(unittest.TestCase):
    """subprocess 级：CLI 管道 + 轻判据状态。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_plan_to_extract(self):
        run_dir = write_run(self.tmp / "r1", "PLAN")
        (run_dir / "inputs").mkdir()
        (run_dir / "inputs" / "prompt_v1.md").write_text("prompt")
        (run_dir / "inputs" / "aclnnFoo.md").write_text("doc")
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "TRANSIT")
        self.assertEqual(payload["to_state"], "EXTRACT")
        self.assertTrue(payload["applied"])
        state = read_state(run_dir)
        self.assertEqual(state["state"], "EXTRACT")
        self.assertEqual(state["history"][-1]["state"], "EXTRACT")
        self.assertTrue(
            (run_dir / "iter_001" / "transition_decision.json").is_file()
        )

    def test_plan_hold_when_inputs_missing(self):
        run_dir = write_run(self.tmp / "r2", "PLAN")
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "HOLD")
        self.assertEqual(read_state(run_dir)["state"], "PLAN")
        self.assertEqual(read_state(run_dir)["history"], [])

    def test_missing_run_dir_is_error(self):
        rc, payload = flow("advance", "--run-dir", str(self.tmp / "nope"))
        self.assertEqual(rc, 2)
        self.assertEqual(payload["decision"], "ERROR")

    def test_terminal_noop(self):
        run_dir = write_run(self.tmp / "r3", "SUCCESS")
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "NOOP")
        self.assertFalse(payload["applied"])
        self.assertEqual(read_state(run_dir)["state"], "SUCCESS")

    def test_evaluate_never_writes(self):
        run_dir = write_run(self.tmp / "r4", "EXTRACT")
        rc, payload = flow("evaluate", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "HOLD")
        self.assertFalse(payload["applied"])
        self.assertEqual(read_state(run_dir)["state"], "EXTRACT")
        self.assertFalse((run_dir / "iter_001" / "transition_decision.json").exists())

    # --- GENERATE ----------------------------------------------------------

    def test_generate_to_execute(self):
        run_dir = write_run(self.tmp / "g1", "GENERATE")
        write_iter(run_dir, 1, "generation_summary.json", {"total": 2})
        write_iter(run_dir, 1, "cases.json", [{"id": "case_001"}])
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "EXECUTE")
        self.assertEqual(read_state(run_dir)["state"], "EXECUTE")

    def test_generate_hold_while_running(self):
        run_dir = write_run(self.tmp / "g2", "GENERATE")
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "HOLD")
        self.assertEqual(read_state(run_dir)["state"], "GENERATE")

    def test_generate_blocked_on_failed_signal(self):
        run_dir = write_run(self.tmp / "g3", "GENERATE")
        write_iter(
            run_dir,
            1,
            "generation_status.json",
            {"state": "failed", "error": "ZERO_CASES_GENERATED"},
        )
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "BLOCKED")
        self.assertEqual(read_state(run_dir)["state"], "BLOCKED")

    # --- EXECUTE -----------------------------------------------------------

    def test_execute_to_gate(self):
        run_dir = write_run(self.tmp / "e1", "EXECUTE")
        write_iter(run_dir, 1, "execution_result.json", dict(EXEC_OK))
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "GATE")
        self.assertEqual(read_state(run_dir)["state"], "GATE")

    def test_execute_blocked_on_engine_error(self):
        run_dir = write_run(self.tmp / "e2", "EXECUTE")
        fixture = dict(EXEC_OK, engine_error="ssh connection refused")
        write_iter(run_dir, 1, "execution_result.json", fixture)
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "BLOCKED")
        self.assertEqual(read_state(run_dir)["state"], "BLOCKED")

    def test_execute_hold_while_running(self):
        run_dir = write_run(self.tmp / "e3", "EXECUTE")
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "HOLD")

    # --- GATE --------------------------------------------------------------

    def _gate_ready(self, run_dir: Path, failed: int, check_status: str = "passed"):
        execution = dict(EXEC_OK, failed=failed, passed=2 - failed, total=2)
        write_iter(run_dir, 1, "execution_result.json", execution)
        write_iter(
            run_dir,
            1,
            "quality_gate.json",
            {"status": "passed", "blocking_issues": [], "next_state": "IGNORED"},
        )
        write_iter(
            run_dir,
            1,
            "constraint_check.json",
            {"status": check_status, "iteration": 1, "current_round": 1, "max_rounds": 3},
        )

    def test_gate_to_success(self):
        run_dir = write_run(self.tmp / "q1", "GATE")
        self._gate_ready(run_dir, failed=0)
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "SUCCESS")
        self.assertEqual(read_state(run_dir)["state"], "SUCCESS")

    def test_gate_to_diagnose(self):
        run_dir = write_run(self.tmp / "q2", "GATE")
        self._gate_ready(run_dir, failed=2)
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "DIAGNOSE")
        self.assertEqual(read_state(run_dir)["state"], "DIAGNOSE")

    def test_gate_to_blocked_on_blocking(self):
        run_dir = write_run(self.tmp / "q3", "GATE")
        execution = dict(EXEC_OK)
        write_iter(run_dir, 1, "execution_result.json", execution)
        write_iter(
            run_dir,
            1,
            "quality_gate.json",
            {"status": "blocked", "blocking_issues": ["dummy golden marker"]},
        )
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "BLOCKED")

    def test_gate_hold_while_reviewing(self):
        run_dir = write_run(self.tmp / "q4", "GATE")
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "HOLD")

    # --- EXTRACT（subprocess 只测免重校验器的路径） ---------------------------

    def test_extract_blocked_on_check_failed(self):
        run_dir = write_run(self.tmp / "x1", "EXTRACT")
        write_iter(
            run_dir,
            1,
            "constraint_check.json",
            {"status": "failed", "iteration": 1, "current_round": 3, "max_rounds": 3},
        )
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "BLOCKED")
        state = read_state(run_dir)
        self.assertEqual(state["state"], "BLOCKED")
        self.assertEqual(
            state["history"][-1].get("code"), "CONSTRAINT_CHECK_FAILED"
        )

    def test_extract_blocked_on_rounds_exhausted(self):
        run_dir = write_run(self.tmp / "x2", "EXTRACT")
        write_iter(
            run_dir,
            1,
            "constraint_check.json",
            {"status": "needs_repair", "iteration": 1, "current_round": 3, "max_rounds": 3},
        )
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "BLOCKED")

    def test_extract_hold_while_checking(self):
        run_dir = write_run(self.tmp / "x3", "EXTRACT")
        write_iter(
            run_dir,
            1,
            "constraint_check.json",
            {"status": "needs_repair", "iteration": 1, "current_round": 1, "max_rounds": 3},
        )
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "HOLD")
        self.assertEqual(read_state(run_dir)["state"], "EXTRACT")

    # --- 等待态 -------------------------------------------------------------

    def test_needs_human_transits_to_checkpoint(self):
        run_dir = write_run(self.tmp / "w1", "NEEDS_HUMAN_EVIDENCE")
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "HUMAN_CHECKPOINT")
        self.assertEqual(read_state(run_dir)["state"], "HUMAN_CHECKPOINT")

    def test_mixed_hold_without_decision(self):
        run_dir = write_run(self.tmp / "w2", "MIXED_FAILURE_REVIEW")
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "HOLD")
        self.assertEqual(read_state(run_dir)["state"], "MIXED_FAILURE_REVIEW")

    def test_mixed_stop_goes_to_stopped_by_user(self):
        run_dir = write_run(self.tmp / "w3", "MIXED_FAILURE_REVIEW")
        rc, payload = flow(
            "advance", "--run-dir", str(run_dir), "--user-decision", "stop"
        )
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "STOPPED_BY_USER")
        self.assertEqual(read_state(run_dir)["state"], "STOPPED_BY_USER")

    def test_human_checkpoint_hold_until_supplement(self):
        run_dir = write_run(
            self.tmp / "w4",
            "HUMAN_CHECKPOINT",
            supplement_revision=1,
            supplement_hash="h0",
            last_consumed_supplement_hash="",
        )
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "HOLD")

    def test_human_checkpoint_supplement_updated_goes_to_diagnose(self):
        run_dir = write_run(
            self.tmp / "w5",
            "HUMAN_CHECKPOINT",
            supplement_revision=2,
            supplement_hash="h1",
            last_consumed_supplement_hash="",
        )
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["to_state"], "DIAGNOSE")
        self.assertEqual(read_state(run_dir)["state"], "DIAGNOSE")

    def test_human_checkpoint_consumed_hash_holds(self):
        run_dir = write_run(
            self.tmp / "w6",
            "HUMAN_CHECKPOINT",
            supplement_revision=2,
            supplement_hash="h1",
            last_consumed_supplement_hash="h1",
        )
        rc, payload = flow("advance", "--run-dir", str(run_dir))
        self.assertEqual(rc, 0)
        self.assertEqual(payload["decision"], "HOLD")


class InProcessDecisionTests(unittest.TestCase):
    """进程内 monkeypatch 重校验器：EXTRACT/UPDATE/DIAGNOSE 的重判据路径。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._patches: list[tuple[str, object]] = []

    def tearDown(self):
        for name, orig in self._patches:
            setattr(flow_control, name, orig)
        self._tmp.cleanup()

    def _patch(self, name: str, fake):
        self._patches.append((name, getattr(flow_control, name)))
        setattr(flow_control, name, fake)

    def _decide_and_apply(self, run_dir: Path, user_decision: str | None = None):
        rc = flow_control.run_flow(run_dir, dry_run=False, user_decision=user_decision)
        return rc, read_state(run_dir)

    def test_extract_to_generate(self):
        self._patch("validate_constraints", lambda value: [])
        run_dir = write_run(self.tmp / "a1", "EXTRACT")
        write_iter(
            run_dir,
            1,
            "constraint_check.json",
            {"status": "passed", "iteration": 1, "current_round": 2, "max_rounds": 3},
        )
        write_iter(run_dir, 1, "constraints.json", {"any": "thing"})
        rc, state = self._decide_and_apply(run_dir)
        self.assertEqual(rc, 0)
        self.assertEqual(state["state"], "GENERATE")

    def test_extract_to_success_constraints_only(self):
        self._patch("validate_constraints", lambda value: [])
        run_dir = write_run(self.tmp / "a2", "EXTRACT", run_scope="constraints_only")
        write_iter(
            run_dir,
            1,
            "constraint_check.json",
            {"status": "passed", "iteration": 1, "current_round": 1, "max_rounds": 3},
        )
        write_iter(run_dir, 1, "constraints.json", {"any": "thing"})
        rc, state = self._decide_and_apply(run_dir)
        self.assertEqual(rc, 0)
        self.assertEqual(state["state"], "SUCCESS")
        self.assertEqual(
            state["history"][-1].get("event"), "CONSTRAINTS_ONLY_SUCCESS"
        )

    def test_extract_hold_when_constraints_invalid(self):
        self._patch(
            "validate_constraints", lambda value: ["constraints[0] missing field: id"]
        )
        run_dir = write_run(self.tmp / "a3", "EXTRACT")
        write_iter(
            run_dir,
            1,
            "constraint_check.json",
            {"status": "passed", "iteration": 1, "current_round": 1, "max_rounds": 3},
        )
        write_iter(run_dir, 1, "constraints.json", {"any": "thing"})
        rc, state = self._decide_and_apply(run_dir)
        self.assertEqual(rc, 0)
        self.assertEqual(state["state"], "EXTRACT")

    def test_update_to_generate(self):
        self._patch("validate_constraints", lambda value: [])
        self._patch("validate_constraint_update", lambda value: [])
        run_dir = write_run(self.tmp / "a4", "UPDATE_CONSTRAINTS", iteration=2)
        write_iter(run_dir, 2, "constraints.json", {"any": "thing"})
        write_iter(run_dir, 2, "constraint_update.json", {"any": "thing"})
        write_iter(
            run_dir,
            2,
            "constraint_check.json",
            {"status": "passed", "iteration": 2, "current_round": 1, "max_rounds": 3},
        )
        rc, state = self._decide_and_apply(run_dir)
        self.assertEqual(rc, 0)
        self.assertEqual(state["state"], "GENERATE")
        self.assertEqual(state["current_iteration"], 2)

    def test_update_user_stop_goes_blocked(self):
        run_dir = write_run(self.tmp / "a5", "UPDATE_CONSTRAINTS", iteration=2)
        rc, state = self._decide_and_apply(run_dir, user_decision="stop")
        self.assertEqual(rc, 0)
        self.assertEqual(state["state"], "BLOCKED")
        self.assertEqual(
            state["history"][-1].get("code"),
            "CONSTRAINT_REGRESSION_USER_ABORT",
        )

    def test_update_hold_until_check_repassed(self):
        self._patch("validate_constraints", lambda value: [])
        self._patch("validate_constraint_update", lambda value: [])
        run_dir = write_run(self.tmp / "a6", "UPDATE_CONSTRAINTS", iteration=2)
        write_iter(run_dir, 2, "constraints.json", {"any": "thing"})
        write_iter(run_dir, 2, "constraint_update.json", {"any": "thing"})
        write_iter(
            run_dir,
            2,
            "constraint_check.json",
            {"status": "needs_repair", "iteration": 2, "current_round": 1, "max_rounds": 3},
        )
        rc, state = self._decide_and_apply(run_dir)
        self.assertEqual(rc, 0)
        self.assertEqual(state["state"], "UPDATE_CONSTRAINTS")

    def _diagnose_run(
        self,
        name: str,
        clusters,
        findings,
        declared: str,
        iteration: int = 1,
        max_iterations: int = 5,
    ) -> tuple[Path, dict]:
        self._patch("validate_analysis", lambda value: [])
        run_dir = write_run(
            self.tmp / name, "DIAGNOSE", iteration=iteration, max_iterations=max_iterations
        )
        write_iter(
            run_dir,
            iteration,
            "analysis.json",
            {
                "schema_version": "2.1",
                "failure_clusters": clusters,
                "constraint_findings": findings,
                "overall_action": declared,
            },
        )
        rc = flow_control.run_flow(run_dir, dry_run=False, user_decision=None)
        return run_dir, json.loads(
            (run_dir / f"iter_{iteration:03d}" / "transition_decision.json")
            .read_text(encoding="utf-8")
        )

    def test_diagnose_routes_update_with_bump(self):
        run_dir, payload = self._diagnose_run(
            "d1",
            [{"id": "FC-1", "root_cause": "constraint_extraction"}],
            [{"id": "CF-1", "cluster_ids": ["FC-1"]}],
            "UPDATE_CONSTRAINTS",
        )
        self.assertEqual(payload["to_state"], "UPDATE_CONSTRAINTS")
        self.assertTrue(payload["bump_iteration"])
        state = read_state(run_dir)
        self.assertEqual(state["state"], "UPDATE_CONSTRAINTS")
        self.assertEqual(state["current_iteration"], 2)

    def test_diagnose_routes_max_iterations_when_exhausted(self):
        run_dir, payload = self._diagnose_run(
            "d2",
            [{"id": "FC-1", "root_cause": "constraint_extraction"}],
            [{"id": "CF-1", "cluster_ids": ["FC-1"]}],
            "UPDATE_CONSTRAINTS",
            iteration=3,
            max_iterations=3,
        )
        self.assertEqual(payload["to_state"], "MAX_ITERATIONS")
        self.assertEqual(read_state(run_dir)["state"], "MAX_ITERATIONS")

    def test_diagnose_routes_mixed(self):
        run_dir, payload = self._diagnose_run(
            "d3",
            [
                {"id": "FC-1", "root_cause": "generator_bug"},
                {"id": "FC-2", "root_cause": "executor_bug"},
            ],
            [],
            "MIXED_FAILURE_REVIEW",
        )
        self.assertEqual(payload["to_state"], "MIXED_FAILURE_REVIEW")

    def test_diagnose_routes_needs_human(self):
        run_dir, payload = self._diagnose_run(
            "d4",
            [{"id": "FC-1", "root_cause": "constraint_extraction"}],
            [],
            "NEEDS_HUMAN_EVIDENCE",
        )
        self.assertEqual(payload["to_state"], "HUMAN_CHECKPOINT")

    def test_diagnose_routes_generator_bug(self):
        run_dir, payload = self._diagnose_run(
            "d5",
            [{"id": "FC-1", "root_cause": "generator_bug"}],
            [],
            "STOP_GENERATOR_BUG",
        )
        self.assertEqual(payload["to_state"], "STOP_GENERATOR_BUG")

    def test_diagnose_routes_executor_bug(self):
        run_dir, payload = self._diagnose_run(
            "d6",
            [{"id": "FC-1", "root_cause": "executor_bug"}],
            [],
            "STOP_EXECUTOR_BUG",
        )
        self.assertEqual(payload["to_state"], "STOP_EXECUTOR_BUG")

    def test_diagnose_holds_when_declared_mismatches(self):
        run_dir, payload = self._diagnose_run(
            "d7",
            [{"id": "FC-1", "root_cause": "constraint_extraction"}],
            [{"id": "CF-1", "cluster_ids": ["FC-1"]}],
            "STOP_EXECUTOR_BUG",
        )
        self.assertEqual(payload["decision"], "HOLD")
        self.assertEqual(read_state(run_dir)["state"], "DIAGNOSE")


class RunStateEdgeGuardTests(unittest.TestCase):
    """run_state.py set-state 的兜底边校验（合法放行 / 非法拒绝 / --force）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_legal_edge_allowed(self):
        run_dir = write_run(self.tmp / "s1", "PLAN")
        rc, out = run_cli(RUN_STATE, "set-state", "--run-dir", str(run_dir), "--to", "EXTRACT")
        self.assertEqual(rc, 0)
        self.assertEqual(read_state(run_dir)["state"], "EXTRACT")

    def test_illegal_edge_rejected(self):
        run_dir = write_run(self.tmp / "s2", "PLAN")
        rc, out = run_cli(RUN_STATE, "set-state", "--run-dir", str(run_dir), "--to", "EXECUTE")
        self.assertEqual(rc, 2)
        self.assertIn("FLOW_GUARD_REJECTED", out)
        self.assertEqual(read_state(run_dir)["state"], "PLAN")

    def test_illegal_edge_force_overrides(self):
        run_dir = write_run(self.tmp / "s3", "PLAN")
        rc, out = run_cli(
            RUN_STATE,
            "set-state",
            "--run-dir",
            str(run_dir),
            "--to",
            "EXECUTE",
            "--force",
        )
        self.assertEqual(rc, 0)
        self.assertEqual(read_state(run_dir)["state"], "EXECUTE")

    def test_terminal_has_no_successors(self):
        run_dir = write_run(self.tmp / "s4", "SUCCESS")
        rc, out = run_cli(RUN_STATE, "set-state", "--run-dir", str(run_dir), "--to", "BLOCKED")
        self.assertEqual(rc, 2)
        rc, out = run_cli(
            RUN_STATE,
            "set-state",
            "--run-dir",
            str(run_dir),
            "--to",
            "BLOCKED",
            "--force",
        )
        self.assertEqual(rc, 0)

    def test_same_state_reentry_allowed(self):
        run_dir = write_run(self.tmp / "s5", "EXTRACT")
        rc, out = run_cli(RUN_STATE, "set-state", "--run-dir", str(run_dir), "--to", "EXTRACT")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
