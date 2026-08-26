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
from scripts.validate_ttk_aclnn_csv import _check_dtype


ROOT = Path(__file__).resolve().parents[2]


def _constraint_with_dimensions(values, src_text):
    return {
        "inputs": {
            "x": {
                "Atlas": {
                    "dimensions": {"value": values, "src_text": src_text},
                    "allowed_range_value": {"value": [], "src_text": ""},
                }
            }
        },
        "outputs": {},
        "constraints_in_parameters": {},
    }


def test_dimensions_continuous_range_must_be_expanded_as_rank_enum():
    errors = va.validate_constraint_semantics(
        _constraint_with_dimensions([1, 3], "支持1~3维")
    )

    assert any("must expand" in error and "[1, 2, 3]" in error for error in errors)


def test_dimensions_discrete_rank_enum_does_not_fill_missing_rank():
    errors = va.validate_constraint_semantics(
        _constraint_with_dimensions([1, 3], "只支持1维或3维")
    )

    assert errors == []


def test_dimensions_bracket_range_in_source_must_be_expanded():
    errors = va.validate_constraint_semantics(
        _constraint_with_dimensions([2, 6], "view shape 维度不在[2, 6]的范围")
    )

    assert any("must expand" in error and "[2, 3, 4, 5, 6]" in error for error in errors)


def test_dimensions_fixed_rank_is_singleton_enum():
    errors = va.validate_constraint_semantics(
        _constraint_with_dimensions([3, 3], "必须为3维")
    )

    assert any("sorted and deduplicated" in error and "[3]" in error for error in errors)


def test_constraint_semantics_rejects_attribute_on_tensor_list_subscript():
    value = {
        "inputs": {},
        "outputs": {},
        "constraints_in_parameters": {
            "Atlas": [{"expr": "x[0].shape[1] == weight[0].shape[1]"}]
        },
    }

    errors = va.validate_constraint_semantics(value)

    assert any("attribute root" in error and "P[0].shape" in error for error in errors)


def test_constraint_semantics_accepts_parameter_root_shape_subscript():
    value = {
        "inputs": {},
        "outputs": {},
        "constraints_in_parameters": {
            "Atlas": [{"expr": "x.shape[1] == weight.shape[1]"}]
        },
    }

    assert va.validate_constraint_semantics(value) == []


def _support_description_constraints(dtype_combo=None, format_combo=None):
    return {
        "inputs": {
            "x": {
                "Atlas": {
                    "dtype": {"value": ["INT8"], "src_text": ""},
                    "format": {"value": ["ND"], "src_text": ""},
                }
            },
            "weight": {
                "Atlas": {
                    "dtype": {"value": ["INT4"], "src_text": ""},
                    "format": {"value": ["ND", "NZ"], "src_text": ""},
                }
            },
            "offsetOptional": {
                "Atlas": {
                    "dtype": {"value": ["FLOAT"], "src_text": ""},
                    "format": {"value": ["ND"], "src_text": ""},
                }
            },
        },
        "outputs": {},
        "dtype_support_description": (
            {"Atlas": [dtype_combo]} if dtype_combo is not None else {}
        ),
        "format_support_description": (
            {"Atlas": [format_combo]} if format_combo is not None else {}
        ),
    }


def test_support_description_accepts_atomic_canonical_values():
    value = _support_description_constraints(
        dtype_combo={"x": "INT8", "weight": "INT4"},
        format_combo={"x": "ND", "weight": "NZ"},
    )

    assert va._validate_support_descriptions(value) == []


def test_support_description_rejects_merged_values_aliases_and_null_dtype():
    value = _support_description_constraints(
        dtype_combo={
            "offset": "FLOAT",
            "offsetOptional": "FLOAT/null",
            "weight": "INT4",
        },
        format_combo={"x": "ND", "weight": "ND/NZ"},
    )

    errors = va._validate_support_descriptions(value)

    assert any("unknown parameter 'offset'" in error for error in errors)
    assert any("encodes absence as dtype" in error for error in errors)
    assert any("merges multiple format values" in error for error in errors)
    assert any("outside format.value domain" in error for error in errors)


def _grouped_matmul_constraints(expressions):
    return {
        "operator_name": "aclnnGroupedMatmulV5",
        "inputs": {
            "weight": {
                "Atlas": {
                    "dtype": {"value": ["INT4"], "src_text": ""},
                    "format": {"value": ["ND", "NZ"], "src_text": ""},
                }
            }
        },
        "outputs": {},
        "constraints_in_parameters": {
            "Atlas": [{"expr": expr} for expr in expressions]
        },
    }


def test_grouped_matmul_validator_accepts_guarded_int4_n_alignment():
    value = _grouped_matmul_constraints([
        'not(weight.format == "NZ") or not(weight.dtype == "INT4") '
        'or weight.shape[2] % 16 == 0'
    ])

    assert va._validate_grouped_matmul_v5_constraints(value) == []


def test_grouped_matmul_validator_rejects_unguarded_n_and_unsupported_k_alignment():
    value = _grouped_matmul_constraints([
        "weight.shape[2] % 16 == 0",
        "weight.shape[1] % 64 == 0",
    ])

    errors = va._validate_grouped_matmul_v5_constraints(value)

    assert any("without the required NZ and INT4/INT8 guards" in error for error in errors)
    assert any("unsupported weight.shape[1] % 64" in error for error in errors)
    assert any("misses the trial-verified" in error for error in errors)


def test_ttk_aclnn_validator_accepts_native_int4():
    assert _check_dtype("int4") is None


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


@pytest.mark.parametrize("weight_format", ["ND", "NZ"])
def test_grouped_matmul_int4_weight_preserves_native_int4(weight_format):
    signature = [
        {"name": "x", "tensor_list": True, "output": False, "optional": False},
        {"name": "weight", "tensor_list": True, "output": False, "optional": False},
        {"name": "out", "tensor_list": True, "output": True, "optional": False},
    ]
    case = {
        "id": 0,
        "name": "aclnnGroupedMatmulV5",
        "outputs": "out",
        "inputs": [
            {
                "name": "x", "type": "tensors", "shape": [2, 16],
                "dtype": "int8", "format": "ND", "length": 1,
            },
            {
                "name": "weight", "type": "tensors", "shape": [1, 16, 30],
                "dtype": "int4", "format": weight_format, "length": 1,
                "range_values": [-10, 19],
            },
            {
                "name": "out", "type": "tensors", "shape": [2, 32],
                "dtype": "fp16", "format": "ND", "length": 1,
            },
        ],
    }

    row = convert_case(case, 0, signature=signature, attr_names=[])

    assert ast.literal_eval(row["tensor_view_shapes"]) == (
        ((2, 16),), ((1, 16, 30),), ((2, 32),),
    )
    assert ast.literal_eval(row["tensor_dtypes"]) == (
        ("int8",), ("int4",), ("float16",),
    )
    assert ast.literal_eval(row["input_data_ranges"])[1] == (-8, 7)
    assert audit_case(case, signature, row, []) == []


def test_int32_case_passes_through_without_repacking():
    signature = [
        {"name": "x", "tensor_list": True, "output": False, "optional": False},
        {"name": "weight", "tensor_list": True, "output": False, "optional": False},
    ]
    case = {
        "id": 0,
        "name": "aclnnGroupedMatmulV5",
        "inputs": [
            {
                "name": "x", "type": "tensors", "shape": [2, 16],
                "dtype": "int8", "format": "ND", "length": 1,
            },
            {
                "name": "weight", "type": "tensors", "shape": [16, 4],
                "dtype": "int32", "format": "ND", "length": 1,
            },
        ],
    }

    row = convert_case(case, 0, signature=signature, attr_names=[])

    assert ast.literal_eval(row["tensor_view_shapes"]) == (
        ((2, 16),), ((16, 4),),
    )
    assert ast.literal_eval(row["tensor_dtypes"]) == (("int8",), ("int32",))
    assert audit_case(case, signature, row, []) == []


def test_grouped_matmul_int4_multi_weight_preserves_each_tensor():
    signature = [
        {"name": "x", "tensor_list": True, "output": False, "optional": False},
        {"name": "weight", "tensor_list": True, "output": False, "optional": False},
        {"name": "out", "tensor_list": True, "output": True, "optional": False},
    ]
    case = {
        "id": 0,
        "name": "aclnnGroupedMatmulV5",
        "outputs": "out",
        "inputs": [
            {
                "name": "x", "type": "tensors", "shape": [2, 16],
                "dtype": "int8", "format": "ND", "length": 1,
            },
            {
                "name": "weight", "type": "tensors",
                "shape": [[16, 32], [16, 32]],
                "dtype": "int4", "format": "ND", "length": 2,
            },
            {
                "name": "out", "type": "tensors", "shape": [2, 32],
                "dtype": "fp16", "format": "ND", "length": 1,
            },
        ],
    }

    row = convert_case(case, 0, signature=signature, attr_names=[])

    assert ast.literal_eval(row["tensor_view_shapes"]) == (
        ((2, 16),), ((16, 32), (16, 32)), ((2, 32),),
    )
    assert ast.literal_eval(row["tensor_dtypes"]) == (
        ("int8",), ("int4", "int4"), ("float16",),
    )
    assert audit_case(case, signature, row, []) == []


def test_grouped_matmul_a8w4_without_offset_normalizes_group_list_type_to_count():
    case = {
        "id": 0,
        "name": "aclnnGroupedMatmulV5",
        "inputs": [
            {"name": "x", "type": "tensors", "dtype": "int8"},
            {"name": "weight", "type": "tensors", "dtype": "int4"},
            {"name": "groupListType", "type": "attr", "range_values": 0},
        ],
    }

    row = convert_case(case, 0, signature=[], attr_names=["groupListType"])

    assert ast.literal_eval(row["attributes"])["groupListType"] == 1


def test_grouped_matmul_a8w8_without_offset_preserves_type_two():
    case = {
        "id": 0,
        "name": "aclnnGroupedMatmulV5",
        "inputs": [
            {"name": "x", "type": "tensors", "dtype": "int8"},
            {"name": "weight", "type": "tensors", "dtype": "int8"},
            {"name": "groupListType", "type": "attr", "range_values": 2},
        ],
    }

    row = convert_case(case, 0, signature=[], attr_names=["groupListType"])

    assert ast.literal_eval(row["attributes"])["groupListType"] == 2


def test_grouped_matmul_with_offset_preserves_group_list_type():
    case = {
        "id": 0,
        "name": "aclnnGroupedMatmulV5",
        "inputs": [
            {
                "name": "offsetOptional", "type": "tensors", "shape": [1, 1, 8],
                "dtype": "fp32", "format": "ND", "length": 1,
            },
            {"name": "groupListType", "type": "attr", "range_values": 0},
        ],
    }

    row = convert_case(case, 0, signature=[], attr_names=["groupListType"])

    assert ast.literal_eval(row["attributes"])["groupListType"] == 0


def _invoke_grouped_matmul_input_hook(
    group, *, m: int, group_list_type: int, offset=None, tensor_dtypes=None,
) -> None:
    import torch

    from agent.hs.ttk_plugins.aclnn_grouped_matmul_v5_golden import (
        grouped_matmul_v5_execution_input,
    )

    grouped_matmul_v5_execution_input(
        [torch.empty((m, 8))],
        [torch.empty((int(group.shape[0]), 8, 8))],
        None,
        None,
        offset,
        None,
        None,
        None,
        group,
        None,
        None,
        None,
        2,
        0,
        group_list_type,
        0,
        None,
        tensor_dtypes=tensor_dtypes,
    )


def test_grouped_matmul_aclnn_input_hook_is_registered():
    from agent.hs.ttk_plugins.aclnn_grouped_matmul_v5_golden import __input__

    assert __input__["aclnn"]["aclnnGroupedMatmulV5"] == (
        "grouped_matmul_v5_execution_input"
    )


def test_grouped_matmul_mock_golden_mirrors_numpy_output():
    import numpy as np

    from agent.hs.ttk_plugins.aclnn_grouped_matmul_v5_golden import (
        grouped_matmul_v5_execution_golden,
    )

    expected_out = [np.empty((4, 65534), dtype=np.float16)]
    golden = grouped_matmul_v5_execution_golden(
        [np.empty((4, 2), dtype=np.float16)],
        [np.empty((1, 2, 65534), dtype=np.int8)],
        out=expected_out,
    )

    assert isinstance(golden, list)
    assert golden[0].shape == (4, 65534)
    assert golden[0].dtype == np.float16


def test_grouped_matmul_mock_golden_infers_numpy_output_shape():
    import numpy as np

    from agent.hs.ttk_plugins.aclnn_grouped_matmul_v5_golden import (
        grouped_matmul_v5_execution_golden,
    )

    golden = grouped_matmul_v5_execution_golden(
        [np.empty((4, 2), dtype=np.float16)],
        [np.empty((1, 2, 65534), dtype=np.int8)],
    )

    assert golden.shape == (4, 65534)
    assert golden.dtype == np.float16


def test_grouped_matmul_mock_golden_preserves_multiple_outputs():
    import torch

    from agent.hs.ttk_plugins.aclnn_grouped_matmul_v5_golden import (
        grouped_matmul_v5_execution_golden,
    )

    golden = grouped_matmul_v5_execution_golden(
        [torch.empty((2, 4)), torch.empty((3, 8))],
        [torch.empty((4, 5)), torch.empty((8, 7))],
    )

    assert [tuple(item.shape) for item in golden] == [(2, 5), (3, 7)]
    assert all(item.device.type == "cpu" for item in golden)


def test_grouped_matmul_input_hook_materializes_counts_for_confirmed_a8w4():
    import torch

    group = torch.full((4,), -1, dtype=torch.int64)

    _invoke_grouped_matmul_input_hook(
        group,
        m=10,
        group_list_type=0,
        tensor_dtypes=(("int8",), ("int4",)),
    )

    assert group.tolist() == [3, 3, 2, 2]


def test_grouped_matmul_input_hook_honors_type_zero_for_a8w8_without_offset():
    import torch

    group = torch.full((4,), -1, dtype=torch.int64)

    _invoke_grouped_matmul_input_hook(
        group,
        m=10,
        group_list_type=0,
        tensor_dtypes=(("int8",), ("int8",)),
    )

    assert group.tolist() == [3, 6, 8, 10]


def test_grouped_matmul_input_hook_materializes_cumulative_counts():
    import torch

    group = torch.full((4,), -1, dtype=torch.int64)
    offset = [torch.ones((1, 1, 8))]

    _invoke_grouped_matmul_input_hook(
        group, m=10, group_list_type=0, offset=offset,
    )

    assert group.tolist() == [3, 6, 8, 10]


def test_grouped_matmul_input_hook_materializes_type_two_pairs():
    import torch

    group = torch.full((4, 2), -1, dtype=torch.int64)
    offset = [torch.ones((1, 1, 8))]

    _invoke_grouped_matmul_input_hook(
        group, m=2, group_list_type=2, offset=offset,
    )

    assert group.tolist() == [[0, 1], [1, 1], [2, 0], [3, 0]]


def test_grouped_matmul_input_hook_honors_type_two_for_a8w8_without_offset():
    import torch

    group = torch.full((4, 2), -1, dtype=torch.int64)

    _invoke_grouped_matmul_input_hook(
        group,
        m=2,
        group_list_type=2,
        tensor_dtypes=(("int8",), ("int8",)),
    )

    assert group.tolist() == [[0, 1], [1, 1], [2, 0], [3, 0]]


def test_grouped_matmul_input_hook_rejects_bad_type_two_shape():
    import torch

    group = torch.full((4, 3), -1, dtype=torch.int64)

    with pytest.raises(ValueError, match=r"requires groupListOptional shape \(4, 2\)"):
        _invoke_grouped_matmul_input_hook(
            group,
            m=2,
            group_list_type=2,
            tensor_dtypes=(("int8",), ("int8",)),
        )


def test_grouped_matmul_input_hook_encodes_actual_multi_x_group_sizes():
    import torch

    from agent.hs.ttk_plugins.aclnn_grouped_matmul_v5_golden import (
        grouped_matmul_v5_execution_input,
    )

    x = [torch.empty((m, 8)) for m in (64, 0, 128, 64)]
    weight = [torch.empty((8, 16)) for _ in x]

    cumulative = torch.full((4,), -1, dtype=torch.int64)
    grouped_matmul_v5_execution_input(
        x, weight, groupListOptional=cumulative,
        groupType=0, groupListType=0,
    )
    assert cumulative.tolist() == [64, 64, 192, 256]

    counts = torch.full((4,), -1, dtype=torch.int64)
    grouped_matmul_v5_execution_input(
        x, weight, groupListOptional=counts,
        groupType=0, groupListType=1,
    )
    assert counts.tolist() == [64, 0, 128, 64]

    sparse = torch.full((4, 2), -1, dtype=torch.int64)
    grouped_matmul_v5_execution_input(
        x, weight, groupListOptional=sparse,
        groupType=0, groupListType=2,
    )
    assert sparse.tolist() == [[0, 64], [2, 128], [3, 64], [1, 0]]


def test_grouped_matmul_input_hook_materializes_numpy_group_only():
    import numpy as np

    from agent.hs.ttk_plugins.aclnn_grouped_matmul_v5_golden import (
        grouped_matmul_v5_execution_input,
    )

    x = np.full((5, 256), 7, dtype=np.int8)
    weight = np.full((2, 256, 8), 7, dtype=np.int8)
    group = np.full((2,), -1, dtype=np.int64)

    grouped_matmul_v5_execution_input(
        [x],
        [weight],
        groupListOptional=group,
        groupType=0,
        groupListType=0,
        tensor_dtypes=(("int8",), ("int4",)),
    )

    # A8W4 without offset is interpreted as groupListType=1 by CANN.
    assert group.tolist() == [3, 2]
    assert np.unique(x).tolist() == [7]
    assert np.unique(weight).tolist() == [7]


def test_grouped_matmul_input_hook_uses_input_m_axis_for_group_type_two():
    import torch

    from agent.hs.ttk_plugins.aclnn_grouped_matmul_v5_golden import (
        grouped_matmul_v5_execution_input,
    )

    group = torch.full((4,), -1, dtype=torch.int64)
    grouped_matmul_v5_execution_input(
        [torch.empty((10, 8))],
        [torch.empty((8, 16))],
        groupListOptional=group,
        groupType=2,
        groupListType=1,
    )

    assert group.tolist() == [3, 3, 2, 2]
    assert sum(group.tolist()) == 10


def test_grouped_matmul_input_hook_leaves_no_split_group_values_unchanged():
    import torch

    from agent.hs.ttk_plugins.aclnn_grouped_matmul_v5_golden import (
        grouped_matmul_v5_execution_input,
    )

    group = torch.tensor([9, 7], dtype=torch.int64)
    grouped_matmul_v5_execution_input(
        [torch.empty((3, 8)), torch.empty((7, 8))],
        [torch.empty((8, 16)), torch.empty((8, 16))],
        groupListOptional=group,
        groupType=-1,
        groupListType=1,
    )

    assert group.tolist() == [9, 7]


def _invoke_grouped_matmul_quantized_hook(*, x_dtype, logical_weight_dtype):
    import torch

    from agent.hs.ttk_plugins.aclnn_grouped_matmul_v5_golden import (
        grouped_matmul_v5_execution_input,
    )

    tensors = {
        "x": torch.full((2, 256), 7, dtype=x_dtype),
        "weight": torch.full((1, 256, 8), 7, dtype=torch.int8),
        "bias": torch.full((1, 8), 7.0, dtype=torch.float32),
        "scale": torch.zeros((1, 1, 8), dtype=torch.uint64),
        "per_token": torch.zeros((2,), dtype=torch.float32),
        "group": torch.full((1,), -1, dtype=torch.int64),
    }
    tensor_dtypes = (
        ("int8" if x_dtype == torch.int8 else "float16",),
        (logical_weight_dtype,),
        ("float32",),
        ("uint64",),
        None,
        None,
        None,
        ("float32",),
        "int64",
        None,
        None,
        None,
        ("float16",),
        None,
        None,
    )
    grouped_matmul_v5_execution_input(
        [tensors["x"]],
        [tensors["weight"]],
        [tensors["bias"]],
        [tensors["scale"]],
        None,
        None,
        None,
        [tensors["per_token"]],
        tensors["group"],
        None,
        None,
        None,
        2,
        0,
        1,
        0,
        None,
        tensor_dtypes=tensor_dtypes,
    )
    return tensors


def test_grouped_matmul_input_hook_does_not_rewrite_a16w4_values():
    import torch

    tensors = _invoke_grouped_matmul_quantized_hook(
        x_dtype=torch.float16, logical_weight_dtype="int4",
    )

    assert tensors["x"].unique().tolist() == [7.0]
    assert tensors["weight"].unique().tolist() == [7]
    assert tensors["bias"].unique().tolist() == [7.0]
    assert tensors["scale"].unique().tolist() == [0]
    assert tensors["per_token"].unique().tolist() == [0.0]


def test_grouped_matmul_input_hook_does_not_rewrite_a8w8_values():
    import torch

    tensors = _invoke_grouped_matmul_quantized_hook(
        x_dtype=torch.int8, logical_weight_dtype="int8",
    )

    assert tensors["x"].unique().tolist() == [7]
    assert tensors["weight"].unique().tolist() == [7]
    assert tensors["bias"].unique().tolist() == [7.0]
    assert tensors["scale"].unique().tolist() == [0]
    assert tensors["per_token"].unique().tolist() == [0.0]


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
