"""Generic TTK ACLNN ``__input__`` plugin template.

``generate_cases.py`` replaces the two marker lines below with the detected
operator name and the auxiliary-attribute -> signature-position mapping, then
writes the result to ``<iter>/golden.py``. The generated file is self-contained
and has no dependency on this template at runtime.
"""
from __future__ import annotations

import ast
import json
import logging

import numpy as np
import torch


__input__ = {"aclnn": {}}

AUX_TARGETS = {}


def _coerce(value):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return value


def _assign(target, value, aux_name: str) -> None:
    if target is None or value is None:
        return
    value = _coerce(value)
    if isinstance(target, torch.Tensor):
        tensor = torch.as_tensor(
            value, dtype=target.dtype, device=target.device,
        )
        if tensor.numel() != target.numel():
            raise ValueError(
                f"auxiliary parameter {aux_name} has {tensor.numel()} values, "
                f"target tensor has {target.numel()} elements"
            )
        with torch.no_grad():
            target.copy_(tensor.reshape(target.shape))
        return
    array = np.asarray(value, dtype=target.dtype)
    if array.size != target.size:
        raise ValueError(
            f"auxiliary parameter {aux_name} has {array.size} values, "
            f"target tensor has {target.size} elements"
        )
    target[...] = array.reshape(target.shape)


def apply_aux_params(*args, **kwargs):
    for aux_name, signature_index in AUX_TARGETS.items():
        value = kwargs.get(aux_name)
        if value is None:
            continue
        if signature_index >= len(args):
            raise ValueError(
                f"auxiliary parameter {aux_name} signature index "
                f"{signature_index} is out of range ({len(args)} args)"
            )
        target = args[signature_index]
        logging.info(
            "applying auxiliary parameter %s to signature[%s]",
            aux_name,
            signature_index,
        )
        if isinstance(target, (list, tuple)):
            if (
                isinstance(value, (list, tuple))
                and len(value) == len(target)
                and any(isinstance(item, (list, tuple)) for item in value)
            ):
                for sub_target, sub_value in zip(target, value):
                    _assign(sub_target, sub_value, aux_name)
            else:
                for sub_target in target:
                    _assign(sub_target, value, aux_name)
        else:
            _assign(target, value, aux_name)
