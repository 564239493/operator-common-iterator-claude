"""Per-worker torch_npu runtime settings required by internal formats."""
import torch_npu

torch_npu.npu.config.allow_internal_format = True
