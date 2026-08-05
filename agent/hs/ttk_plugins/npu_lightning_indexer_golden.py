"""CPU Golden for torch_npu.npu_lightning_indexer.

Implements the lightning indexer formula documented in
``operator_docs/hs/torch_npu-npu_lightning_indexer.md`` and matches the
upstream CPU golden reference at
``operators-src/cann-recipes-infer/ops/pypto/examples/goldens/gen_lightning_indexer_topk.py``
(function ``indexer_topk_compute``).

For each query token, the inner expression collapses to one scalar per
key position::

    score[j] = sum_g  W[g] * ReLU( sum_d  Q[g, d] * K[j, d] )

Top-k over the S_k key-position scores gives ``sparse_indices`` (int32)
and ``sparse_values`` (bf16/fp16). When ``eff_seq < sparse_count``
(where ``eff_seq`` is the effective number of visible key positions for
the current query token), the unfilled slots are padded with ``0`` for
indices and ``0`` for values to match the upstream kernel.

Supports BSND / TND / PA_BSND layouts for the key side, BSND / TND for
the query side, sparse_mode 0 (no causal mask) and 3 (rightDownCausal).

Rejected modes raise ``NotImplementedError`` rather than approximating an
unsupported branch.
"""
from __future__ import annotations

import torch
import torch_npu

torch_npu.npu.config.allow_internal_format = True


# ---------------------------------------------------------------------------
# Layout helpers (similar to those in sparse_flash_attention_golden; inlined
# because there is no shared agent/hs/ttk_plugins/common/ directory yet).
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
    """Write valid actual-sequence content into an optional 1-D tensor."""
    if tensor is None:
        return
    count = int(tensor.numel())
    if count <= 0:
        return
    if cumulative:
        values = _balanced_prefix_sum(total, count)
    else:
        values = [max(int(total), 0)] * count
    tensor.copy_(torch.tensor(values, dtype=tensor.dtype, device=tensor.device))


def npu_lightning_indexer_input(
    query: torch.Tensor,
    key: torch.Tensor,
    weights: torch.Tensor,
    *,
    actual_seq_lengths_query=None,
    actual_seq_lengths_key=None,
    block_table=None,
    layout_query: str = "BSND",
    layout_key: str = "BSND",
    **kwargs,
):
    """Materialize sequence metadata that random tensor generation cannot satisfy."""
    del weights, kwargs
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


# ---------------------------------------------------------------------------
# Main golden function (matches upstream gen_lightning_indexer_topk.py)
# ---------------------------------------------------------------------------

def npu_lightning_indexer_golden(
    query: torch.Tensor,
    key: torch.Tensor,
    weights: torch.Tensor,
    *,
    actual_seq_lengths_query=None,
    actual_seq_lengths_key=None,
    block_table=None,
    layout_query: str = "BSND",
    layout_key: str = "BSND",
    sparse_count: int = 2048,
    sparse_mode: int = 3,
    pre_tokens: int = (1 << 63) - 1,
    next_tokens: int = (1 << 63) - 1,
    return_value: bool = False,
    **kwargs,
):
    """CPU reference for ``torch_npu.npu_lightning_indexer``.

    Returns ``(sparse_indices, sparse_values)`` — both share the same
    shape: ``[B, S, N2, sparse_count]`` for BSND/PA_BSND, ``[T, N2,
    sparse_count]`` for TND. ``N2`` is always 1 per the operator's
    constraint. Padding logic:

    * If ``eff_seq >= sparse_count``: top-k picks fill ``[0:sparse_count]``.
    * If ``eff_seq < sparse_count``: top-k fills ``[0:eff_seq]``; remaining
      slots ``[eff_seq:sparse_count]`` are padded with ``0`` for indices
      and ``0`` for values (matches upstream). upstream comment notes an
      alternative ``indices = -1`` form, but the kernel currently
      returns ``0`` and the precision check is calibrated accordingly.
    """
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    del pre_tokens, next_tokens  # doc restricts these to 2^63-1; never used here

    layout_q = str(layout_query)
    layout_k = str(layout_key)

    if layout_q not in ("BSND", "TND"):
        raise NotImplementedError(
            f"layout_query={layout_q!r} not supported by lightning_indexer golden"
        )
    if layout_k not in ("BSND", "TND", "PA_BSND"):
        raise NotImplementedError(
            f"layout_key={layout_k!r} not supported by lightning_indexer golden"
        )
    if sparse_mode not in (0, 3):
        raise NotImplementedError(
            f"sparse_mode={sparse_mode} not supported by lightning_indexer golden"
        )
    if int(sparse_count) < 1:
        raise NotImplementedError(
            f"sparse_count={sparse_count} must be >= 1"
        )
    _ = return_value

    unsupported = {
        k: v for k, v in kwargs.items()
        if k not in {"backend", "tensor_formats", "tensor_dtypes",
                     "use_torch", "short_soc_version", "testcase_name"}
    }
    if unsupported:
        raise NotImplementedError(
            f"unsupported lightning_indexer optional kwargs: {sorted(unsupported)}"
        )

    device = query.device

    # ---- 1. Determine batch size and per-batch query / key lengths ----
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

    _ = q_total, k_total

    # ---- 2. Convert query/key/weights to a uniform BSND-like layout ----
    if layout_q == "TND":
        q_bsnd = _tnd_to_bsnd(query, q_lens)
        w_bsnd = _tnd_to_bsnd(weights, q_lens)
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

    if layout_k == "PA_BSND":
        k_bsnd = _page_to_bsnd(key, block_table, kv_lens)
    elif layout_k == "TND":
        k_bsnd = _tnd_to_bsnd(key, kv_lens)
    else:
        k_bsnd = key

    max_q = max(q_lens) if q_lens else 0
    N2 = int(k_bsnd.shape[2])

    indices_out = torch.zeros(
        (batch, max_q, N2, int(sparse_count)),
        dtype=torch.int32, device=device,
    )
    values_out = torch.zeros(
        (batch, max_q, N2, int(sparse_count)),
        dtype=query.dtype, device=device,
    )

    NEG_INF = float("-inf")
    SC = int(sparse_count)

    # ---- 3. Per-batch, per-query-token loop (mirrors upstream) ----
    for b in range(batch):
        S_q = int(q_lens[b])
        S_k = int(kv_lens[b])
        if S_q == 0 or S_k == 0:
            continue

        q_b = q_bsnd[b, :S_q].float()
        w_b = w_bsnd[b, :S_q].float()
        k_b = k_bsnd[b, :S_k, 0].float()

        for q_idx in range(S_q):
            # Upstream uses casual_offset = s1 - s_idx - 1 for PA per-block
            # walk; collapsed for non-PA layouts (S_k is global). Match
            # threshold = S_k - S_q + q_idx + 1 for sparse_mode=3.
            scores = torch.matmul(q_b[q_idx], k_b.transpose(-1, -2))
            scores = torch.relu(scores)
            scores = scores * w_b[q_idx].unsqueeze(-1)
            scores = scores.sum(dim=0)  # [S_k]

            if sparse_mode == 3:
                threshold = S_k - S_q + q_idx + 1
                if threshold <= 0:
                    scores = torch.full_like(scores, NEG_INF)
                elif threshold < S_k:
                    mask = torch.arange(S_k, device=device) >= threshold
                    scores = scores.masked_fill(mask, NEG_INF)

            # Match upstream: k_num = min(sparse_count, S_k); padding
            # indices = 0, padding values = 0.
            k_num = min(SC, S_k)
            if k_num <= 0:
                top_idx = torch.zeros(SC, dtype=torch.int32, device=device)
                top_val = torch.zeros(SC, dtype=query.dtype, device=device)
            else:
                topk_vals, topk_idx = torch.topk(
                    scores, k_num, largest=True, sorted=True,
                )
                topk_idx = topk_idx.to(torch.int32)
                if k_num < SC:
                    pad_n = SC - k_num
                    top_idx = torch.cat([
                        topk_idx,
                        torch.zeros(pad_n, dtype=torch.int32, device=device),
                    ])
                    top_val = torch.cat([
                        topk_vals.to(query.dtype),
                        torch.zeros(pad_n, dtype=query.dtype, device=device),
                    ])
                else:
                    top_idx = topk_idx
                    top_val = topk_vals.to(query.dtype)

            indices_out[b, q_idx, 0, :] = top_idx
            values_out[b, q_idx, 0, :] = top_val

    if layout_q == "TND":
        parts_idx = [
            indices_out[b, :q_lens[b], 0, :] for b in range(batch)
        ]
        parts_val = [
            values_out[b, :q_lens[b], 0, :] for b in range(batch)
        ]
        if parts_idx:
            indices_out = torch.cat(parts_idx, dim=0)
            values_out = torch.cat(parts_val, dim=0)
        else:
            indices_out = torch.empty(
                (0, SC), dtype=torch.int32, device=device,
            )
            values_out = torch.empty(
                (0, SC), dtype=query.dtype, device=device,
            )

    return indices_out, values_out


__golden__ = {
    "e2e": {
        "torch_npu.npu_lightning_indexer": "npu_lightning_indexer_golden",
    }
}

__input__ = {
    "e2e": {
        "torch_npu.npu_lightning_indexer": "npu_lightning_indexer_input",
    }
}
