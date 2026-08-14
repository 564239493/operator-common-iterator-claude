"""CPU Golden for the ACLNN ScatterPaKvCache functional baseline.

The current ACLNN TTK materializer deliberately projects generated cases to
the documented A2 ``cacheMode=Norm, scatterMode=None`` scenario.  This Golden
implements that scenario exactly and rejects compression, NZ, NHSD and Nct
instead of comparing against an approximation with different semantics.
"""
from __future__ import annotations

from typing import Any

import torch


def _scatter_norm(
    source: torch.Tensor,
    cache: torch.Tensor,
    slot_mapping: torch.Tensor,
) -> torch.Tensor:
    """Scatter flattened source tokens into [block, offset, head, dim]."""
    if source.ndim not in (3, 4):
        raise ValueError(
            f"Norm ScatterPaKvCache Golden requires rank-3/4 source, "
            f"got rank {source.ndim}"
        )
    if cache.ndim != 4:
        raise ValueError(
            f"Norm ScatterPaKvCache Golden requires rank-4 cache, "
            f"got rank {cache.ndim}"
        )
    tokens = source.reshape(-1, source.shape[-2], source.shape[-1])
    slots = slot_mapping.reshape(-1).to(torch.int64)
    if slots.numel() != tokens.shape[0]:
        raise ValueError(
            f"slotMapping length {slots.numel()} must equal token count "
            f"{tokens.shape[0]}"
        )
    if tuple(tokens.shape[1:]) != tuple(cache.shape[2:]):
        raise ValueError(
            f"source head shape {tuple(tokens.shape[1:])} must equal cache "
            f"head shape {tuple(cache.shape[2:])}"
        )

    result = cache.clone()
    block_size = int(cache.shape[1])
    capacity = int(cache.shape[0]) * block_size
    for token_index, raw_slot in enumerate(slots.tolist()):
        slot = int(raw_slot)
        # The operator uses a negative slot as an ignored/padding token in
        # compatible non-compression paths.
        if slot < 0:
            continue
        if slot >= capacity:
            raise ValueError(
                f"slotMapping[{token_index}]={slot} exceeds cache capacity "
                f"{capacity}"
            )
        block_index, block_offset = divmod(slot, block_size)
        result[block_index, block_offset].copy_(tokens[token_index])
    return result


def scatter_pa_kv_cache_golden(
    key: torch.Tensor,
    keyCacheRef: torch.Tensor,
    slotMapping: torch.Tensor,
    value: torch.Tensor,
    valueCacheRef: torch.Tensor,
    compressLensOptional: torch.Tensor | None = None,
    compressSeqOffsetOptional: torch.Tensor | None = None,
    seqLensOptional: torch.Tensor | None = None,
    cacheModeOptional: str | None = "Norm",
    scatterModeOptional: str | None = "None",
    stridesOptional: list[int] | tuple[int, ...] | None = None,
    offsetsOptional: list[int] | tuple[int, ...] | None = None,
    **metadata: Any,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return expected in-place key/value cache contents."""
    del stridesOptional, offsetsOptional, metadata
    if cacheModeOptional not in (None, "Norm"):
        raise NotImplementedError(
            "ScatterPaKvCache Golden currently supports cacheMode Norm only"
        )
    if scatterModeOptional not in (None, "None"):
        raise NotImplementedError(
            "ScatterPaKvCache Golden currently supports scatterMode None only"
        )
    if any(
        item is not None
        for item in (
            compressLensOptional,
            compressSeqOffsetOptional,
            seqLensOptional,
        )
    ):
        raise NotImplementedError(
            "ScatterPaKvCache Golden does not approximate compression modes"
        )
    if value.ndim == 0 or valueCacheRef.ndim == 0:
        raise NotImplementedError(
            "ScatterPaKvCache Golden baseline requires key and value outputs"
        )

    return (
        _scatter_norm(key, keyCacheRef, slotMapping),
        _scatter_norm(value, valueCacheRef, slotMapping),
    )


__golden__ = {
    "aclnn": {
        "aclnnScatterPaKvCache": "scatter_pa_kv_cache_golden",
    }
}
