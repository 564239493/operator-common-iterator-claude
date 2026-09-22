#!/usr/bin/env python3
"""Unit + integration tests for scripts/run_state.py (run_state.json 唯一写入器).

断言基准：每条 CLI 命令 / 库函数写出的 run_state.json 与命令契约声明的
字段完全一致——声明的字段按预期变化，未声明字段一律不变（含 updated_at
是否刷新、尾换行是否保持）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from run_state import save_run_state, write_initial

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "run_state.py"


def run_script(script: str, *argv: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / script), *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    payload: dict = {}
    if proc.stdout and proc.stdout.strip():
        payload = json.loads(proc.stdout)
    return proc.returncode, payload


def run_cli(*argv: str) -> tuple[int, dict]:
    return run_script("run_state.py", *argv)


def initial_state() -> dict:
    """init_run.py 产物的代表性子集（覆盖全部被测字段 + 若干旁观字段）。"""
    return {
        "run_id": "aclnnFoo-20260909",
        "operator_doc_source": "D:/docs/aclnnFoo.md",
        "operator_doc": "D:/repo/runs/aclnnFoo-20260909/inputs/aclnnFoo.md",
        "current_prompt_modules": ["common/documentation_conventions"],
        "supplement_revision": 0,
        "supplement_hash": "",
        "last_consumed_supplement_hash": "",
        "supplement_updated_iteration": 0,
        "operator_src_snapshot": "",
        "mode": "real",
        "max_iterations": 3,
        "constraint_check": {
            "max_rounds": 3,
            "iteration": 0,
            "current_round": 0,
            "status": "pending",
            "report": "",
        },
        "case_count": 10,
        "operator_family": "aclnn",
        "test_framework": "atk",
        "run_scope": "full",
        "scene": None,
        "execution_strategy": None,
        "operator_category": None,
        "operator_category_evidence": [],
        "current_iteration": 1,
        "state": "PLAN",
        "history": [{"state": "PLAN", "at": "2026-09-09T00:00:00+00:00"}],
        "created_at": "2026-09-09T00:00:00+00:00",
        "updated_at": "2026-09-09T00:00:00+00:00",
    }


class RunStateCliTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.path = self.run_dir / "run_state.json"
        self.addCleanup(self._tmp.cleanup)

    def write_state(self, state: dict, trailing_newline: bool = False) -> None:
        save_run_state(self.path, state, trailing_newline=trailing_newline)

    def read_state(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def raw_bytes(self) -> bytes:
        return self.path.read_bytes()


class TestLibrary(RunStateCliTestBase):
    def test_write_initial_no_trailing_newline(self) -> None:
        write_initial(self.run_dir, initial_state())
        raw = self.raw_bytes()
        self.assertFalse(raw.endswith(b"\n"))  # 与 init_run.py 历史落盘一致
        self.assertEqual(json.loads(raw), initial_state())
        # 字节级断言：与 json.dumps(..., ensure_ascii=False, indent=2)
        # 的 write_text 落盘结果一致（含换行翻译 \n → os.linesep）
        expected = json.dumps(
            initial_state(), ensure_ascii=False, indent=2
        ).encode("utf-8").replace(b"\n", os.linesep.encode())
        self.assertEqual(raw, expected)

    def test_save_run_state_trailing_flag(self) -> None:
        state = initial_state()
        save_run_state(self.path, state, trailing_newline=False)
        self.assertFalse(self.raw_bytes().endswith(b"\n"))
        save_run_state(self.path, state, trailing_newline=True)
        self.assertTrue(self.raw_bytes().endswith(b"\n"))

    def test_write_initial_rejects_unknown_state(self) -> None:
        state = initial_state()
        state["state"] = "SUCESS"
        with self.assertRaises(ValueError):
            write_initial(self.run_dir, state)


class TestSetState(RunStateCliTestBase):
    def test_advance_to_extract(self) -> None:
        """推进 EXTRACT：写 state 并 append history {"state","at"}；其余字段不动。"""
        self.write_state(initial_state())
        code, payload = run_cli("set-state", "--run-dir", str(self.run_dir), "--to", "EXTRACT")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        state = self.read_state()
        self.assertEqual(state["state"], "EXTRACT")
        self.assertEqual(len(state["history"]), 2)
        entry = state["history"][-1]
        self.assertEqual(set(entry), {"state", "at"})
        self.assertEqual(entry["state"], "EXTRACT")
        # set-state 不刷新 updated_at
        self.assertEqual(state["updated_at"], "2026-09-09T00:00:00+00:00")
        # 其他字段全不变
        expected = initial_state()
        expected["state"] = "EXTRACT"
        expected["history"] = expected["history"] + [entry]
        self.assertEqual(state, expected)

    def test_blocked_with_code(self) -> None:
        self.write_state(initial_state())
        code, _ = run_cli(
            "set-state", "--run-dir", str(self.run_dir),
            "--to", "BLOCKED", "--code", "CONSTRAINT_CHECK_FAILED",
            "--force",  # 本用例验证 code 记账机制，显式绕过 flow 边校验
        )
        self.assertEqual(code, 0)
        entry = self.read_state()["history"][-1]
        self.assertEqual(set(entry), {"state", "code", "at"})
        self.assertEqual(entry["code"], "CONSTRAINT_CHECK_FAILED")

    def test_success_with_event(self) -> None:
        self.write_state(initial_state())
        code, _ = run_cli(
            "set-state", "--run-dir", str(self.run_dir),
            "--to", "SUCCESS", "--event", "CONSTRAINTS_ONLY_SUCCESS",
            "--force",  # 本用例验证 event 记账机制，显式绕过 flow 边校验
        )
        self.assertEqual(code, 0)
        state = self.read_state()
        self.assertEqual(state["state"], "SUCCESS")
        entry = state["history"][-1]
        self.assertEqual(set(entry), {"state", "event", "at"})
        self.assertEqual(entry["event"], "CONSTRAINTS_ONLY_SUCCESS")

    def test_update_constraints_bumps_iteration(self) -> None:
        self.write_state(initial_state())
        code, payload = run_cli(
            "set-state", "--run-dir", str(self.run_dir),
            "--to", "UPDATE_CONSTRAINTS", "--bump-iteration",
            "--force",  # 本用例验证 bump 记账机制，显式绕过 flow 边校验
        )
        self.assertEqual(code, 0)
        state = self.read_state()
        self.assertEqual(state["current_iteration"], 2)
        self.assertEqual(state["state"], "UPDATE_CONSTRAINTS")
        self.assertEqual(state["history"][-1]["state"], "UPDATE_CONSTRAINTS")
        self.assertEqual(payload["current_iteration"], 2)

    def test_invalid_state_rejected_file_unchanged(self) -> None:
        self.write_state(initial_state())
        before = self.raw_bytes()
        code, payload = run_cli(
            "set-state", "--run-dir", str(self.run_dir), "--to", "SUCESS"
        )
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(self.raw_bytes(), before)

    def test_trailing_newline_preserved(self) -> None:
        for trailing in (False, True):
            with self.subTest(trailing_newline=trailing):
                self.write_state(initial_state(), trailing_newline=trailing)
                code, _ = run_cli(
                    "set-state", "--run-dir", str(self.run_dir), "--to", "EXTRACT"
                )
                self.assertEqual(code, 0)
                self.assertEqual(self.raw_bytes().endswith(b"\n"), trailing)


class TestSetFields(RunStateCliTestBase):
    def test_classify_backfill_fields(self) -> None:
        """分类回写：一次写 3 个字段；updated_at 与其他字段不动。"""
        self.write_state(initial_state())
        evidence = '[{"rule":"R1","quote":"..."}]'
        code, payload = run_cli(
            "set-fields", "--run-dir", str(self.run_dir),
            "--set", "execution_strategy=fusion",
            "--set", "operator_category=fusion_comm_compute",
            "--set", f"operator_category_evidence={evidence}",
        )
        self.assertEqual(code, 0)
        state = self.read_state()
        self.assertEqual(state["execution_strategy"], "fusion")
        self.assertEqual(state["operator_category"], "fusion_comm_compute")
        self.assertEqual(state["operator_category_evidence"], [{"rule": "R1", "quote": "..."}])
        self.assertEqual(state["updated_at"], "2026-09-09T00:00:00+00:00")
        self.assertEqual(len(state["history"]), 1)

    def test_windows_path_value_raw_string_fallback(self) -> None:
        """Windows 反斜杠绝对路径按原始字符串写入（JSON 解析失败的回退）。"""
        self.write_state(initial_state())
        code, _ = run_cli(
            "set-fields", "--run-dir", str(self.run_dir),
            "--set", r"operator_src_snapshot=D:\snap\aclnnFoo",
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            self.read_state()["operator_src_snapshot"], r"D:\snap\aclnnFoo"
        )

    def test_unknown_key_rejected(self) -> None:
        self.write_state(initial_state())
        before = self.raw_bytes()
        code, payload = run_cli(
            "set-fields", "--run-dir", str(self.run_dir),
            "--set", "execution_strategie=fusion",
        )
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(self.raw_bytes(), before)

    def test_protected_key_rejected(self) -> None:
        self.write_state(initial_state())
        before = self.raw_bytes()
        for key in ("state", "history", "updated_at"):
            code, _ = run_cli(
                "set-fields", "--run-dir", str(self.run_dir),
                "--set", f"{key}=x",
            )
            self.assertEqual(code, 2)
        self.assertEqual(self.raw_bytes(), before)


class TestSetConstraintCheck(RunStateCliTestBase):
    def test_reset_preserves_max_rounds(self) -> None:
        """--reset：保留 max_rounds、重置其余字段、report 指向当前轮、刷新 updated_at。"""
        state = initial_state()
        state["constraint_check"]["max_rounds"] = 5
        state["constraint_check"]["status"] = "passed"
        self.write_state(state)
        code, payload = run_cli(
            "set-constraint-check", "--run-dir", str(self.run_dir), "--reset"
        )
        self.assertEqual(code, 0)
        after = self.read_state()
        self.assertEqual(after["constraint_check"], {
            "max_rounds": 5,
            "iteration": 1,
            "current_round": 0,
            "status": "pending",
            "report": "iter_001/constraint_check.json",
        })
        self.assertNotEqual(after["updated_at"], "2026-09-09T00:00:00+00:00")
        self.assertEqual(payload["constraint_check"]["max_rounds"], 5)

    def test_reset_defaults_max_rounds_3_when_missing(self) -> None:
        state = initial_state()
        del state["constraint_check"]
        self.write_state(state)
        code, _ = run_cli(
            "set-constraint-check", "--run-dir", str(self.run_dir), "--reset"
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.read_state()["constraint_check"]["max_rounds"], 3)

    def test_round_status_backfill(self) -> None:
        self.write_state(initial_state())
        code, _ = run_cli(
            "set-constraint-check", "--run-dir", str(self.run_dir),
            "--current-round", "2", "--status", "passed",
        )
        self.assertEqual(code, 0)
        check = self.read_state()["constraint_check"]
        self.assertEqual(check["current_round"], 2)
        self.assertEqual(check["status"], "passed")
        # 未给出的字段不动
        self.assertEqual(check["iteration"], 0)
        self.assertEqual(check["report"], "")

    def test_recheck_pending(self) -> None:
        self.write_state(initial_state())
        code, _ = run_cli(
            "set-constraint-check", "--run-dir", str(self.run_dir),
            "--status", "recheck_pending",
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            self.read_state()["constraint_check"]["status"], "recheck_pending"
        )

    def test_passed_invalidation_reset(self) -> None:
        state = initial_state()
        state["constraint_check"]["current_round"] = 2
        state["constraint_check"]["status"] = "passed"
        self.write_state(state)
        code, _ = run_cli(
            "set-constraint-check", "--run-dir", str(self.run_dir),
            "--current-round", "0", "--status", "pending",
        )
        self.assertEqual(code, 0)
        check = self.read_state()["constraint_check"]
        self.assertEqual(check["current_round"], 0)
        self.assertEqual(check["status"], "pending")

    def test_invalid_status_rejected_file_unchanged(self) -> None:
        self.write_state(initial_state())
        before = self.raw_bytes()
        code, payload = run_cli(
            "set-constraint-check", "--run-dir", str(self.run_dir),
            "--status", "pasSED",
        )
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(self.raw_bytes(), before)

    def test_reset_conflicts_with_status_and_round(self) -> None:
        self.write_state(initial_state())
        before = self.raw_bytes()
        code, _ = run_cli(
            "set-constraint-check", "--run-dir", str(self.run_dir),
            "--reset", "--status", "passed",
        )
        self.assertEqual(code, 2)
        self.assertEqual(self.raw_bytes(), before)

    def test_set_fields_constraint_check_status_validated(self) -> None:
        self.write_state(initial_state())
        before = self.raw_bytes()
        code, _ = run_cli(
            "set-fields", "--run-dir", str(self.run_dir),
            "--set", "constraint_check.status=bad_value",
        )
        self.assertEqual(code, 2)
        self.assertEqual(self.raw_bytes(), before)


class TestDelegatedScripts(unittest.TestCase):
    """落盘委托脚本集成测试（render_scene_directive / update_supplement_state）：字段、stdout、尾换行格式。"""

    def test_update_supplement_state_delegation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            path = run_dir / "run_state.json"
            save_run_state(path, initial_state(), trailing_newline=True)
            supp = run_dir / "supplementary-doc.md"
            supp.write_text("fact: axis in {0, 1}", encoding="utf-8")
            code, payload = run_script(
                "update_supplement_state.py", str(path),
                "--supplementary", str(supp), "--iteration", "1",
            )
            self.assertEqual(code, 0)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["changed"])
            after = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(after["supplement_revision"], 1)
            self.assertEqual(after["supplement_hash"], payload["supplement_hash"])
            self.assertEqual(after["supplement_updated_iteration"], 1)
            self.assertEqual(after["last_consumed_supplement_hash"], "")
            # update_supplement_state 落盘带尾换行
            self.assertTrue(path.read_bytes().endswith(b"\n"))
            # --consume 对齐已消费 hash
            code, payload = run_script(
                "update_supplement_state.py", str(path),
                "--supplementary", str(supp), "--iteration", "1", "--consume",
            )
            self.assertEqual(code, 0)
            self.assertFalse(payload["changed"])
            after = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                after["last_consumed_supplement_hash"], after["supplement_hash"]
            )

    def test_render_scene_directive_scope_off_delegation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            inputs = run_dir / "inputs"
            inputs.mkdir()
            scan = inputs / "scene_scan.json"
            scan.write_text(
                json.dumps({"device_types": [], "devices": []}), encoding="utf-8"
            )
            save_run_state(run_dir / "run_state.json", initial_state(),
                           trailing_newline=False)
            code, payload = run_script(
                "render_scene_directive.py",
                "--scan", str(scan), "--run-dir", str(run_dir), "--scope", "off",
            )
            self.assertEqual(code, 0)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["scope"], "off")
            path = run_dir / "run_state.json"
            after = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(after["scene"]["enabled"], False)
            self.assertEqual(after["scene"]["scope"], "off")
            self.assertEqual(after["scene"]["scan"], str(scan))
            self.assertNotEqual(after["updated_at"], "2026-09-09T00:00:00+00:00")
            # render_scene_directive 落盘无尾换行
            self.assertFalse(path.read_bytes().endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
