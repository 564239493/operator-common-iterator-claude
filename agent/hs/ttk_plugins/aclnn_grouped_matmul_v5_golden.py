"""Execution-only TTK ACLNN plugin for ``aclnnGroupedMatmulV5``.

This plugin deliberately does not validate numerical accuracy.  It returns a
cheap shape/dtype-compatible CPU tensor so Golden generation cannot mask the
more important result: whether the NPU invocation produced an output.

It also materialises ``groupListOptional`` before the ACLNN call.  TTK's
range-based input generator cannot express the required sequence relation
(counts/cumulative counts), and V5 does not validate the tensor contents on
the host.  Invalid random values can therefore become an asynchronous AICore
failure instead of a useful parameter error.
"""
from __future__ import annotations

from typing import Any

import torch


def _first_tensor(value: Any) -> torch.Tensor | None:
    if isinstance(value, torch.Tensor):
        return value
    if isinstance(value, (list, tuple)):
        return next((item for item in value if isinstance(item, torch.Tensor)), None)
    return None


def _mock_output(out: Any, x: Any, weight: Any) -> torch.Tensor:
    try:
        out_tensor = _first_tensor(out)
        if out_tensor is not None:
            return torch.zeros(
                tuple(out_tensor.shape), dtype=out_tensor.dtype, device="cpu"
            )
    except Exception:
        pass

    try:
        x_tensor = _first_tensor(x)
        weight_tensor = _first_tensor(weight)
        if x_tensor is not None and weight_tensor is not None:
            return torch.zeros(
                (int(x_tensor.shape[0]), int(weight_tensor.shape[-1])),
                dtype=torch.float32,
                device="cpu",
            )
    except Exception:
        pass

    # Keep custom Golden invocation alive even for malformed negative cases.
    # A shape mismatch is reported as non-blocking precision failure.
    return torch.zeros((1,), dtype=torch.float32, device="cpu")


def _balanced_counts(m: int, experts: int, reference: torch.Tensor) -> torch.Tensor:
    """Split ``m`` rows deterministically across ``experts`` groups."""
    counts = torch.full(
        (experts,),
        m // experts,
        dtype=reference.dtype,
        device=reference.device,
    )
    remainder = m % experts
    if remainder:
        counts[:remainder] += 1
    return counts


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
    """Overwrite TTK's random group list with a device-safe sequence.

    For A8W4 with an absent offset, CANN treats the group list as type 1 even
    if the attribute says otherwise.  Generate counts in that case so old CSV
    files are safe as well; newly converted files also normalise the attribute
    to type 1.
    """
    del (
        weight,
        biasOptional,
        scaleOptional,
        antiquantScaleOptional,
        antiquantOffsetOptional,
        perTokenScaleOptional,
        activationInputOptional,
        activationQuantScaleOptional,
        activationQuantOffsetOptional,
        splitItem,
        groupType,
        actType,
        tuningConfigOptional,
        args,
        metadata,
    )
    x_tensor = _first_tensor(x)
    group_tensor = _first_tensor(groupListOptional)
    if x_tensor is None or group_tensor is None or group_tensor.numel() == 0:
        return

    m = int(x_tensor.shape[0])
    offset_tensor = _first_tensor(offsetOptional)
    effective_type = 1 if offset_tensor is None else int(groupListType)

    with torch.no_grad():
        if effective_type == 2:
            if group_tensor.ndim != 2 or int(group_tensor.shape[1]) != 2:
                return
            experts = int(group_tensor.shape[0])
            counts = _balanced_counts(m, experts, group_tensor)
            values = torch.empty_like(group_tensor)
            values[:, 0] = torch.arange(
                experts, dtype=group_tensor.dtype, device=group_tensor.device
            )
            values[:, 1] = counts
        else:
            experts = int(group_tensor.numel())
            counts = _balanced_counts(m, experts, group_tensor)
            values = counts.cumsum(0) if effective_type == 0 else counts
            values = values.reshape(group_tensor.shape)
        group_tensor.copy_(values)


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
) -> torch.Tensor:
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
