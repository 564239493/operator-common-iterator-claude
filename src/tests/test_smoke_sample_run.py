"""真实样例 run 的端到端解析冒烟测试（不开端口）。

样例缺失时自动跳过；存在时校验适配层在真实数据上的关键不变量。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench import paths
from workbench.adapters import artifacts, run_detail, runs_index

ROOT = paths.project_root(None)
SAMPLE = "aclnnQuantMatmulV5-20260922-052458-932673"


@unittest.skipUnless((ROOT / "runs" / SAMPLE).is_dir(), "样例 run 不存在，跳过冒烟")
class TestSampleRunSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = run_detail.build_run_view(ROOT, SAMPLE)

    def test_runs_index(self):
        runs = runs_index.list_runs(ROOT)
        ids = [r["run_id"] for r in runs]
        self.assertIn(SAMPLE, ids)

    def test_six_iterations(self):
        self.assertEqual(len(self.view["iterations"]), 6)
        self.assertEqual(self.view["current_iteration"], 6)

    def test_segments_have_restart_and_continuation(self):
        kinds = [s["kind"] for s in self.view["segments"]]
        self.assertIn("restart", kinds)
        self.assertIn("continuation", kinds)

    def test_agents_all_have_basis(self):
        agents = self.view["agents"]
        self.assertEqual(len(agents), 12)
        for agent in agents:
            self.assertTrue(agent["basis"], agent["name"])
            self.assertTrue(agent["inferred"])
        by_name = {a["name"]: a for a in agents}
        # 当前轮（iter_006）检查首轮通过：checker passed、repairer skipped（首轮通过无修复证据，
        # 旧版"因 iter_001 有备份故 passed"的跨轮推断已废弃——基线随规则走）
        self.assertEqual(by_name["constraint-checker"]["status"], "passed")
        self.assertEqual(by_name["constraint-repairer"]["status"], "skipped")
        self.assertEqual(by_name["quality-reviewer"]["status"], "passed")
        self.assertEqual(by_name["failure-analyst"]["status"], "passed")

    def test_gate_shapes_normalized(self):
        for view in self.view["iterations"]:
            gate = view["quality_gate"]
            self.assertIsNotNone(gate, "iter_%03d 缺 gate" % view["n"])
            self.assertIn(gate["status"], ("pass", "fail", "warn", "unknown"))
            for check in gate["checks"]:
                self.assertIn(check["result"], ("pass", "fail", "warn", "unknown"))

    def test_regression_is_evaluator_unsupported(self):
        view2 = next(v for v in self.view["iterations"] if v["n"] == 2)
        self.assertIsNotNone(view2["regression"])
        self.assertEqual(view2["regression"]["kind"], "evaluator_unsupported")

    def test_execution_verdict_not_raw_status(self):
        view1 = next(v for v in self.view["iterations"] if v["n"] == 1)
        exe = view1["execution"]
        self.assertEqual(exe["status_raw"], "success")
        self.assertEqual(exe["verdict"], "partial_pass")
        self.assertEqual(exe["passed"] + exe["failed"], exe["total"])

    def test_iter004_updater_absent(self):
        view4 = next(v for v in self.view["iterations"] if v["n"] == 4)
        self.assertIsNone(view4["constraint_update"])
        self.assertFalse(view4["exists"]["constraint_update.json"])

    def test_run_view_json_serializable(self):
        """RunView 必须能被 server 的 json.dumps(default=str) 序列化。"""
        import json as _json
        from workbench.adapters import replay as replay_adapter
        from workbench import jsonutil as _jsonutil

        raw = _jsonutil.read_json(paths.resolve_run(ROOT, SAMPLE) / "run_state.json")
        events = replay_adapter.build_replay(raw, self.view["iterations"], self.view["inputs_files"])
        payload = _json.dumps({"ok": True, "data": {"view": self.view, "replay": events}},
                              ensure_ascii=False, default=str)
        self.assertGreater(len(payload), 1000)
        self.assertTrue(any(e["action"] == "terminal" for e in events))
        # iter_004 无 constraint_update.json（仅扩量）→ 本轮无更新实例，不生成节点事件；
        # 更新事件只出现在有 constraint_update.json 的轮（2/3/5/6），均为 passed
        updater_rounds = sorted({e["iteration"] for e in events if e["agent"] == "constraint-updater"})
        self.assertEqual(updater_rounds, [2, 3, 5, 6])
        self.assertTrue(all(e["action"] == "passed"
                            for e in events if e["agent"] == "constraint-updater"))
        # 因"未见产物"而待确认的角色（no_instance）不生成节点事件
        self.assertFalse(any(e["action"] == "unconfirmed" and e["iteration"] == 4
                             and e["agent"] in ("constraint-updater", "constraint-supplementer",
                                                "prompt-optimizer", "scene-scanner")
                             for e in events))
        # 明确交接才带 to_agent；场景扫描 → 提取器的业务流箭头应存在
        self.assertTrue(any(e["agent"] == "case-generator" and e.get("to_agent") == "case-executor"
                            for e in events))

    def test_artifact_whitelist_and_escape(self):
        run_root = paths.resolve_run(ROOT, SAMPLE)
        out = artifacts.get_artifact(run_root, "iter_001/quality_gate.json")
        self.assertFalse(out["truncated"])
        big = artifacts.get_artifact(run_root, "iter_004/execution_result.json")
        self.assertTrue(big["truncated"])  # 2.65MB > 2MB 阈值
        with self.assertRaises((paths.PathEscapeError, artifacts.ArtifactRejected)):
            artifacts.get_artifact(run_root, "../../CLAUDE.md")


if __name__ == "__main__":
    unittest.main()
