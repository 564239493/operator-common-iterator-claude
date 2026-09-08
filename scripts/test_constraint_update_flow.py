#!/usr/bin/env python3
"""Regression tests for all-cluster routing and direct constraint updates."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from constraint_update_state import finalize, prepare
from validate_artifacts import validate_analysis, validate_constraint_update


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _constraint_analysis() -> dict:
    return {
        "schema_version": "2.1",
        "root_cause": "constraint_extraction",
        "analysis": "axis 的支持范围提取过宽。",
        "specific_issues": ["case_007 使用 axis=2 后执行失败。"],
        "failure_clusters": [{
            "id": "FC-001",
            "signature": "invalid axis value",
            "case_ids": ["case_007"],
            "root_cause": "constraint_extraction",
            "recommended_action": "UPDATE_CONSTRAINTS",
            "evidence": [{"source": "execution_result", "detail": "axis=2 is unsupported"}],
        }],
        "constraint_findings": [{
            "id": "CF-001",
            "kind": "too_broad",
            "fact": "axis 仅支持 0 或 1。",
            "suggested_change": "将 axis 的值域从连续范围改为枚举 [0, 1]。",
            "affected_params": ["axis"],
            "case_ids": ["case_007"],
            "cluster_ids": ["FC-001"],
            "evidence": [{"source": "operator_doc", "detail": "axis 取值为 0 或 1"}],
            "confidence": 0.95,
            "expected_effect": "生成器不再生成 axis=2 的用例。",
        }],
        # Findings may describe a correction to an existing rule; they do not
        # require a separate supplement_additions.md file in schema 2.1.
        "supplement_decision": {
            "has_explicit_additions": False,
            "source": "none",
            "reason": "修正现有约束，不新增补充文档。",
        },
        "prompt_optimization": {"eligible": False, "reason": "在线直接更新约束。"},
        "root_cause_summary": {
            "constraint_extraction": {"clusters": 1, "cases": 1},
            "generator_bug": {"clusters": 0, "cases": 0},
            "executor_bug": {"clusters": 0, "cases": 0},
        },
        "overall_action": "UPDATE_CONSTRAINTS",
    }


def _constraints() -> dict:
    return {
        "operator_name": "aclnnExample",
        "function_explanation": "test operator",
        "product_support": ["Atlas A"],
        "function_signature": "aclnnExample(...) -> int",
        "inputs": {},
        "outputs": {},
        "constraints_in_parameters": {},
    }


class AnalysisRoutingTests(unittest.TestCase):
    def test_constraint_findings_do_not_require_supplement_file(self) -> None:
        self.assertEqual(validate_analysis(_constraint_analysis()), [])

    def test_mixed_clusters_cannot_route_as_constraint_update(self) -> None:
        value = _constraint_analysis()
        value["failure_clusters"].append({
            "id": "FC-002",
            "signature": "executor expansion error",
            "case_ids": ["case_009"],
            "root_cause": "executor_bug",
            "recommended_action": "STOP_EXECUTOR_BUG",
            "evidence": [{"source": "traceback", "detail": "expanded argument count mismatch"}],
        })
        value["root_cause"] = "executor_bug"
        value["root_cause_summary"]["executor_bug"] = {"clusters": 1, "cases": 1}
        value["overall_action"] = "UPDATE_CONSTRAINTS"
        errors = validate_analysis(value)
        self.assertTrue(any("MIXED_FAILURE_REVIEW" in error for error in errors), errors)

    def test_summary_must_match_all_clusters(self) -> None:
        value = _constraint_analysis()
        value["root_cause_summary"]["constraint_extraction"]["cases"] = 0
        errors = validate_analysis(value)
        self.assertTrue(any("root_cause_summary" in error for error in errors), errors)


class ConstraintUpdateTests(unittest.TestCase):
    def _prepare(self, root: Path) -> tuple[Path, Path]:
        source = root / "iter_001" / "constraints.json"
        analysis = root / "iter_001" / "analysis.json"
        execution = root / "iter_001" / "execution_result.json"
        target = root / "iter_002" / "constraints.json"
        _write_json(source, _constraints())
        _write_json(analysis, _constraint_analysis())
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        _write_json(execution, {
            "status": "failed",
            "mode": "mock",
            "passed": 0,
            "failed": 1,
            "total": 1,
            "records": [{"case_id": "case_007", "status": "FAIL"}],
            "engine_error": "",
            "input_artifacts": {
                "constraints": {
                    "path": str(source.resolve()),
                    "sha256": digest,
                    "size": source.stat().st_size,
                    "mtime_ns": source.stat().st_mtime_ns,
                }
            },
        })
        prepare(source, target, analysis, execution)
        return target, target.parent / "constraint_update.json"

    def test_prepare_and_finalize_versioned_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target, report_path = self._prepare(Path(temporary))
            constraints = json.loads(target.read_text(encoding="utf-8"))
            constraints["product_support"].append("Atlas B")
            _write_json(target, constraints)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["changes"] = [{
                "id": "CU-001",
                "finding_ids": ["CF-001"],
                "op": "update_product_support",
                "target": "product_support",
                "before": ["Atlas A"],
                "after": ["Atlas A", "Atlas B"],
                "basis": "CF-001 的文档与失败证据",
                "expected_effect": "覆盖 CF-001 并由 checker 复核。",
            }]
            _write_json(report_path, report)
            result = finalize(report_path)
            self.assertTrue(result["finalized"])
            finalized = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(finalized["status"], "updated")
            self.assertEqual(validate_constraint_update(finalized), [])
            self.assertTrue(target.with_name("constraints.json.pre_update").is_file())

    def test_finalize_rejects_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, report_path = self._prepare(Path(temporary))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["changes"] = [{
                "id": "CU-001",
                "finding_ids": ["CF-001"],
                "op": "set_parameter_field",
                "target": "inputs.axis.allowed_range_value",
                "before": {"type": "range", "value": [0, 7]},
                "after": {"type": "enum", "value": [0, 1]},
                "basis": "CF-001",
                "expected_effect": "不再生成 axis=2。",
            }]
            _write_json(report_path, report)
            with self.assertRaisesRegex(ValueError, "noop"):
                finalize(report_path)

    def test_prepare_rejects_unconsumed_constraint_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "iter_001" / "constraints.json"
            other = root / "iter_001" / "other_constraints.json"
            analysis = root / "iter_001" / "analysis.json"
            execution = root / "iter_001" / "execution_result.json"
            _write_json(source, _constraints())
            _write_json(other, _constraints())
            _write_json(analysis, _constraint_analysis())
            digest = hashlib.sha256(other.read_bytes()).hexdigest()
            _write_json(execution, {
                "status": "failed", "mode": "mock", "passed": 0, "failed": 1,
                "total": 1, "records": [], "engine_error": "",
                "input_artifacts": {"constraints": {
                    "path": str(other.resolve()), "sha256": digest,
                    "size": other.stat().st_size, "mtime_ns": other.stat().st_mtime_ns,
                }},
            })
            with self.assertRaisesRegex(ValueError, "consumed by execution"):
                prepare(source, root / "iter_002" / "constraints.json", analysis, execution)


if __name__ == "__main__":
    unittest.main()
