"""scripts/validate_artifacts.py 纯函数单元测试（标准库路径，无需 torch/z3）。

constraints 语义校验（validate_constraints）依赖 agent.generators 包（torch），
在无完整依赖环境下跳过——见 test_validate_constraints_requires_torch_guard。
"""

import json

import pytest

from scripts import validate_artifacts as va


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


def test_executor_clean(tmp_path):
    target = tmp_path / "cases_executor.py"
    target.write_text("def __call__(self):\n    return out\n", encoding="utf-8")
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
