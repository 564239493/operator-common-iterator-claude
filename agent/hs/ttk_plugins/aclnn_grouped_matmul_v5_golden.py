"""Execution-only TTK ACLNN plugin for ``aclnnGroupedMatmulV5``.

This plugin deliberately does not validate numerical accuracy.  It returns a
cheap shape/dtype-compatible CPU tensor so Golden generation cannot mask the
more important result: whether the NPU invocation produced an output.

It also materialises ``groupListOptional`` before the ACLNN call.  TTK's
range-based input generator cannot express the required sequence relation
(counts/cumulative counts), and V5 does not validate the tensor contents on
the host.  Invalid random values can therefore become an asynchronous AICore
failure instead of a useful parameter error.

All other inputs (x/weight/bias/scale/offset/perTokenScale) keep the values
TTK generated; only ``groupListOptional`` is rewritten here.  Golden stays a
shape-compatible zero mock; numerical accuracy is intentionally not checked.
"""
from __future__ import annotations

import ast
from typing import Any

import numpy as np
import torch


_ARRAY_TYPES = (torch.Tensor, np.ndarray)


def _first_array(value: Any) -> torch.Tensor | np.ndarray | None:
    """Return the first mutable Torch/NumPy array supplied by TTK."""
    if isinstance(value, _ARRAY_TYPES):
        return value
    if isinstance(value, (list, tuple)):
        return next((item for item in value if isinstance(item, _ARRAY_TYPES)), None)
    return None


def _zeros(shape: tuple[int, ...], reference: Any) -> Any:
    if isinstance(reference, torch.Tensor):
        return torch.zeros(shape, dtype=reference.dtype, device="cpu")
    if isinstance(reference, np.ndarray):
        return np.zeros(shape, dtype=reference.dtype)
    return np.zeros(shape, dtype=np.float32)


def _zeros_like_structure(value: Any) -> Any:
    """Mirror an output placeholder without retaining an NPU allocation."""
    if isinstance(value, _ARRAY_TYPES):
        return _zeros(tuple(int(dim) for dim in value.shape), value)
    if isinstance(value, list):
        mirrored = [_zeros_like_structure(item) for item in value]
        return mirrored if all(item is not None for item in mirrored) else None
    if isinstance(value, tuple):
        mirrored = tuple(_zeros_like_structure(item) for item in value)
        return mirrored if all(item is not None for item in mirrored) else None
    return None


def _array_list(value: Any) -> list[torch.Tensor | np.ndarray]:
    if isinstance(value, _ARRAY_TYPES):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, _ARRAY_TYPES)]
    return []


def _numel(value: torch.Tensor | np.ndarray) -> int:
    return int(value.numel()) if isinstance(value, torch.Tensor) else int(value.size)


_INPUT_NAMES = (
    "x",
    "weight",
    "biasOptional",
    "scaleOptional",
    "offsetOptional",
    "antiquantScaleOptional",
    "antiquantOffsetOptional",
    "perTokenScaleOptional",
    "groupListOptional",
    "activationInputOptional",
    "activationQuantScaleOptional",
    "activationQuantOffsetOptional",
    "out",
    "activationFeatureOutOptional",
    "dynQuantScaleOutOptional",
)


def _logical_dtype(metadata: dict[str, Any], index: int) -> str | None:
    """Read a logical dtype only when TTK supplied unambiguous metadata."""
    values = metadata.get("tensor_dtypes")
    if isinstance(values, str):
        try:
            values = ast.literal_eval(values)
        except (SyntaxError, ValueError):
            return None

    if isinstance(values, dict):
        value = values.get(_INPUT_NAMES[index])
    elif isinstance(values, (list, tuple)) and index < len(values):
        value = values[index]
    else:
        return None

    def normalize(item: Any) -> str | None:
        if isinstance(item, str):
            item = item.lower().replace("torch.", "")
            return {
                "fp16": "float16",
                "fp32": "float32",
                "bf16": "bfloat16",
            }.get(item, item)
        if isinstance(item, (list, tuple)) and item:
            dtypes = {normalize(child) for child in item}
            return dtypes.pop() if len(dtypes) == 1 and None not in dtypes else None
        return None

    # Multi-TensorList metadata is represented as, for example,
    # ("int4", "int4", ...).  Treat a uniform list as one unambiguous logical
    # dtype; mixed lists remain deliberately unsupported.
    return normalize(value)


def _mock_output(out: Any, x: Any, weight: Any) -> Any:
    """Create shape-compatible zeros for both NumPy and Torch TTK inputs.

    TTK supplies NumPy arrays to custom golden functions in ACLNN mode and may
    also use NumPy for the input hook when a logical dtype (such as INT4) lacks
    native Torch support.  Supporting both representations prevents comparison
    logging from masking an otherwise valid NPU execution.
    """
    mirrored_out = _zeros_like_structure(out)
    if mirrored_out is not None:
        return mirrored_out

    try:
        x_tensors = _array_list(x)
        weight_tensors = _array_list(weight)
        if x_tensors and weight_tensors:
            if len(x_tensors) == len(weight_tensors) and len(x_tensors) > 1:
                return [
                    _zeros(
                        (int(x_item.shape[0]), int(weight_item.shape[-1])),
                        x_item,
                    )
                    for x_item, weight_item in zip(x_tensors, weight_tensors)
                ]
            x_tensor = x_tensors[0]
            weight_tensor = weight_tensors[0]
            return _zeros(
                (int(x_tensor.shape[0]), int(weight_tensor.shape[-1])),
                x_tensor,
            )
    except Exception:
        pass

    # An empty golden is safer than a one-element value for malformed negative
    # cases: TTK's difference logger indexes non-empty goldens with an NPU-side
    # mismatch index, which can otherwise mask the execution result.
    return np.empty((0,), dtype=np.float32)


def _balanced_counts(
    m: int, experts: int, reference: torch.Tensor | np.ndarray
) -> torch.Tensor | np.ndarray:
    """Split ``m`` rows deterministically across ``experts`` groups."""
    if isinstance(reference, torch.Tensor):
        counts = torch.full(
            (experts,),
            m // experts,
            dtype=reference.dtype,
            device=reference.device,
        )
    else:
        counts = np.full((experts,), m // experts, dtype=reference.dtype)
    remainder = m % experts
    if remainder:
        counts[:remainder] += 1
    return counts


def _counts_from_x(
    x: Any,
    *,
    group_type: int,
    experts: int,
    reference: torch.Tensor | np.ndarray,
) -> torch.Tensor | np.ndarray:
    """Derive non-negative per-group sizes from the concrete x topology."""
    x_items = _array_list(x)
    if not x_items:
        raise ValueError("groupListOptional materialization requires x tensors")

    if group_type == 0 and len(x_items) > 1:
        if len(x_items) != experts:
            raise ValueError(
                "multi-x M-axis grouping requires one group per x tensor: "
                f"x_count={len(x_items)}, groups={experts}"
            )
        sizes = [int(item.shape[0]) for item in x_items]
        if isinstance(reference, torch.Tensor):
            return torch.tensor(
                sizes, dtype=reference.dtype, device=reference.device,
            )
        return np.asarray(sizes, dtype=reference.dtype)

    x_tensor = x_items[0]
    if group_type == 0:
        m = int(x_tensor.shape[0])
    elif group_type == 2:
        if len(x_items) != 1 or x_tensor.ndim < 2:
            raise ValueError(
                "K-axis grouping requires one rank-2-or-higher x tensor"
            )
        # transposeX describes the matmul interpretation; the concrete input
        # layout still carries M on axis 0 (x.shape[1] aligns with weight K).
        m = int(x_tensor.shape[0])
    else:
        raise ValueError(f"unsupported grouped groupType: {group_type}")
    return _balanced_counts(m, experts, reference)


def _materialized_group_list(
    counts: torch.Tensor | np.ndarray,
    *,
    group_list_type: int,
    reference: torch.Tensor | np.ndarray,
) -> torch.Tensor | np.ndarray:
    """Encode per-group sizes using the requested groupListType."""
    experts = int(counts.shape[0])
    if group_list_type == 0:
        return counts.cumsum(0).reshape(reference.shape)
    if group_list_type == 1:
        return counts.reshape(reference.shape)
    if group_list_type != 2:
        raise ValueError(f"unsupported groupListType: {group_list_type}")

    if isinstance(reference, torch.Tensor):
        indices = torch.arange(
            experts, dtype=reference.dtype, device=reference.device,
        )
        nonzero = counts != 0
        order = torch.cat((indices[nonzero], indices[~nonzero])).long()
        values = torch.empty_like(reference)
    else:
        indices = np.arange(experts, dtype=reference.dtype)
        nonzero = counts != 0
        order = np.concatenate((indices[nonzero], indices[~nonzero])).astype(
            np.intp, copy=False,
        )
        values = np.empty_like(reference)
    values[:, 0] = indices[order]
    values[:, 1] = counts[order]
    return values


def grouped_matmul_v5_execution_input(
    x: Any,
    weight: Any,
    biasOptional: Any = None,
    scaleOptional: Any = None,
    offsetOptional: Any = None,
    antiquantScaleOptional: Any = None,
    antiquantOffsetOptional: Any = None,
    perTokenScaleOptional: Any = None,
    groupListOptional: Any = None,
    activationInputOptional: Any = None,
    activationQuantScaleOptional: Any = None,
    activationQuantOffsetOptional: Any = None,
    splitItem: int = 2,
    groupType: int = 0,
    groupListType: int = 0,
    actType: int = 0,
    tuningConfigOptional: Any = None,
    *args: Any,
    **metadata: Any,
) -> None:
    """Materialise a valid ``groupListOptional`` without changing other inputs.

    For confirmed A8W4 with an absent offset, CANN treats the group list as
    type 1 even if a legacy CSV says otherwise.  Other modes, including A8W8,
    must continue to honour the explicit ``groupListType`` attribute.
    """
    group_tensor = _first_array(groupListOptional)
    if group_tensor is None or _numel(group_tensor) == 0:
        return

    group_type = int(groupType)
    if group_type not in (0, 2):
        # NO_SPLIT and invalid group types have no group-value semantics for
        # this materializer.  Preserve TTK's input so ACLNN remains authoritative.
        return

    offset_tensor = _first_array(offsetOptional)
    is_a8w4 = (
        _logical_dtype(metadata, 0) == "int8"
        and _logical_dtype(metadata, 1) == "int4"
    )
    group_list_type = (
        1 if is_a8w4 and offset_tensor is None else int(groupListType)
    )
    if group_list_type not in (0, 1, 2):
        raise ValueError(f"unsupported groupListType: {group_list_type}")

    if group_list_type == 2:
        if group_tensor.ndim != 2 or int(group_tensor.shape[1]) != 2:
            expected_shape = (int(group_tensor.shape[0]), 2)
            raise ValueError(
                "groupListType=2 requires groupListOptional shape "
                f"{expected_shape}, got {tuple(map(int, group_tensor.shape))}"
            )
    elif group_tensor.ndim != 1:
        raise ValueError(
            f"groupListType={group_list_type} requires a rank-1 "
            f"groupListOptional, got {tuple(map(int, group_tensor.shape))}"
        )

    experts = int(group_tensor.shape[0])
    counts = _counts_from_x(
        x,
        group_type=group_type,
        experts=experts,
        reference=group_tensor,
    )
    values = _materialized_group_list(
        counts,
        group_list_type=group_list_type,
        reference=group_tensor,
    )
    if isinstance(group_tensor, torch.Tensor):
        with torch.no_grad():
            group_tensor.copy_(values)
    else:
        group_tensor[...] = values


def grouped_matmul_v5_execution_golden(
    x: Any,
    weight: Any,
    biasOptional: Any = None,
    scaleOptional: Any = None,
    offsetOptional: Any = None,
    antiquantScaleOptional: Any = None,
    antiquantOffsetOptional: Any = None,
    perTokenScaleOptional: Any = None,
    groupListOptional: Any = None,
    activationInputOptional: Any = None,
    activationQuantScaleOptional: Any = None,
    activationQuantOffsetOptional: Any = None,
    splitItem: int = 2,
    groupType: int = 0,
    groupListType: int = 0,
    actType: int = 0,
    tuningConfigOptional: Any = None,
    out: Any = None,
    activationFeatureOutOptional: Any = None,
    dynQuantScaleOutOptional: Any = None,
    *args: Any,
    **metadata: Any,
) -> Any:
    del (
        biasOptional,
        scaleOptional,
        offsetOptional,
        antiquantScaleOptional,
        antiquantOffsetOptional,
        perTokenScaleOptional,
        groupListOptional,
        activationInputOptional,
        activationQuantScaleOptional,
        activationQuantOffsetOptional,
        splitItem,
        groupType,
        groupListType,
        actType,
        tuningConfigOptional,
        activationFeatureOutOptional,
        dynQuantScaleOutOptional,
        args,
        metadata,
    )
    return _mock_output(out, x, weight)


__golden__ = {
    "aclnn": {
        "aclnnGroupedMatmulV5": "grouped_matmul_v5_execution_golden",
    }
}


__input__ = {
    "aclnn": {
        "aclnnGroupedMatmulV5": "grouped_matmul_v5_execution_input",
    }
}
