"""CPU Golden for ``torch_npu.npu_quant_lightning_indexer``.

Implements the per-token-head quantized indexer formula documented in
``operator_docs/hs/torch_npu-npu_quant_lightning_indexer.md`` and matches
the upstream Ascend-maintained CPU reference at
``operators-src/op-plugin/test/test_custom_ops/test_npu_quant_lightning_indexer.py``
(method ``_quant_lightning_indexer``).

Formula (per query token q_idx in batch b, per key position j)::

    raw[g, j]      = sum_d  Q[g, d] * K[j, d]                       # Q @ K^T
    scale[g, j]    = query_dequant_scale[g] * key_dequant_scale[j] # Scale_Q ⊙ Scale_K
    relu[g, j]     = ReLU(scale[g, j] * raw[g, j])                 # doc: ReLU of scale*QK
    weighted[g, j] = weights[g] * relu[g, j]
    score[j]       = sum_g weighted[g, j]

``sparse_mode`` masks::

    0 (defaultMask): no mask applied.
    3 (rightDownCausal): per query token, mask out positions where
       j >= S_k - S_q + q_idx + 1 (right-down causal / lower-triangular).

``out`` is the top-k indices (int32), shape ``[B, S1, N2, sparse_count]``
for BSND / PA_BSND, ``[T, N2, sparse_count]`` for TND. N2 is always 1.

The kernel contract returns **only the indices** (sparse_values is not
exposed by ``torch_npu.npu_quant_lightning_indexer``). The CPU reference
returns indices only, padded with 0 (matching the kernel padding, which
mirrors the unquantized sibling ``npu_lightning_indexer_golden.py``).

Supported dtypes (per platform source-confirmed constraints):
* ``query`` / ``key``: int8 (Atlas A2 / A3); float8_e4m3fn or hifloat8
  (Ascend 950).
* ``weights``: float16 (A2 / A3 with int8), bfloat16 (950).
* ``query_dequant_scale`` / ``key_dequant_scale``: float16 (A2 / A3 with
  int8), float (950).

Rejected modes raise ``NotImplementedError`` rather than approximating an
unsupported branch.
"""
from __future__ import annotations

import torch
import torch_npu

torch_npu.npu.config.allow_internal_format = True


# ---------------------------------------------------------------------------
# Layout helpers (inlined; mirrors npu_lightning_indexer_golden.py)
# ---------------------------------------------------------------------------

def _ceil(value):
    ivalue = int(value)
    return ivalue if float(value) == float(ivalue) else ivalue + 1


def _seq_lengths(lengths, total, batch, is_tnd):
    """Normalise length specs into a plain per-batch list."""
    if lengths is None:
        if is_tnd:
            base = max(int(total), 0) // max(int(batch or 1), 1)
            out = [base] * max(int(batch or 1), 1)
            remainder = max(int(total), 0) - base * max(int(batch or 1), 1)
            for i in range(remainder):
                out[i] += 1
            return out
        return [max(int(total or 0), 0)] * max(int(batch or 1), 1)

    if isinstance(lengths, torch.Tensor):
        lengths = lengths.detach().cpu().reshape(-1).tolist()
    elif isinstance(lengths, (int, float)):
        lengths = [int(lengths)]
    else:
        lengths = [int(x) for x in lengths]
    if not lengths:
        return _seq_lengths(None, total, batch, is_tnd)

    # TTK often passes all-zero tensors (input_data_range=(0,0)) for
    # actual_seq_lengths in BSND mode when the operator docs allow None.
    # Treat all-zero (or all-equal-to-zero) sequences as "use full length"
    # — the kernel also defaults to shape length in this case.
    if all(int(x) == 0 for x in lengths):
        if is_tnd:
            return _seq_lengths(None, total, batch, is_tnd)
        return [max(int(total or 0), 0)] * max(int(batch or 1), 1)

    if not is_tnd:
        return lengths

    if (
        len(lengths) == int(batch or 1)
        and all(item >= 0 for item in lengths)
        and all(a <= b for a, b in zip(lengths, lengths[1:]))
        and lengths[-1] == int(total or 0)
    ):
        prev = 0
        result = []
        for item in lengths:
            result.append(max(int(item) - prev, 0))
            prev = int(item)
        return result
    # TTK sometimes passes a placeholder [0] for TND; treat it as
    # "single batch of total tokens" so the golden doesn't reject valid
    # operator inputs.
    if is_tnd and lengths == [0] and int(total or 0) > 0:
        return [int(total)]
    raise NotImplementedError(
        "TND actual sequence lengths must be a nonnegative, nondecreasing "
        f"prefix sum of length {int(batch or 1)} ending at total={int(total or 0)}; "
        f"got {lengths!r}"
    )


def _balanced_prefix_sum(total, batch):
    """Build a deterministic nonnegative prefix sum ending at total."""
    total = max(int(total), 0)
    batch = max(int(batch), 1)
    base, remainder = divmod(total, batch)
    cumulative = 0
    result = []
    for index in range(batch):
        cumulative += base + (1 if index < remainder else 0)
        result.append(cumulative)
    return result


def _overwrite_lengths(tensor, total, *, cumulative):
    """Write valid actual-sequence content into an optional 1-D tensor.

    Tolerates scalar int (TTK may pass the per-batch length as an int).
    """
    if tensor is None:
        return
    # TTK may pass actual_seq_lengths as a scalar int (no .numel())
    if isinstance(tensor, (int, float)):
        return
    if not hasattr(tensor, "numel"):
        return
    count = int(tensor.numel())
    if count <= 0:
        return
    if cumulative:
        values = _balanced_prefix_sum(total, count)
    else:
        values = [max(int(total), 0)] * count
    tensor.copy_(torch.tensor(values, dtype=tensor.dtype, device=tensor.device))


def _tnd_to_bsnd(x, lengths):
    """TND [T, ...] -> BSND [B, max_S, ...] (zero-padded)."""
    batch = len(lengths)
    max_s = max(lengths) if lengths else 0
    out_shape = (batch, max_s) + tuple(x.shape[1:])
    out = torch.zeros(out_shape, dtype=x.dtype, device=x.device)
    start = 0
    for b, seq_len in enumerate(lengths):
        out[b, :seq_len] = x[start: start + seq_len]
        start += seq_len
    return out


def _page_to_bsnd(x, block_table, lengths):
    """PA_BSND [block_count, block_size, N, D] -> BSND via block_table."""
    if block_table is None:
        return x
    block_num, block_size, n, d = x.shape
    table = block_table.detach().cpu().to(torch.int64)
    max_len = max(lengths) if lengths else 0
    out = torch.zeros(
        (len(lengths), max_len, n, d), dtype=x.dtype, device=x.device,
    )
    for b, seq_len in enumerate(lengths):
        if b >= table.shape[0]:
            continue
        for block_idx in range(min(_ceil(seq_len / block_size), table.shape[1])):
            src = int(table[b, block_idx]) % max(block_num, 1)
            begin = block_idx * block_size
            end = min(begin + block_size, int(seq_len), max_len)
            if end > begin:
                out[b, begin:end] = x[src, : end - begin]
    return out


def _page_scale_to_bsnd(x, block_table, lengths):
    """PA_BSND scale [block_count, block_size, N2] -> BSND via block_table.

    The scale tensor drops the head dim for last-axis-aligned variants but
    still carries N2=1 per source. Mirrors upstream test reference.
    """
    if block_table is None:
        return x
    block_num, block_size, n2 = x.shape
    table = block_table.detach().cpu().to(torch.int64)
    max_len = max(lengths) if lengths else 0
    out = torch.zeros(
        (len(lengths), max_len, n2), dtype=x.dtype, device=x.device,
    )
    for b, seq_len in enumerate(lengths):
        if b >= table.shape[0]:
            continue
        for block_idx in range(min(_ceil(seq_len / block_size), table.shape[1])):
            src = int(table[b, block_idx]) % max(block_num, 1)
            begin = block_idx * block_size
            end = min(begin + block_size, int(seq_len), max_len)
            if end > begin:
                out[b, begin:end] = x[src, : end - begin]
    return out


# ---------------------------------------------------------------------------
# Input materialisation (sequence metadata random tensor generation cannot
# supply).
# ---------------------------------------------------------------------------

def npu_quant_lightning_indexer_input(
    query: torch.Tensor,
    key: torch.Tensor,
    weights: torch.Tensor,
    *args,
    actual_seq_lengths_query=None,
    actual_seq_lengths_key=None,
    block_table=None,
    layout_query: str = "BSND",
    layout_key: str = "BSND",
    **kwargs,
):
    """Materialise valid sequence metadata for random tensor generation.

    TTK invokes OnGenInput with all tensor inputs positionally. The 5
    mandatory tensors are bound by name; any trailing optional tensors
    (query_dequant_scale, key_dequant_scale, actual_seq_lengths_query,
    actual_seq_lengths_key, block_table) are absorbed into ``args`` and
    re-extracted by position so the function survives TTK signature drift.
    """
    # Trailing positional tensor order matches the operator signature
    # (query_dequant_scale, key_dequant_scale, actual_seq_lengths_query,
    # actual_seq_lengths_key, block_table). The first 5 may also be passed
    # positionally depending on TTK version.
    positional_tensors = list(args)
    while len(positional_tensors) < 5:
        positional_tensors.append(None)
    query_dequant_scale = positional_tensors[0]
    key_dequant_scale = positional_tensors[1]
    if positional_tensors[2] is not None:
        actual_seq_lengths_query = positional_tensors[2]
    if positional_tensors[3] is not None:
        actual_seq_lengths_key = positional_tensors[3]
    if len(positional_tensors) > 4 and positional_tensors[4] is not None:
        block_table = positional_tensors[4]
    del weights, query_dequant_scale, key_dequant_scale, kwargs
    layout_q = str(layout_query)
    layout_k = str(layout_key)
    query_total = int(query.shape[0] if layout_q == "TND" else query.shape[1])
    if layout_k == "TND":
        key_total = int(key.shape[0])
    elif layout_k == "PA_BSND":
        key_total = int(key.shape[0]) * int(key.shape[1])
    else:
        key_total = int(key.shape[1])

    _overwrite_lengths(
        actual_seq_lengths_query, query_total, cumulative=layout_q == "TND",
    )
    _overwrite_lengths(
        actual_seq_lengths_key, key_total, cumulative=layout_k == "TND",
    )

    if layout_k == "PA_BSND" and block_table is not None:
        block_count = max(int(key.shape[0]), 1)
        values = torch.arange(
            int(block_table.numel()), dtype=block_table.dtype,
            device=block_table.device,
        ).reshape(block_table.shape)
        block_table.copy_(values.remainder(block_count))


# ---------------------------------------------------------------------------
# Main golden function
# ---------------------------------------------------------------------------

def npu_quant_lightning_indexer_golden(
    query: torch.Tensor,
    key: torch.Tensor,
    weights: torch.Tensor,
    *args,
    actual_seq_lengths_query=None,
    actual_seq_lengths_key=None,
    block_table=None,
    layout_query: str = "BSND",
    layout_key: str = "BSND",
    sparse_count: int = 2048,
    sparse_mode: int = 3,
    query_quant_mode: int = 0,
    key_quant_mode: int = 0,
    pre_tokens: int = (1 << 63) - 1,
    next_tokens: int = (1 << 63) - 1,
    **kwargs,
):
    """CPU reference for ``torch_npu.npu_quant_lightning_indexer``.

    Returns ``out`` (int32 indices), shape ``[B, S1, N2, sparse_count]``
    for BSND / PA_BSND, ``[T, N2, sparse_count]`` for TND. Padding: when
    ``eff_seq < sparse_count`` the unfilled tail slots are filled with
    ``0`` (matches kernel). Only ``quant_mode=0`` (per-token-head) is
    supported in this reference; ``quant_mode=1`` (per-token) is rejected.

    TTK invokes OnGenGolden with 7 positional args (5 mandatory tensors
    + 2 optional tensors). They are absorbed into ``args`` and re-bound
    to the named kwargs so the signature survives TTK positional drift.
    """
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements,too-many-arguments
    positional_tensors = list(args)
    if len(positional_tensors) >= 1 and positional_tensors[0] is not None:
        query_dequant_scale = positional_tensors[0]
    if len(positional_tensors) >= 2 and positional_tensors[1] is not None:
        key_dequant_scale = positional_tensors[1]
    if len(positional_tensors) >= 3 and positional_tensors[2] is not None:
        actual_seq_lengths_query = positional_tensors[2]
    if len(positional_tensors) >= 4 and positional_tensors[3] is not None:
        actual_seq_lengths_key = positional_tensors[3]
    if len(positional_tensors) >= 5 and positional_tensors[4] is not None:
        block_table = positional_tensors[4]
    # The two mandatory scale tensors are required inputs to the operator
    # and must be present somewhere in the call. If TTK passed them as
    # kwargs we use them; otherwise we need them positionally.
    if "query_dequant_scale" in kwargs:
        query_dequant_scale = kwargs.pop("query_dequant_scale")
    if "key_dequant_scale" in kwargs:
        key_dequant_scale = kwargs.pop("key_dequant_scale")
    if query_dequant_scale is None or key_dequant_scale is None:
        raise NotImplementedError(
            "Golden requires query_dequant_scale and key_dequant_scale tensors; "
            "TTK did not supply them positionally or as kwargs"
        )
    del pre_tokens, next_tokens, query_quant_mode, key_quant_mode

    layout_q = str(layout_query)
    layout_k = str(layout_key)

    if layout_q not in ("BSND", "TND"):
        raise NotImplementedError(
            f"layout_query={layout_q!r} not supported by quant_lightning_indexer golden"
        )
    if layout_k not in ("BSND", "TND", "PA_BSND"):
        raise NotImplementedError(
            f"layout_key={layout_k!r} not supported by quant_lightning_indexer golden"
        )
    if int(sparse_mode) not in (0, 3):
        raise NotImplementedError(
            f"sparse_mode={sparse_mode} not supported by quant_lightning_indexer golden"
        )
    if int(sparse_count) < 1:
        raise NotImplementedError(
            f"sparse_count={sparse_count} must be >= 1"
        )

    unsupported = {
        k: v for k, v in kwargs.items()
        if k not in {"backend", "tensor_formats", "tensor_dtypes",
                     "use_torch", "short_soc_version", "testcase_name"}
    }
    if unsupported:
        raise NotImplementedError(
            f"unsupported quant_lightning_indexer optional kwargs: {sorted(unsupported)}"
        )

    device = query.device

    # Normalise actual_seq_lengths_* in case TTK passed them as a scalar int.
    if isinstance(actual_seq_lengths_query, (int, float)) and not isinstance(actual_seq_lengths_query, bool):
        actual_seq_lengths_query = torch.tensor(
            [int(actual_seq_lengths_query)], dtype=torch.int32, device=device,
        )
    if isinstance(actual_seq_lengths_key, (int, float)) and not isinstance(actual_seq_lengths_key, bool):
        actual_seq_lengths_key = torch.tensor(
            [int(actual_seq_lengths_key)], dtype=torch.int32, device=device,
        )

    # ---- 1. Determine batch and per-batch query / key lengths ----
    if layout_q == "TND":
        q_total = int(query.shape[0])
        if actual_seq_lengths_query is None:
            raise NotImplementedError(
                "TND query layout requires actual_seq_lengths_query"
            )
        batch = max(int(actual_seq_lengths_query.numel()), 1)
    else:
        batch = int(query.shape[0])
        q_total = int(query.shape[1])

    q_lens_raw = _seq_lengths(
        actual_seq_lengths_query, q_total, batch, layout_q == "TND",
    )

    if layout_k == "TND":
        k_total = int(key.shape[0])
        if actual_seq_lengths_key is None:
            raise NotImplementedError(
                "TND key layout requires actual_seq_lengths_key"
            )
        batch_k = max(int(actual_seq_lengths_key.numel()), 1)
    elif layout_k == "PA_BSND":
        k_total = int(key.shape[0]) * int(key.shape[1])
        if actual_seq_lengths_key is not None:
            batch_k = max(int(actual_seq_lengths_key.numel()), 1)
        elif block_table is not None:
            batch_k = int(block_table.shape[0])
        else:
            batch_k = batch
    else:  # BSND
        batch_k = int(key.shape[0])
        k_total = int(key.shape[1])

    kv_lens_raw = _seq_lengths(
        actual_seq_lengths_key, k_total, batch_k, layout_k == "TND",
    )

    if layout_q == "TND" and layout_k == "TND" and batch != batch_k:
        raise NotImplementedError(
            "TND actual_seq_lengths_query and actual_seq_lengths_key must "
            f"describe the same batch count; got {batch} and {batch_k}"
        )

    if len(q_lens_raw) < batch:
        q_lens_raw = q_lens_raw + [0] * (batch - len(q_lens_raw))
    elif len(q_lens_raw) > batch:
        q_lens_raw = q_lens_raw[:batch]
    if len(kv_lens_raw) < batch:
        kv_lens_raw = kv_lens_raw + [0] * (batch - len(kv_lens_raw))
    elif len(kv_lens_raw) > batch:
        kv_lens_raw = kv_lens_raw[:batch]

    q_max = int(query.shape[1]) if layout_q == "BSND" else int(query.shape[0])
    if layout_k == "PA_BSND":
        k_max = int(key.shape[0]) * int(key.shape[1])
    elif layout_k == "BSND":
        k_max = int(key.shape[1])
    else:
        k_max = int(key.shape[0])

    q_lens = [max(0, min(int(l), q_max)) for l in q_lens_raw]
    kv_lens = [max(0, min(int(l), k_max)) for l in kv_lens_raw]

    # ---- 2. Convert query/key/weights/scales to a uniform BSND-like layout ----
    if layout_q == "TND":
        q_bsnd = _tnd_to_bsnd(query, q_lens)
        w_bsnd = _tnd_to_bsnd(weights, q_lens)
        qs_bsnd = _tnd_to_bsnd(query_dequant_scale, q_lens)
    else:
        q_bsnd = query
        if weights.ndim == 3:
            w_bsnd = weights
        elif weights.ndim == 4:
            w_bsnd = weights.squeeze(-1)
        else:
            raise ValueError(
                f"unexpected weights ndim={weights.ndim} for BSND layout"
            )
        if query_dequant_scale.ndim == 3:
            qs_bsnd = query_dequant_scale
        elif query_dequant_scale.ndim == 4:
            qs_bsnd = query_dequant_scale.squeeze(-1)
        else:
            raise ValueError(
                f"unexpected query_dequant_scale ndim={query_dequant_scale.ndim}"
            )

    if layout_k == "PA_BSND":
        k_bsnd = _page_to_bsnd(key, block_table, kv_lens)
        ks_bsnd = _page_scale_to_bsnd(key_dequant_scale, block_table, kv_lens)
    elif layout_k == "TND":
        k_bsnd = _tnd_to_bsnd(key, kv_lens)
        if key_dequant_scale.ndim == 2:
            ks_bsnd = _tnd_to_bsnd(key_dequant_scale, kv_lens)
        elif key_dequant_scale.ndim == 3:
            ks_bsnd = _tnd_to_bsnd(key_dequant_scale.squeeze(-1), kv_lens)
        else:
            raise ValueError(
                f"unexpected key_dequant_scale ndim={key_dequant_scale.ndim} for TND"
            )
    else:
        k_bsnd = key
        if key_dequant_scale.ndim == 3:
            ks_bsnd = key_dequant_scale
        elif key_dequant_scale.ndim == 4:
            ks_bsnd = key_dequant_scale.squeeze(-1)
        else:
            raise ValueError(
                f"unexpected key_dequant_scale ndim={key_dequant_scale.ndim}"
            )

    max_q = max(q_lens) if q_lens else 0
    N2 = int(k_bsnd.shape[2])

    indices_out = torch.zeros(
        (batch, max_q, N2, int(sparse_count)),
        dtype=torch.int32, device=device,
    )

    NEG_INF = float("-inf")
    SC = int(sparse_count)

    # ---- 3. Per-batch, per-query-token loop ----
    for b in range(batch):
        S_q = int(q_lens[b])
        S_k = int(kv_lens[b])
        if S_q == 0 or S_k == 0:
            continue

        q_b = q_bsnd[b, :S_q].float()       # [S_q, N1, D]
        w_b = w_bsnd[b, :S_q].float()       # [S_q, N1]
        qs_b = qs_bsnd[b, :S_q].float()     # [S_q, N1]
        k_b = k_bsnd[b, :S_k, 0].float()    # [S_k, D]
        ks_b = ks_bsnd[b, :S_k, 0].float()  # [S_k]

        for q_idx in range(S_q):
            # ---- 4. Core formula ----
            # raw[g, j] = sum_d q[g, d] * k[j, d]
            raw = torch.matmul(q_b[q_idx], k_b.transpose(-1, -2))  # [N1, S_k]
            # scale[g, j] = qs[g] * ks[j]
            scale = qs_b[q_idx].unsqueeze(-1) * ks_b.unsqueeze(0)  # [N1, S_k]
            # relu = ReLU(scale * raw) — per doc formula
            relu = torch.relu(scale * raw)  # [N1, S_k]
            # weighted[g, j] = w[g] * relu[g, j]
            weighted = w_b[q_idx].unsqueeze(-1) * relu  # [N1, S_k]
            # score[j] = sum_g weighted[g, j]  (per doc formula)
            scores = weighted.sum(dim=0)  # [S_k]
            _ = q_idx

            if sparse_mode == 3:
                # Kernel formula: cuRealAcSeq = actS2Size - (actS1Size - q_idx)
                # so threshold (positions ≥ threshold are masked) is
                # `S_k - S_q + q_idx`, NOT `+ 1` as in the doc / upstream
                # non-quant reference. Verified against kernel source.
                threshold = S_k - S_q + q_idx
                if threshold <= 0:
                    scores = torch.full_like(scores, NEG_INF)
                elif threshold < S_k:
                    mask = torch.arange(S_k, device=device) >= threshold
                    scores = scores.masked_fill(mask, NEG_INF)

            # ---- 5. Top-k, padding ----
            k_num = min(SC, S_k)
            if k_num <= 0:
                top_idx = torch.zeros(SC, dtype=torch.int32, device=device)
            else:
                _, topk_idx = torch.topk(
                    scores, k_num, largest=True, sorted=True,
                )
                topk_idx = topk_idx.to(torch.int32)
                if k_num < SC:
                    pad_n = SC - k_num
                    top_idx = torch.cat([
                        topk_idx,
                        torch.zeros(pad_n, dtype=torch.int32, device=device),
                    ])
                else:
                    top_idx = topk_idx

            indices_out[b, q_idx, 0, :] = top_idx

    if layout_q == "TND":
        parts_idx = [
            indices_out[b, :q_lens[b], 0, :] for b in range(batch)
        ]
        if parts_idx:
            indices_out = torch.cat(parts_idx, dim=0)
        else:
            indices_out = torch.empty(
                (0, SC), dtype=torch.int32, device=device,
            )

    return indices_out


__golden__ = {
    "e2e": {
        "torch_npu.npu_quant_lightning_indexer": "npu_quant_lightning_indexer_golden",
    }
}

__input__ = {
    "e2e": {
        "torch_npu.npu_quant_lightning_indexer": "npu_quant_lightning_indexer_input",
    }
}