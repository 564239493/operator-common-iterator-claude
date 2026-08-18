"""Executor template regressions and deterministic runner mock tests."""

import ast
from types import SimpleNamespace

import pytest
import torch

from executer.resources.generator import generate_api_class_for_op  # noqa: E402

try:
    import asyncssh  # noqa: F401
except ImportError:
    _mock_execute = None
else:
    from executer.runner import _mock_execute  # noqa: E402


requires_asyncssh = pytest.mark.skipif(
    _mock_execute is None, reason="runner import chain requires asyncssh"
)


def test_default_template_casts_missing_acl_int_array_to_typed_nullptr():
    signature = """aclnnStatus aclnnExampleGetWorkspaceSize(
        const aclTensor *x,
        aclIntArray *tuningConfigOptional,
        aclTensor *out,
        uint64_t *workspaceSize,
        aclOpExecutor **executor)"""
    cases = [{
        "outputs": "out",
        "api_type": "function",
        "aclnn_api_type": "aclnn_function",
        "inputs": [
            {"name": "x", "type": "tensor", "required": True},
            {
                "name": "tuningConfigOptional", "type": "attrs",
                "required": False, "range_values": None,
            },
        ],
    }]

    generated = generate_api_class_for_op(cases, signature, "aclnnExample")

    assert 'elif type == "aclIntArray":' in generated
    assert "ctypes.POINTER(AclIntArray)" in generated
    assert "ctypes.cast(null_void_ptr, AclIntArrayPtr)" in generated


def _load_generated_cpu_class(generated):
    tree = ast.parse(generated)
    cpu_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Function"
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[cpu_class], type_ignores=[])
    )
    namespace = {
        "BaseApi": object,
        "InputDataset": object,
        "register": lambda _name: (lambda cls: cls),
        "torch": torch,
    }
    exec(compile(module, "<generated-cpu-class>", "exec"), namespace)
    return namespace["Function"]


def test_cpu_golden_binding_supports_kwargs_and_reports_missing_required():
    signature = (
        "aclnnStatus aclnnExampleGetWorkspaceSize(const aclTensor *x, "
        "const aclTensor *weight, aclTensor *out, uint64_t *workspaceSize, "
        "aclOpExecutor **executor)"
    )
    cases = [{
        "outputs": "out",
        "api_type": "function",
        "aclnn_api_type": "aclnn_function",
        "inputs": [
            {"name": "x", "type": "tensor", "required": True},
            {"name": "weight", "type": "tensor", "required": True},
        ],
    }]
    generated = generate_api_class_for_op(cases, signature, "aclnnExample")
    assert "# TODO: CPU_GOLDEN" in generated
    assert "_REQUIRED_TENSOR_NAMES = ['x', 'weight']" in generated
    cpu_class = _load_generated_cpu_class(generated)

    instance = cpu_class()
    result = instance(SimpleNamespace(
        args=[], kwargs={"x": torch.ones(2), "weight": torch.ones(2)}
    ))
    assert isinstance(result, torch.Tensor)

    with pytest.raises(TypeError, match="missing required tensor inputs.*weight"):
        instance(SimpleNamespace(args=[], kwargs={"x": torch.ones(2)}))


def test_cpu_golden_binding_supports_args_mode():
    signature = (
        "aclnnStatus aclnnExampleGetWorkspaceSize(const aclTensor *x, "
        "aclTensor *out, uint64_t *workspaceSize, aclOpExecutor **executor)"
    )
    cases = [{
        "outputs": "out",
        "api_type": "function",
        "aclnn_api_type": "aclnn_function",
        "inputs": [{"name": "x", "type": "tensor", "required": True}],
    }]
    generated = generate_api_class_for_op(cases, signature, "aclnnExample")
    cpu_class = _load_generated_cpu_class(generated)
    instance = cpu_class()
    instance.task_result = SimpleNamespace(case_config=SimpleNamespace(
        inputs=[SimpleNamespace(name="x")]
    ))

    result = instance(SimpleNamespace(args=[torch.ones(2)], kwargs={}))
    assert isinstance(result, torch.Tensor)


def test_grouped_matmul_v5_template_handles_weight_layout_by_transpose_flag():
    signature = (
        "aclnnStatus aclnnGroupedMatmulV5GetWorkspaceSize("
        "const aclTensorList *x, const aclTensorList *weight, "
        "const aclTensor *groupListOptional, int64_t groupType, "
        "int64_t groupListType, "
        "aclTensorList *out, aclTensorList *activationFeatureOutOptional, "
        "aclTensorList *dynQuantScaleOutOptional, uint64_t *workspaceSize, "
        "aclOpExecutor **executor)"
    )
    cases = [{
        "outputs": "out,activationFeatureOutOptional,dynQuantScaleOutOptional",
        "api_type": "function",
        "aclnn_api_type": "aclnn_function",
        "inputs": [
            {"name": "x", "type": "tensors", "required": True},
            {"name": "weight", "type": "tensors", "required": True},
            {
                "name": "groupListOptional", "type": "tensor",
                "required": True,
            },
            {
                "name": "groupType", "type": "attr", "required": True,
                "range_values": 0,
            },
            {
                "name": "groupListType", "type": "attr", "required": True,
                "range_values": 0,
            },
            {
                "name": "weight_transposed", "type": "attr",
                "required": True, "range_values": False,
            },
        ],
    }]

    generated = generate_api_class_for_op(
        cases, signature, "aclnnGroupedMatmulV5"
    )

    # Generated executor remains valid Python and does not restore the old
    # unconditional x transpose.
    ast.parse(generated)
    assert 'input_data.kwargs["x"][0]' not in generated
    assert '"weight_transposed"' in generated

    # The generated template contains both the legacy 2-D conversion and the
    # groupType=0 A8W8 3-D branch.  The latter is functionally tested below.
    assert "return tensor.transpose(-1, -2).contiguous()" in generated
    assert (
        "return tensor.transpose(-1, -2).contiguous().transpose(-1, -2)"
        in generated
    )
    assert 'pop("activationFeatureOutOptional", None)' in generated
    assert 'pop("dynQuantScaleOutOptional", None)' in generated
    assert "_build_grouped_matmul_v5_group_list" in generated
    assert "_prepare_grouped_matmul_v5_weight" in generated
    assert "tuple(group_list.shape) != expected_shape" in generated
    assert "x_tensor.dtype == torch.int8" in generated
    assert "weight_tensor.dtype == torch.int8" in generated


def test_grouped_matmul_v5_group_list_builders_and_weight_layout():
    signature = (
        "aclnnStatus aclnnGroupedMatmulV5GetWorkspaceSize("
        "const aclTensorList *x, const aclTensorList *weight, "
        "const aclTensor *groupListOptional, int64_t groupType, "
        "int64_t groupListType, aclTensorList *out, "
        "uint64_t *workspaceSize, aclOpExecutor **executor)"
    )
    cases = [{
        "outputs": "out",
        "api_type": "function",
        "aclnn_api_type": "aclnn_function",
        "inputs": [
            {"name": "x", "type": "tensors", "required": True},
            {"name": "weight", "type": "tensors", "required": True},
            {"name": "groupListOptional", "type": "tensor", "required": True},
            {"name": "groupType", "type": "attr", "range_values": 0},
            {"name": "groupListType", "type": "attr", "range_values": 0},
        ],
    }]
    generated = generate_api_class_for_op(
        cases, signature, "aclnnGroupedMatmulV5"
    )
    tree = ast.parse(generated)
    helper_names = {
        "_build_grouped_matmul_v5_group_list",
        "_prepare_grouped_matmul_v5_group_list",
        "_prepare_grouped_matmul_v5_weight",
    }
    helpers = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in helper_names
    ]
    assert {node.name for node in helpers} == helper_names
    helper_module = ast.fix_missing_locations(
        ast.Module(body=helpers, type_ignores=[])
    )
    namespace = {"torch": torch}
    exec(compile(helper_module, "<group-list-helper>", "exec"), namespace)
    build = namespace["_build_grouped_matmul_v5_group_list"]
    prepare = namespace["_prepare_grouped_matmul_v5_group_list"]
    prepare_weight = namespace["_prepare_grouped_matmul_v5_weight"]

    assert build(7, 1, 0).tolist() == [7]
    assert build(8, 3, 0).tolist() == [3, 6, 8]
    assert build(8, 3, 1).tolist() == [3, 3, 2]
    assert build(8, 3, 2).tolist() == [[0, 3], [1, 3], [2, 2]]
    assert build(1, 3, 0).tolist() == [1, 1, 1]
    assert build(1, 3, 1).tolist() == [1, 0, 0]
    assert build(1, 3, 2).tolist() == [[0, 1], [1, 0], [2, 0]]
    result = build(1024, 1011, 0)
    assert result.dtype == torch.int64
    assert result.numel() == 1011
    assert result[-1].item() == 1024
    assert torch.all(result[1:] >= result[:-1])
    with pytest.raises(ValueError, match="group count must be positive"):
        build(7, 0, 0)
    with pytest.raises(ValueError, match="Unsupported.*groupListType"):
        build(7, 1, 3)

    x = torch.zeros((8, 4), dtype=torch.int8)
    weight = torch.zeros((3, 4, 5), dtype=torch.int8)
    raw_1d = torch.full((3,), 2147483647, dtype=torch.int64)
    raw_2d = torch.full((3, 2), 2147483647, dtype=torch.int64)
    assert prepare([x], [weight], raw_1d, 0, 0).tolist() == [3, 6, 8]
    assert prepare([x], [weight], raw_1d, 0, 1).tolist() == [3, 3, 2]
    assert prepare([x], [weight], raw_2d, 0, 2).tolist() == [
        [0, 3], [1, 3], [2, 2]
    ]
    with pytest.raises(ValueError, match="groupListOptional is required"):
        prepare([x], [weight], None, 0, 0)
    with pytest.raises(ValueError, match="dtype must be torch.int64"):
        prepare([x], [weight], raw_1d.to(torch.int32), 0, 0)
    assert prepare([x], [weight], raw_1d, 2, 0) is raw_1d
    assert prepare([x.float()], [weight], raw_1d, 0, 0) is raw_1d
    with pytest.raises(ValueError, match="shape must be"):
        prepare([x], [weight], torch.zeros(2, dtype=torch.int64), 0, 0)
    with pytest.raises(ValueError, match="shape must be"):
        prepare([x], [weight], torch.zeros((3, 9), dtype=torch.int64), 0, 2)

    non_transposed = torch.arange(60, dtype=torch.int8).reshape(3, 4, 5)
    npu_non_transposed = prepare_weight(non_transposed, 0, False)
    assert npu_non_transposed.shape == (3, 4, 5)
    assert npu_non_transposed.is_contiguous()
    assert torch.equal(npu_non_transposed, non_transposed)

    transposed = torch.arange(60, dtype=torch.int8).reshape(3, 5, 4)
    npu_transposed = prepare_weight(transposed, 0, True)
    assert npu_transposed.shape == (3, 4, 5)
    assert not npu_transposed.is_contiguous()
    assert torch.equal(npu_transposed, transposed.transpose(-1, -2))

    legacy_2d = torch.arange(20, dtype=torch.float32).reshape(5, 4)
    assert prepare_weight(legacy_2d, -1, False).shape == (4, 5)
    assert prepare_weight(legacy_2d, -1, True).shape == (5, 4)

    # Unverified layouts must pass through unchanged instead of inheriting the
    # legacy groupType=-1, 2-D FP32 conversion by fall-through.
    group_type_2 = torch.arange(20, dtype=torch.float32).reshape(5, 4)
    assert prepare_weight(group_type_2, 2, False) is group_type_2
    fp16_2d = legacy_2d.to(torch.float16)
    assert prepare_weight(fp16_2d, -1, False) is fp16_2d
    other_3d = torch.arange(24, dtype=torch.float32).reshape(2, 3, 4)
    assert prepare_weight(other_3d, 0, False) is other_3d


@requires_asyncssh
def test_mock_all_pass():
    result = _mock_execute([{"id": 1}, {"id": 2}], fail_every=0)
    assert result.status == "success"
    assert result.task_report_data.passed == 2
    assert result.task_report_data.failed == 0
    assert result.task_report_data.record_count == 2


@requires_asyncssh
def test_mock_deterministic_failures():
    result = _mock_execute(
        [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}], fail_every=2
    )
    assert result.status == "failed"
    assert result.task_report_data.failed == 2
    assert result.task_report_data.passed == 2
    records = result.task_report_data.report_records
    assert records[0].run_result == "pass"
    assert records[1].run_result == "fail"
    assert "MOCK_CONSTRAINT_MISMATCH" in records[1].failure_reason
    assert records[2].run_result == "pass"
    assert records[3].run_result == "fail"


@requires_asyncssh
def test_mock_fail_every_greater_than_count():
    result = _mock_execute([{"id": 1}, {"id": 2}], fail_every=5)
    assert result.status == "success"
    assert result.task_report_data.failed == 0


@requires_asyncssh
def test_mock_empty_cases():
    result = _mock_execute([], fail_every=1)
    assert result.status == "success"
    assert result.task_report_data.record_count == 0
