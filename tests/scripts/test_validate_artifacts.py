"""scripts/validate_artifacts.py 纯函数单元测试（标准库路径，无需 torch/z3）。

constraints 语义校验（validate_constraints）依赖 agent.generators 包（torch），
在无完整依赖环境下跳过——见 test_validate_constraints_requires_torch_guard。
"""

import json
import ast
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import validate_artifacts as va
from scripts.atk_to_ttk_aclnn import audit_case, convert_case


ROOT = Path(__file__).resolve().parents[2]


def test_collection_prompt_proposal_decision_and_application(tmp_path):
    run_dir = tmp_path / "run"
    inputs = run_dir / "inputs"
    inputs.mkdir(parents=True)
    proposal_path = inputs / "prompt_update_proposal.json"
    decisions_path = inputs / "prompt_update_decisions.json"
    proposal_path.write_text(json.dumps({
        "run_id": "collection-test",
        "proposals": [{
            "id": "p1",
            "destination": "base_prompt",
            "canonical_target": "prompts/operator_constraints/base.md §test",
            "change_summary": "test collection proposal",
            "status": "deferred",
        }],
    }), encoding="utf-8")
    decisions_path.write_text(
        json.dumps({"schema_version": "1.0", "decisions": []}),
        encoding="utf-8",
    )

    decision = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "record_prompt_update_decision.py"),
        "--proposal", str(proposal_path), "--proposal-id", "p1",
        "--decisions", str(decisions_path), "--decision", "approve",
        "--confirmed-by-user",
    ], cwd=ROOT, capture_output=True, text=True, check=False)
    assert decision.returncode == 0, decision.stderr or decision.stdout

    application = subprocess.run([
        sys.executable, str(ROOT / "scripts" / "record_prompt_update_application.py"),
        "--proposal", str(proposal_path), "--proposal-id", "p1",
        "--decisions", str(decisions_path), "--validation", "unit-test:passed",
    ], cwd=ROOT, capture_output=True, text=True, check=False)
    assert application.returncode == 0, application.stderr or application.stdout

    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    decision_log = json.loads(decisions_path.read_text(encoding="utf-8"))
    assert proposal["proposals"][0]["status"] == "applied"
    assert decision_log["decisions"][0]["proposal_id"] == "p1"
    assert decision_log["decisions"][0]["canonical_applied"] is True


def test_atk_to_ttk_tensor_list_metadata_repeated_to_length():
    signature = [
        {"name": "x", "tensor_list": True, "output": False, "optional": False},
        {"name": "y", "tensor_list": False, "output": False, "optional": False},
    ]
    case = {
        "id": 0,
        "name": "aclnnExample",
        "inputs": [
            {
                "name": "x", "type": "tensors", "shape": [2, 3],
                "dtype": "float16", "format": "nz", "length": 3,
            },
            {
                "name": "y", "type": "tensor", "shape": [1],
                "dtype": "int32", "format": "nd",
            },
        ],
    }

    row = convert_case(case, 0, signature=signature, attr_names=[])

    assert ast.literal_eval(row["tensor_view_shapes"]) == (
        ((2, 3), (2, 3), (2, 3)), (1,),
    )
    assert ast.literal_eval(row["tensor_dtypes"]) == (
        ("float16", "float16", "float16"), "int32",
    )
    assert ast.literal_eval(row["tensor_formats"]) == (
        ("NZ", "NZ", "NZ"), "ND",
    )
    assert audit_case(case, signature, row, []) == []


# ── load ─────────────────────────────────────────────────────────────────────


def test_load_json(tmp_path):
    target = tmp_path / "data.json"
    target.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert va.load(str(target)) == {"a": 1}


# ── 辅助迭代器 ────────────────────────────────────────────────────────────────


def test_iter_param_attributes_flat():
    value = {
        "inputs": {"x": {"all": {"dtype": "float32"}}},
        "outputs": {"y": {"all": {"dtype": "float32"}}},
    }
    got = list(va._iter_param_attributes(value))
    assert len(got) == 2
    assert got[0][0] == "inputs" and got[0][1] == "x" and got[0][2] == "all"


def test_iter_param_attributes_skips_non_dict():
    value = {"inputs": {"x": "not-dict", "y": [1, 2]}}
    assert list(va._iter_param_attributes(value)) == []


def test_iter_constraints_list_form():
    value = {"constraints_in_parameters": [{"expr": "a > 0"}, {"expr": "b < 2"}]}
    got = list(va._iter_constraints(value))
    assert [g[0] for g in got] == ["common", "common"]
    assert got[0][1] == 0 and got[1][1] == 1


def test_iter_constraints_platform_dict_form():
    value = {"constraints_in_parameters": {"atlas_a3": [{"expr": "a > 0"}]}}
    got = list(va._iter_constraints(value))
    assert got[0][0] == "atlas_a3" and got[0][2]["expr"] == "a > 0"


# ── AST 语义：嵌套数值区间成员判断 ────────────────────────────────────────────


def test_nested_interval_detected():
    node = va._is_nested_numeric_interval_membership(
        __import__("ast").parse("x in [[0, 10], [20, 30]]", mode="eval")
    )
    assert node is True


def test_nested_interval_with_null_detected():
    node = va._is_nested_numeric_interval_membership(
        __import__("ast").parse("x in [[0, null], [5, 10]]", mode="eval")
    )
    assert node is True


def test_flat_interval_not_detected():
    node = va._is_nested_numeric_interval_membership(
        __import__("ast").parse("x in [0, 10]", mode="eval")
    )
    assert node is False


def test_non_interval_expr_not_detected():
    node = va._is_nested_numeric_interval_membership(
        __import__("ast").parse("len(x.shape) == 2", mode="eval")
    )
    assert node is False


# ── validate_execution ────────────────────────────────────────────────────────


def test_execution_valid():
    value = {
        "status": "completed", "mode": "real", "passed": 8, "failed": 2,
        "total": 10, "records": [], "engine_error": None,
    }
    assert va.validate_execution(value) == []


def test_execution_passed_failed_mismatch():
    value = {
        "status": "completed", "mode": "real", "passed": 8, "failed": 1,
        "total": 10, "records": [], "engine_error": None,
    }
    errors = va.validate_execution(value)
    assert any("passed + failed" in e for e in errors)


def test_execution_missing_fields():
    errors = va.validate_execution({"passed": 0})
    assert any("missing field: total" in e for e in errors)


def test_execution_fusion_requires_phases():
    value = {
        "status": "completed", "mode": "fusion", "passed": 3, "failed": 0,
        "total": 3, "records": [], "engine_error": None,
        "execution_strategy": "fusion", "fusion_phases": [],
    }
    errors = va.validate_execution(value)
    assert any("fusion_phases 必须是非空数组" in e for e in errors)


def test_execution_fusion_phase_dir_check():
    value = {
        "status": "completed", "mode": "fusion", "passed": 3, "failed": 0,
        "total": 3, "records": [], "engine_error": None,
        "execution_strategy": "fusion",
        "fusion_phases": [
            {"phase": "cpu_benchmark", "dir_check_passed": False},
            {"phase": "npu_cascaded", "dir_check_passed": True},
        ],
    }
    errors = va.validate_execution(value)
    assert any("cpu_benchmark dir_check_passed" in e for e in errors)


# ── validate_analysis ─────────────────────────────────────────────────────────


def test_analysis_root_cause_allowed():
    for cause in ("constraint_extraction", "generator_bug", "executor_bug"):
        assert va.validate_analysis({"root_cause": cause}) == []


def test_analysis_root_cause_invalid():
    errors = va.validate_analysis({"root_cause": "unknown"})
    assert any("root_cause" in e for e in errors)


# ── validate_cases ────────────────────────────────────────────────────────────


def test_cases_valid():
    assert va.validate_cases([{"id": 1}]) == []


def test_cases_empty():
    assert any("not be empty" in e for e in va.validate_cases([]))


def test_cases_non_list():
    assert va.validate_cases({"not": "array"}) == ["cases must be an array"]


# ── validate_executor（dummy 标记检测） ────────────────────────────────────────


def test_executor_with_dummy_marker(tmp_path):
    target = tmp_path / "cases_executor.py"
    target.write_text(
        "def _dummy_output():\n    return torch.ones([1024, 1, 16])\n",
        encoding="utf-8",
    )
    errors = va.validate_executor(str(target))
    assert errors and "dummy" in errors[0]


def test_executor_with_end_golden_marker(tmp_path):
    target = tmp_path / "cases_executor.py"
    target.write_text(
        "def __call__(self):\n    return out\n# END_CPU_GOLDEN\n",
        encoding="utf-8",
    )
    errors = va.validate_executor(str(target))
    assert errors and "END_CPU_GOLDEN" in errors[0]


def test_executor_clean(tmp_path):
    target = tmp_path / "cases_executor.py"
    target.write_text("def __call__(self):\n    return out\n", encoding="utf-8")
    assert va.validate_executor(str(target)) == []


def test_executor_rejects_incomplete_generated_binding(tmp_path):
    target = tmp_path / "cases_executor.py"
    target.write_text(
        "class Function:\n"
        "    _REQUIRED_TENSOR_NAMES = ['x']\n"
        "    def __call__(self, input_data):\n"
        "        return input_data.args[0]\n",
        encoding="utf-8",
    )
    errors = va.validate_executor(str(target))
    assert any("通用入参绑定不完整" in error for error in errors)


def test_executor_accepts_generated_kwargs_args_binding(tmp_path):
    target = tmp_path / "cases_executor.py"
    target.write_text(
        "class Function:\n"
        "    _REQUIRED_TENSOR_NAMES = ['x']\n"
        "    def __call__(self, input_data):\n"
        "        kwargs = getattr(input_data, \"kwargs\", None) or {}\n"
        "        args = getattr(input_data, \"args\", None) or []\n"
        "        if not kwargs and not args:\n"
        "            raise TypeError('missing required tensor inputs')\n"
        "        return kwargs.get('x', args[0] if args else None)\n",
        encoding="utf-8",
    )
    assert va.validate_executor(str(target)) == []


# ── validate_scene_scan ───────────────────────────────────────────────────────


def test_scene_scan_non_quant_ok():
    errors, warnings = va.validate_scene_scan({"has_quant_scenarios": False})
    assert errors == [] and warnings == []


def test_scene_scan_requires_bool():
    errors, _ = va.validate_scene_scan({"has_quant_scenarios": "yes"})
    assert any("must be bool" in e for e in errors)


def test_scene_scan_quant_requires_modes():
    errors, _ = va.validate_scene_scan({"has_quant_scenarios": True})
    assert any("quant_modes" in e for e in errors)


def test_scene_scan_valid_combo():
    value = {
        "has_quant_scenarios": True,
        "quant_modes": ["per_tensor"],
        "quant_widths_by_mode": {"per_tensor": ["int8", "fp16"]},
        "valid_combos": [{"mode": "per_tensor", "width": "int8"}],
        "evidence": [{"mode": "per_tensor", "width": "int8", "src_text": "doc:12"}],
    }
    errors, warnings = va.validate_scene_scan(value)
    assert errors == []


def test_scene_scan_combo_width_not_listed():
    value = {
        "has_quant_scenarios": True,
        "quant_modes": ["per_tensor"],
        "quant_widths_by_mode": {"per_tensor": ["int8"]},
        "valid_combos": [{"mode": "per_tensor", "width": "fp16"}],
        "evidence": [],
    }
    errors, _ = va.validate_scene_scan(value)
    assert any("not in quant_widths_by_mode" in e for e in errors)


def test_scene_scan_false_with_modes_warns():
    _, warnings = va.validate_scene_scan(
        {"has_quant_scenarios": False, "quant_modes": ["per_tensor"]}
    )
    assert any("non-empty" in w for w in warnings)
