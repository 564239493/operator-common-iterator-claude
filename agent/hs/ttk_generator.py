"""torch_npu 推理算子的 TTK 支持工具：算子名单识别与 golden 插件装配。

历史上的 profile 版用例生成器（generate_ttk_cases / load_golden_manifest /
_profiles 及其档案数据）已随 TTK 生成路径统一到 scripts/generate_cases.py
（scenario_planner 约束驱动）而退役，可从 git 历史找回。
"""
from __future__ import annotations

import shutil
from pathlib import Path

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


def resolve_ttk_plugin(operator_name: str, *, golden: bool = True) -> Path:
    """Resolve an operator golden or the runtime-only fallback plugin."""
    sources = {
        "torch_npu.npu_fused_infer_attention_score": "fia_golden.py",
        "torch_npu.npu_mla_prolog_v3": "mla_prolog_v3_golden.py",
        # combine-mode kv_quant needs the official Ascend golden wrapper so
        # TTK's PluginScanner resolves a __golden__ entry for this op instead
        # of falling back to the no-op runtime_bootstrap (which leaves
        # precision comparison without a reference).
        "torch_npu.npu_kv_quant_sparse_flash_attention":
            "kv_quant_sparse_flash_attention_golden.py",
        "torch_npu.npu_lightning_indexer": "npu_lightning_indexer_golden.py",
        "torch_npu.npu_quant_lightning_indexer": "npu_quant_lightning_indexer_golden.py",
        "torch_npu.npu_sparse_flash_attention": "sparse_flash_attention_golden.py",
    }
    source_name = sources.get(operator_name) if golden else None
    return Path(__file__).parent / "ttk_plugins" / (source_name or "runtime_bootstrap.py")


def install_ttk_plugin(operator_name: str, output_dir: Path) -> Path:
    """Install the best available per-operator TTK plugin beside generated CSV."""
    source = resolve_ttk_plugin(operator_name)
    if operator_name.endswith("fused_infer_attention_score"):
        target_name = "ttk_golden_fia.py"
    else:
        target_name = "ttk_plugin.py"
    target = output_dir / target_name
    shutil.copy2(source, target)
    return target
