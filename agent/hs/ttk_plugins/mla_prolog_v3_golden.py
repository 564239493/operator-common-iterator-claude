"""CPU Golden for the unquantized BF16 MLA Prolog V3 baseline."""
from __future__ import annotations

import torch
import torch_npu

torch_npu.npu.config.allow_internal_format = True


def _rms_norm(x, gamma, epsilon):
    x32 = x.float()
    return (x32 * torch.rsqrt(x32.square().mean(dim=-1, keepdim=True) + epsilon)
            * gamma.float()).to(x.dtype)


def _rope_interleave_half(x, rope_sin, rope_cos):
    odd = x[..., ::2]
    even = x[..., 1::2]
    part1 = torch.cat((odd, even), dim=-1)
    part2 = torch.cat((-even, odd), dim=-1)
    sin = rope_sin.float().unsqueeze(-2)
    cos = rope_cos.float().unsqueeze(-2)
    return (part1.float() * cos + part2.float() * sin).to(x.dtype)


def mla_prolog_v3_golden(
    token_x, weight_dq, weight_uq_qr, weight_uk, weight_dkv_kr,
    rmsnorm_gamma_cq, rmsnorm_gamma_ckv, rope_sin, rope_cos,
    kv_cache, kr_cache, cache_index=None, *,
    rmsnorm_epsilon_cq=1e-5, rmsnorm_epsilon_ckv=1e-5,
    cache_mode="PA_BSND", query_norm_flag=False,
    weight_quant_mode=0, kv_cache_quant_mode=0, query_quant_mode=0,
    **kwargs,
):
    if (weight_quant_mode, kv_cache_quant_mode, query_quant_mode) != (0, 0, 0):
        raise NotImplementedError("MLA Golden currently supports unquantized mode only")
    if cache_mode != "PA_BSND":
        raise NotImplementedError("MLA Golden currently supports PA_BSND only")
    if token_x.ndim != 3:
        raise NotImplementedError("MLA Golden currently supports non-merged [B,S,He]")

    # cQ = RMSNorm(x @ WDQ); fused WUQ/WR output is [B,S,N*(D+Dr)].
    cq = _rms_norm(
        torch.matmul(token_x.float(), weight_dq.float()).to(token_x.dtype),
        rmsnorm_gamma_cq, float(rmsnorm_epsilon_cq),
    )
    # The fused implementation materializes this stage in BF16 before the
    # final WUK projection; preserving that rounding is important for the
    # percentage-based TTK comparison on near-zero values.
    qc_qr = torch.matmul(cq.float(), weight_uq_qr.float()).to(token_x.dtype)
    heads, nope_dim, hckv = weight_uk.shape
    rope_dim = rope_sin.shape[-1]
    qc_qr = qc_qr.reshape(*token_x.shape[:-1], heads, nope_dim + rope_dim)
    qc, qr = qc_qr.split((nope_dim, rope_dim), dim=-1)
    query_out = torch.einsum(
        "...nd,ndh->...nh", qc.float(), weight_uk.float()
    ).to(token_x.dtype)
    query_rope_out = _rope_interleave_half(qr.to(token_x.dtype), rope_sin, rope_cos)

    # In non-quantized/query_norm_flag=False mode these three optional outputs
    # are nullptr on device; TTK represents them as empty tensors.
    empty_scale = torch.empty((0,), dtype=torch.float32)
    empty_norm = torch.empty((0,), dtype=token_x.dtype)
    return query_out, query_rope_out, empty_scale, empty_norm, empty_scale.clone()


__golden__ = {"e2e": {"torch_npu.npu_mla_prolog_v3": "mla_prolog_v3_golden"}}
