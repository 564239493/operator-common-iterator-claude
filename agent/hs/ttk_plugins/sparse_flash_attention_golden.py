"""CPU Golden for torch_npu.npu_sparse_flash_attention.

Implements the sparse flash attention formula:
  softmax(Q @ K_tilde^T * scale_value) @ V_tilde

Where K_tilde, V_tilde are gathered from key/value at positions given by
sparse_indices. Supports BSND, TND, and PA_BSND layouts, MLA-absorb
(attention_mode=2), and sparse_mode 0 (no causal mask) / 3 (rightDownCausal).
"""
from __future__ import annotations

from typing import Optional, Sequence

import torch


# ---------------------------------------------------------------------------
# Helpers (shared logic with the official GQA golden)
# ---------------------------------------------------------------------------

def _ceil(value):
    ivalue = int(value)
    return ivalue if float(value) == float(ivalue) else ivalue + 1


def _seq_lengths(
    lengths, total, batch, is_tnd: bool,
) -> Sequence[int]:
    """Normalise length specs into a plain per-batch list."""
    if lengths is None:
        if is_tnd:
            if total is None or batch is None:
                return []
            base = max(int(total), 0) // max(int(batch), 1)
            out = [base] * max(int(batch), 1)
            remainder = max(int(total), 0) - base * max(int(batch), 1)
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

    # TND prefix-sums  ->  per-batch lengths
    if (
        len(lengths) == int(batch or 1)
        and all(a <= b for a, b in zip(lengths, lengths[1:]))
        and lengths[-1] == int(total or 0)
    ):
        prev = 0
        result = []
        for item in lengths:
            result.append(max(int(item) - prev, 0))
            prev = int(item)
        return result
    if sum(lengths) == int(total or 0):
        return lengths
    return lengths


def _tnd_to_bsnd(x: torch.Tensor, lengths: Sequence[int]) -> torch.Tensor:
    """TND [T,N,D] -> BSND [B,S,N,D]."""
    batch = len(lengths)
    max_s = max(lengths) if lengths else 0
    out = torch.zeros(
        (batch, max_s, x.shape[-2], x.shape[-1]),
        dtype=x.dtype, device=x.device,
    )
    start = 0
    for b, seq_len in enumerate(lengths):
        out[b, :seq_len] = x[start : start + seq_len]
        start += seq_len
    return out


def _page_to_bsnd(
    x: torch.Tensor,
    block_table: Optional[torch.Tensor],
    lengths: Sequence[int],
) -> torch.Tensor:
    """PA_BSND [block_num,block_size,N,D] -> BSND via block_table."""
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


def _bsnd_to_tnd(x: torch.Tensor, lengths: Sequence[int]) -> torch.Tensor:
    """BSND [B,S,N,D] -> TND [T,N,D]."""
    parts = [x[b, : int(seq_len)] for b, seq_len in enumerate(lengths)]
    return torch.cat(parts, dim=0) if parts else torch.empty(
        (0, x.shape[-2], x.shape[-1]), dtype=x.dtype, device=x.device,
    )


def _bnsd_to_tnd(x: torch.Tensor, lengths: Sequence[int]) -> torch.Tensor:
    """BNSD [B,N,S,D] -> TND [T,N,D]."""
    parts = []
    for b, seq_len in enumerate(lengths):
        parts.append(x[b, :, : int(seq_len), :].permute(1, 0, 2))
    if parts:
        return torch.cat(parts, dim=0)
    return torch.empty((0, x.shape[-2], x.shape[-1]), dtype=x.dtype, device=x.device)


def _tnd_to_bnsd(x: torch.Tensor, lengths: Sequence[int]) -> torch.Tensor:
    """TND [T,N,D] -> BNSD [B,N,S,D] (N,S,D order)."""
    batch = len(lengths)
    max_s = max(lengths) if lengths else 0
    out = torch.zeros(
        (batch, x.shape[1], max_s, x.shape[2]),
        dtype=x.dtype, device=x.device,
    )
    start = 0
    for b, seq_len in enumerate(lengths):
        out[b, :, :seq_len, :] = x[start : start + seq_len].permute(1, 0, 2)
        start += seq_len
    return out


def _gather_kv_positions(
    indices: torch.Tensor, block_size: int, kv_len: int, device,
) -> torch.Tensor:
    """Expand sparse block indices into flat token positions.

    Each sparse block ID is expanded into ``block_size`` consecutive token
    positions.  The NPU kernel processes every entry (duplicates included),
    so the golden must mirror this exactly — no dedup, no implicit truncation.
    Only ``-1`` terminates early.
    """
    positions: list[int] = []
    for sparse_id in indices.detach().cpu().tolist():
        sparse_id = int(sparse_id)
        if sparse_id == -1:
            break
        begin = sparse_id * block_size
        end = min(begin + block_size, kv_len)
        if begin < kv_len:
            positions.extend(range(begin, end))
    return torch.tensor(positions, dtype=torch.long, device=device)


def _apply_right_down_causal_mask(
    scores: torch.Tensor,
    q_len: int,
    kv_len: int,
    indices: torch.Tensor,
    q_idx: int,
    block_size: int,
):
    """sparse_mode=3 causal mask. Returns (masked_scores, all_masked_bool)."""
    if scores.shape[-1] == 0:
        return scores, True
    tail_block = _ceil(kv_len / block_size)
    tail_len = kv_len % block_size or block_size
    threshold = kv_len - q_len + q_idx + 1
    offset = 0
    masked = 0
    for sparse_id in indices.detach().cpu().tolist():
        sparse_id = int(sparse_id)
        if sparse_id == -1:
            break
        begin = sparse_id * block_size
        block_len = block_size if sparse_id != tail_block - 1 else tail_len
        end = begin + block_len
        if begin < threshold and end <= threshold:
            offset += block_len
            continue
        if end > threshold:
            local = 0 if threshold <= begin else threshold - begin
            scores[:, offset + local : offset + block_len] = torch.finfo(scores.dtype).min
            masked += block_len - local
        offset += block_len
    return scores, masked == scores.shape[-1]


# ---------------------------------------------------------------------------
# Main golden function
# ---------------------------------------------------------------------------

def sparse_flash_attention_golden(
    query: torch.Tensor,
    key: torch.Tensor,
    value: torch.Tensor,
    sparse_indices: torch.Tensor,
    scale_value: float,
    *,
    block_table: Optional[torch.Tensor] = None,
    actual_seq_lengths_query=None,
    actual_seq_lengths_kv=None,
    query_rope: Optional[torch.Tensor] = None,
    key_rope: Optional[torch.Tensor] = None,
    sparse_block_size: int = 1,
    layout_query: str = "BSND",
    layout_kv: str = "BSND",
    sparse_mode: int = 3,
    pre_tokens: int = 9223372036854775807,
    next_tokens: int = 9223372036854775807,
    attention_mode: int = 0,
    return_softmax_lse: bool = False,
    **kwargs,
):
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    del pre_tokens, next_tokens, kwargs  # currently unused

    # ---- Step 1: determine batch and sequence lengths ----
    layout_q = str(layout_query)
    layout_k = str(layout_kv)

    if layout_q == "BSND":
        batch = query.shape[0]
        q_dim_idx = 2  # S is dim 1 for BSND
    else:
        batch = 1
        q_dim_idx = 1  # S = 0? Actually TND: query is [T, N, D]

    # Heuristic: for TND, batch is inferred from actual_seq_lengths_query
    if actual_seq_lengths_query is not None:
        if isinstance(actual_seq_lengths_query, torch.Tensor):
            batch = max(int(actual_seq_lengths_query.numel()), 1)
        elif hasattr(actual_seq_lengths_query, '__len__'):
            batch = max(len(actual_seq_lengths_query), 1)

    q_lens = _seq_lengths(
        actual_seq_lengths_query,
        query.shape[0] if layout_q == "TND" else query.shape[1],
        batch,
        layout_q == "TND",
    )
    kv_lens = _seq_lengths(
        actual_seq_lengths_kv,
        key.shape[0] if layout_k == "TND" else key.shape[1],
        batch,
        layout_k == "TND",
    )

    # ---- Step 2: convert all inputs to a uniform internal format ----
    # We work in BNSD: [B, N, S, D] (batch, heads, seq, dim)

    if layout_q == "TND":
        q_bsnd = _tnd_to_bsnd(query, q_lens)          # [B,Sq,Nq,D]
        idx_bsnd = _tnd_to_bsnd(sparse_indices, q_lens)  # [B,Sq,Nkv,sparse_size]
    else:
        q_bsnd = query                                 # [B,Sq,Nq,D]
        idx_bsnd = sparse_indices                      # [B,Sq,Nkv,sparse_size]

    if layout_k.startswith("PA_"):
        k_bsnd = _page_to_bsnd(key, block_table, kv_lens)     # [B,Skv,Nkv,D]
        v_bsnd = _page_to_bsnd(value, block_table, kv_lens)
    elif layout_k == "TND":
        k_bsnd = _tnd_to_bsnd(key, kv_lens)
        v_bsnd = _tnd_to_bsnd(value, kv_lens)
    else:
        k_bsnd = key
        v_bsnd = value

    # Handle rope tensors similarly
    if query_rope is not None and isinstance(query_rope, torch.Tensor):
        if layout_q == "TND":
            qr_bsnd = _tnd_to_bsnd(query_rope, q_lens)
        else:
            qr_bsnd = query_rope
    else:
        qr_bsnd = None

    if key_rope is not None and isinstance(key_rope, torch.Tensor):
        if layout_k.startswith("PA_"):
            kr_bsnd = _page_to_bsnd(key_rope, block_table, kv_lens)
        elif layout_k == "TND":
            kr_bsnd = _tnd_to_bsnd(key_rope, kv_lens)
        else:
            kr_bsnd = key_rope
    else:
        kr_bsnd = None

    # ---- Step 2b: MLA-absorb (attention_mode=2) ----
    # Concatenate nope (from query/key) with rope (from query_rope/key_rope)
    if attention_mode == 2:
        if qr_bsnd is not None and qr_bsnd.numel() > 0:
            q_bsnd = torch.cat([q_bsnd, qr_bsnd], dim=-1)
        if kr_bsnd is not None and kr_bsnd.numel() > 0:
            k_bsnd = torch.cat([k_bsnd, kr_bsnd], dim=-1)

    # ---- Step 3: permute to BNSD for per-head processing ----
    q_bnsd = q_bsnd.permute(0, 2, 1, 3).contiguous()   # [B,Nq,Sq,Dq]
    k_bnsd = k_bsnd.permute(0, 2, 1, 3).contiguous()   # [B,Nkv,Skv,Dk]
    v_bnsd = v_bsnd.permute(0, 2, 1, 3).contiguous()   # [B,Nkv,Skv,Dv]
    idx_bns = idx_bsnd.permute(0, 2, 1, 3).contiguous() # [B,Nkv,Sq,sparse_size]

    num_heads = q_bnsd.shape[1]   # Nq
    num_kv_heads = k_bnsd.shape[1]  # Nkv (always 1 for this op)
    group = num_heads // num_kv_heads

    # Output buffers
    max_q = max(q_lens) if q_lens else 0
    value_dim = v_bnsd.shape[-1]
    out = torch.zeros(
        (batch, num_heads, max_q, value_dim),
        dtype=query.dtype if not return_softmax_lse else torch.float32,
        device=query.device,
    )
    softmax_max = torch.zeros(
        (batch, num_kv_heads, max_q, num_heads),
        dtype=torch.float32,
        device=query.device,
    )
    softmax_sum = torch.zeros(
        (batch, num_kv_heads, max_q, num_heads),
        dtype=torch.float32,
        device=query.device,
    )

    scale = float(scale_value)

    # ---- Step 4: per-batch, per-head, per-query-position loop ----
    for b in range(batch):
        for kv_head in range(num_kv_heads):
            head_start = kv_head * group
            head_end = (kv_head + 1) * group
            for q_idx in range(q_lens[b]):
                indices = idx_bns[b, kv_head, q_idx]

                # Gather sparse KV tokens
                positions = _gather_kv_positions(
                    indices, sparse_block_size, kv_lens[b], query.device,
                )
                if positions.numel() == 0:
                    # No valid sparse positions -> zero / inf output
                    softmax_max[b, kv_head, q_idx, head_start:head_end] = float('-inf') if return_softmax_lse else 0.0
                    softmax_sum[b, kv_head, q_idx, head_start:head_end] = 0.0
                    continue

                q_cur = q_bnsd[b, head_start:head_end, q_idx, :].float()  # [group, Dq]
                k_sparse = k_bnsd[b, kv_head].index_select(0, positions).float()  # [num_pos, Dk]
                v_sparse = v_bnsd[b, kv_head].index_select(0, positions).float()  # [num_pos, Dv]

                scores = torch.matmul(q_cur, k_sparse.transpose(0, 1)) * scale  # [group, num_pos]

                all_masked = False
                if sparse_mode == 3:
                    scores, all_masked = _apply_right_down_causal_mask(
                        scores, q_lens[b], kv_lens[b], indices, q_idx,
                        sparse_block_size,
                    )

                if all_masked:
                    probs = torch.zeros_like(scores)
                else:
                    probs = torch.softmax(scores.float(), dim=-1)  # [group, num_pos]

                if return_softmax_lse:
                    softmax_max[b, kv_head, q_idx, head_start:head_end] = scores.max(dim=-1).values.float()
                    softmax_sum[b, kv_head, q_idx, head_start:head_end] = probs.sum(dim=-1)

                out[b, head_start:head_end, q_idx, :] = (
                    torch.matmul(probs, v_sparse).to(query.dtype)
                )

    # ---- Step 5: convert back to original layout ----
    if layout_q == "TND":
        attention_out = _bnsd_to_tnd(out, q_lens)
        sm_out = _bnsd_to_tnd(softmax_max, q_lens)
        ss_out = _bnsd_to_tnd(softmax_sum, q_lens)
    else:
        attention_out = out.permute(0, 2, 1, 3).contiguous()  # [B,Sq,Nq,D]
        sm_out = softmax_max  # [B,Nkv,Sq,Nq]
        ss_out = softmax_sum

    if not return_softmax_lse:
        # Return placeholders matching the expected output types
        sm_out = torch.empty((0,), dtype=torch.float32, device=query.device)
        ss_out = torch.empty((0,), dtype=torch.float32, device=query.device)

    return attention_out, sm_out, ss_out


# ---------------------------------------------------------------------------
# TTK registration
# ---------------------------------------------------------------------------

__golden__ = {
    "e2e": {
        "torch_npu.npu_sparse_flash_attention": "sparse_flash_attention_golden",
    }
}
