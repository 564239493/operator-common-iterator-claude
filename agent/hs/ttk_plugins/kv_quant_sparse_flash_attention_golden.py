"""CPU Golden wrapper for torch_npu.npu_kv_quant_sparse_flash_attention.

Thin pass-through to the official ASCEND ``ai_infra_kv_quant_sparse_flash_attention``
reference (cann-bench/bench_lab/omni-ops_bench/ai_infra_kv_quant_sparse_flash_attention/golden.py).

The official function is the canonical torch golden maintained by the Ascend
team. The signature is API-compatible with torch_npu.npu_kv_quant_sparse_flash_attention,
so this plugin merely re-exports it under the TTK ``__golden__`` registry.

Key correctness facts (from golden.py:969-1037):
* Query last dim is split at 512: ``q_base = query[..., :512]``,
  ``q_rope = query[..., 512:]`` (rope stays in fp32 for matmul).
* Key last dim is ``value_dim (512) + rope_bytes (rope_head_dim*2) + scale_bytes``.
* Value is computed as ``key_main[..., : min(value.shape[-1], q_base.shape[-1])]``
  -- i.e. the dequantized K is sliced to produce V (MLA-absorb semantics;
  this is the root cause of the prior 0%-accuracy handwritten golden).

The official function supports BSND / TND / PA_BSND layouts, attention_mode=2,
quant_scale_repo_mode=1, tile_size=128, rope_head_dim=64, sparse_mode in {0, 3}.
"""
from __future__ import annotations

import importlib.util
import os
import sys


# Ensure the operators-src repo is importable so we can load the official
# golden.py. The repo is a sibling of this project; resolve via an absolute
# path instead of relying on PYTHONPATH so the plugin remains self-contained.
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_OPS_SRC_ROOT = os.path.join(_PROJECT_ROOT, "operators-src")
if _OPS_SRC_ROOT not in sys.path:
    sys.path.insert(0, _OPS_SRC_ROOT)

import torch  # noqa: E402  (after sys.path bootstrap)
import torch_npu  # noqa: E402

torch_npu.npu.config.allow_internal_format = True

# Load the official Ascend-maintained torch golden via importlib because
# the operators-src/cann-bench tree ships without __init__.py files. The
# module only depends on torch + typing, so direct spec loading is safe.
_GOLDEN_PATH = os.path.join(
    _OPS_SRC_ROOT,
    "cann-bench",
    "bench_lab",
    "omni-ops_bench",
    "ai_infra_kv_quant_sparse_flash_attention",
    "golden.py",
)
_spec = importlib.util.spec_from_file_location(
    "official_kv_quant_sfa_golden", _GOLDEN_PATH
)
_official_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_official_mod)
_official_golden = _official_mod.ai_infra_kv_quant_sparse_flash_attention


def kv_quant_sparse_flash_attention_golden(
    query: torch.Tensor,
    key: torch.Tensor,
    value: torch.Tensor,
    sparse_indices: torch.Tensor,
    scale_value: float,
    key_quant_mode: int = 2,
    value_quant_mode: int = 2,
    *,
    key_dequant_scale=None,
    value_dequant_scale=None,
    block_table=None,
    actual_seq_lengths_query=None,
    actual_seq_lengths_kv=None,
    key_sink=None,
    value_sink=None,
    sparse_block_size: int = 1,
    layout_query: str = "BSND",
    layout_kv: str = "BSND",
    sparse_mode: int = 3,
    pre_tokens: int = (1 << 63) - 1,
    next_tokens: int = (1 << 63) - 1,
    attention_mode: int = 2,
    quant_scale_repo_mode: int = 1,
    tile_size: int = 128,
    rope_head_dim: int = 64,
    **kwargs,
) -> torch.Tensor:
    """Forward every arg to the official golden; this is the canonical reference.

    No approximation, no fallback: if the official function cannot run the
    configuration (e.g. unsupported attention_mode / tile_size), it raises
    and TTK classifies the case as ``unsupported`` / ``golden_failure``.
    """
    return _official_golden(
        query=query,
        key=key,
        value=value,
        sparse_indices=sparse_indices,
        scale_value=scale_value,
        key_quant_mode=key_quant_mode,
        value_quant_mode=value_quant_mode,
        key_dequant_scale=key_dequant_scale,
        value_dequant_scale=value_dequant_scale,
        block_table=block_table,
        actual_seq_lengths_query=actual_seq_lengths_query,
        actual_seq_lengths_kv=actual_seq_lengths_kv,
        key_sink=key_sink,
        value_sink=value_sink,
        sparse_block_size=sparse_block_size,
        layout_query=layout_query,
        layout_kv=layout_kv,
        sparse_mode=sparse_mode,
        pre_tokens=pre_tokens,
        next_tokens=next_tokens,
        attention_mode=attention_mode,
        quant_scale_repo_mode=quant_scale_repo_mode,
        tile_size=tile_size,
        rope_head_dim=rope_head_dim,
        **kwargs,
    )


__golden__ = {
    "e2e": {
        "torch_npu.npu_kv_quant_sparse_flash_attention": "kv_quant_sparse_flash_attention_golden",
    }
}