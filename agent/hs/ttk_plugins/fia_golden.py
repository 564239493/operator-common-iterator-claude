"""CPU Golden for the unquantized BNSD FIA baseline.

This intentionally rejects unsupported layouts/features instead of silently
producing an approximate reference for a different FIA mode.
"""
from __future__ import annotations

import torch


def fia_bnsd_fp_golden(
    query,
    key,
    value,
    *,
    pse_shift=None,
    atten_mask=None,
    actual_seq_lengths=None,
    actual_seq_lengths_kv=None,
    num_heads=1,
    scale=1.0,
    pre_tokens=2147483647,
    next_tokens=2147483647,
    input_layout="BSH",
    num_key_value_heads=0,
    sparse_mode=0,
    inner_precise=0,
    block_size=0,
    antiquant_mode=0,
    softmax_lse_flag=False,
    key_antiquant_mode=0,
    value_antiquant_mode=0,
    **kwargs,
):
    if input_layout != "BNSD":
        raise NotImplementedError("FIA CPU Golden currently supports BNSD only")
    if query.ndim != 4 or key.ndim != 4 or value.ndim != 4:
        raise ValueError("BNSD FIA Golden requires rank-4 query/key/value")
    if query.shape[0] != key.shape[0] or key.shape != value.shape:
        raise ValueError("FIA Golden requires matching batch and key/value shapes")
    if query.shape[-1] != key.shape[-1]:
        raise ValueError("FIA Golden requires matching Q/K head dimensions")
    if key.shape[1] not in (1, query.shape[1]):
        raise NotImplementedError("FIA Golden currently supports MHA or single-head MQA")
    unsupported = {
        name: value
        for name, value in kwargs.items()
        if value is not None and name not in {"backend", "tensor_formats", "tensor_dtypes",
                                               "use_torch", "short_soc_version", "testcase_name"}
    }
    if unsupported:
        raise NotImplementedError(f"unsupported FIA optional inputs: {sorted(unsupported)}")

    q = query.float()
    k = key.float()
    v = value.float()
    scores = torch.matmul(q, k.transpose(-2, -1)) * float(scale)
    if pse_shift is not None:
        scores = scores + pse_shift.float()
    if atten_mask is not None:
        mask = atten_mask.to(torch.bool)
        scores = scores.masked_fill(mask, torch.finfo(scores.dtype).min)
    probability = torch.softmax(scores, dim=-1)
    output = torch.matmul(probability, v).to(query.dtype)
    if softmax_lse_flag:
        lse = torch.logsumexp(scores, dim=-1).float()
    else:
        lse = torch.empty((0,), dtype=torch.float32)
    return output, lse


__golden__ = {
    "e2e": {
        "torch_npu.npu_fused_infer_attention_score": "fia_bnsd_fp_golden",
    }
}
