"""Constraint-first TTK E2E cases for supported torch_npu inference operators.

These profiles intentionally start from examples published in op-plugin docs.
They are deterministic smoke/accuracy baselines, not independent random samples.
Additional coverage should be added as complete scenario profiles so correlated
shape, dtype, layout and optional-input constraints remain valid.
"""
from __future__ import annotations

import csv
import json
import math
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any


HEADERS = (
    "testcase_name", "api_name", "tensor_view_shapes", "tensor_dtypes",
    "tensor_formats", "attributes", "input_data_ranges",
    "precision_tolerances", "absolute_precision", "soc_series", "priority", "remark",
)

HS_OPERATORS = {
    "torch_npu.npu_fused_infer_attention_score",
    "torch_npu.npu_mla_prolog_v3",
    "torch_npu.npu_lightning_indexer",
    "torch_npu.npu_quant_lightning_indexer",
    "torch_npu.npu_sparse_flash_attention",
    "torch_npu.npu_kv_quant_sparse_flash_attention",
}


def is_hs_operator(name: str) -> bool:
    return name in HS_OPERATORS


def install_ttk_plugin(operator_name: str, output_dir: Path) -> Path:
    """Install the verified per-operator TTK plugin beside generated CSV."""
    sources = {
        "torch_npu.npu_fused_infer_attention_score": "fia_golden.py",
        "torch_npu.npu_mla_prolog_v3": "mla_prolog_v3_golden.py",
    }
    source_name = sources.get(operator_name, "runtime_bootstrap.py")
    target_name = "ttk_golden_fia.py" if operator_name.endswith("fused_infer_attention_score") else "ttk_plugin.py"
    target = output_dir / target_name
    shutil.copy2(Path(__file__).parent / "ttk_plugins" / source_name, target)
    return target


def load_golden_manifest(operator_name: str) -> dict[str, Any]:
    path = Path(__file__).parent / "golden_manifests" / f"{operator_name}.json"
    if not path.is_file():
        return {"operator": operator_name, "status": "missing", "verified_modes": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _case(api: str, name: str, shapes: list[Any], dtypes: list[str],
          attrs: dict[str, Any], ranges: list[tuple[Any, Any]] | None = None,
          formats: list[str] | None = None, remark: str = "",
          precision_tolerances: str = "((0.005, 0.001),)",
          absolute_precision: str = "0.005") -> dict[str, str]:
    if len(shapes) != len(dtypes):
        raise ValueError(f"{name}: shape/dtype count mismatch")
    return {
        "testcase_name": name,
        "api_name": api,
        "tensor_view_shapes": repr(tuple(shapes)),
        "tensor_dtypes": repr(tuple(dtypes)),
        "tensor_formats": repr(tuple(formats or ["ND"] * len(shapes))),
        "attributes": repr(attrs),
        "input_data_ranges": repr(tuple(ranges or [(-1, 1)] * len(shapes))),
        # TTK encodes (rtol, ptol) here; atol is absolute_precision.
        "precision_tolerances": precision_tolerances,
        "absolute_precision": absolute_precision,
        "soc_series": "('Ascend910B',)",
        "priority": "0",
        "remark": remark or "constraint-first baseline from op-plugin documentation",
    }


def _profiles() -> dict[str, list[dict[str, str]]]:
    fia = "torch_npu.npu_fused_infer_attention_score"
    li = "torch_npu.npu_lightning_indexer"
    qli = "torch_npu.npu_quant_lightning_indexer"
    sfa = "torch_npu.npu_sparse_flash_attention"
    kvsfa = "torch_npu.npu_kv_quant_sparse_flash_attention"
    mla = "torch_npu.npu_mla_prolog_v3"
    max_i64 = (1 << 63) - 1
    block_table_32 = [list(range(32))]
    fia_scenarios = (
        # name, batch, heads, query sequence, kv sequence, head dimension, dtype
        ("doc_baseline", 1, 8, 164, 1024, 128, "float16"),
        ("incremental_s1", 1, 8, 1, 256, 128, "float16"),
        ("prompt_short", 1, 8, 16, 128, 128, "float16"),
        ("prompt_square", 1, 8, 128, 128, 128, "float16"),
        ("batch2", 2, 8, 32, 256, 128, "float16"),
        ("heads1", 1, 1, 32, 128, 128, "float16"),
        ("heads4", 1, 4, 64, 512, 128, "float16"),
        ("head_dim64", 1, 8, 64, 256, 64, "float16"),
        ("head_dim256", 1, 8, 32, 128, 256, "float16"),
        ("long_kv", 1, 8, 8, 2048, 128, "float16"),
    )
    fia_profiles = []
    for name, batch, heads, query_seq, kv_seq, head_dim, dtype in fia_scenarios:
        fia_profiles.append(_case(
            fia, f"fia_a2_bnsd_{dtype}_{name}",
            [(batch, heads, query_seq, head_dim),
             (batch, heads, kv_seq, head_dim),
             (batch, heads, kv_seq, head_dim)],
            [dtype] * 3,
            {"actual_seq_lengths": [query_seq] * batch,
             "actual_seq_lengths_kv": [kv_seq] * batch,
             "num_heads": heads, "scale": 1 / math.sqrt(head_dim),
             "pre_tokens": 65535, "next_tokens": 65535,
             "input_layout": "BNSD"},
            remark="constraint-correlated FIA coverage profile",
        ))
    return {
        fia: fia_profiles,
        li: [_case(li, "li_a2_pa_bsnd_bf16_doc_baseline",
            [(1, 1, 64, 128), (32, 256, 1, 128), (1, 1, 64),
             (1,), (1,), (1, 32)],
            ["bfloat16", "bfloat16", "bfloat16", "int32", "int32", "int32"],
            {"actual_seq_lengths_query": [1], "actual_seq_lengths_key": [8192],
             "block_table": block_table_32, "layout_query": "BSND",
             "layout_key": "PA_BSND", "sparse_count": 2048, "sparse_mode": 3})],
        qli: [_case(qli, "qli_a2_pa_bsnd_int8_doc_baseline",
            [(24, 4, 64, 128), (96, 128, 1, 128), (24, 4, 64),
             (24, 4, 64), (96, 128, 1), (24,), (24,), (24, 4)],
            ["int8", "int8", "float16", "float16", "float16", "int32", "int32", "int32"],
            {"query_quant_mode": 0, "key_quant_mode": 0,
             "actual_seq_lengths_query": [4] * 24, "actual_seq_lengths_key": [512] * 24,
             "block_table": [list(range(i * 4, i * 4 + 4)) for i in range(24)],
             "layout_query": "BSND", "layout_key": "PA_BSND",
             "sparse_count": 2048, "sparse_mode": 0},
            ranges=[(-128, 127), (-128, 127)] + [(0, 1)] * 6)],
        sfa: [_case(sfa, "sfa_a2_bsnd_rope_fp16_doc_baseline",
            [(1, 1, 128, 512), (1, 8192, 1, 512), (1, 8192, 1, 512),
             (1, 1, 1, 2048), None, (1,), (1,), (1, 1, 128, 64), (1, 8192, 1, 64)],
            ["float16", "float16", "float16", "int32", "int32", "int32", "int32", "float16", "float16"],
            {"scale_value": 1 / math.sqrt(512), "actual_seq_lengths_query": [1],
             "actual_seq_lengths_kv": [4096], "sparse_block_size": 1,
             "layout_query": "BSND", "layout_kv": "BSND", "sparse_mode": 3,
             "pre_tokens": max_i64, "next_tokens": max_i64, "attention_mode": 2,
             "return_softmax_lse": False})],
        kvsfa: [_case(kvsfa, "kvsfa_a2_pa_bsnd_int8_doc_baseline",
            [(1, 1, 128, 576), (32, 256, 1, 580), (32, 256, 1, 512),
             (1, 1, 1, 2048), None, None, (1, 32), (1,), (1,)],
            ["float16", "int8", "int8", "int32", "float32", "float32", "int32", "int32", "int32"],
            {"scale_value": 1 / math.sqrt(512), "key_quant_mode": 2, "value_quant_mode": 2,
             "block_table": block_table_32, "actual_seq_lengths_query": [1],
             "actual_seq_lengths_kv": [4096], "sparse_block_size": 1,
             "layout_query": "BSND", "layout_kv": "PA_BSND", "sparse_mode": 3,
             "attention_mode": 2, "quant_scale_repo_mode": 1, "tile_size": 128,
             "rope_head_dim": 64})],
        mla: [_case(mla, "mla_prolog_v3_a2_pa_bsnd_bf16_doc_baseline",
            [(8, 2, 7168), (7168, 1536), (1536, 6144), (32, 128, 512),
             (7168, 576), (1536,), (512,), (8, 2, 64), (8, 2, 64),
             (64, 128, 1, 512), (64, 128, 1, 64), (8, 2)],
            ["bfloat16"] * 11 + ["int64"],
            {"cache_index": [[0, 1]] * 8, "rmsnorm_epsilon_cq": 1e-5,
             "rmsnorm_epsilon_ckv": 1e-5, "cache_mode": "PA_BSND",
             "weight_quant_mode": 0, "kv_cache_quant_mode": 0,
             "query_quant_mode": 0},
            formats=["ND", "FRACTAL_NZ", "FRACTAL_NZ", "ND", "FRACTAL_NZ"] + ["ND"] * 7,
            precision_tolerances="((0.02, 0.001),)", absolute_precision="0.02")],
    }


def generate_ttk_cases(operator_name: str, output: Path, count: int = 1) -> dict[str, Any]:
    profiles = _profiles().get(operator_name)
    if not profiles:
        raise ValueError(f"unsupported HiSilicon operator for TTK: {operator_name}")
    # Never duplicate a profile merely to satisfy --case-count. Repeated
    # rows inflate pass counts without increasing constraint coverage.
    rows = [deepcopy(row) for row in profiles[:count]]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    plugin = install_ttk_plugin(operator_name, output.parent)
    return {"operator_name": operator_name, "test_framework": "ttk", "mode": "e2e",
            "requested": count, "total": len(rows),
            "coverage_exhausted": len(rows) < count, "output": str(output),
            "golden_plugin": str(plugin) if plugin else None,
            "source": "op-plugin documented profiles"}
