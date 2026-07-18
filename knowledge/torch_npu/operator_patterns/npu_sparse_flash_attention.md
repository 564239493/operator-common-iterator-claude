---
module: npu_sparse_flash_attention
description: npu_sparse_flash_attention 的 26.0.0 专项场景与返回值检查单
triggers:
  - kind: operator_name_eq
    value: "torch_npu.npu_sparse_flash_attention"
depends_on: ["attention_family"]
---
# npu_sparse_flash_attention 专项检查单

以下信息仅作为 26.0.0 当前文档的反查清单，所有规则仍需当前输入文档支持。

- query/key/value 在文档支持场景下 dtype 相同且为 fp16/bf16；query 支持 BSND/TND，KV 还支持 PA。分别提取固定 head dim、query head 离散集合、KV head 数及各 layout 轴关系。
- `query_rope`/`key_rope` 的固定 rope dim、dtype/shape/成对 presence 只按文档条件提取。不要因为参数名存在就推断 rope 全局必传。
- `sparse_indices`、`sparse_block_size`、`sparse_mode`、`pre_tokens`、`next_tokens` 的关系按 mode/layout 场景保存；稀疏 block 的离散幂值集合不要压成连续区间。
- TND actual sequence 的前缀和/必传规则与 PA 的 block table、KV actual sequence 规则分开建模。
- 返回原型固定为三个 Tensor 槽。`return_softmax_lse` 只在文档限定的训练/非 PA/非图等场景有效时，用条件规则表达；其他场景下额外返回槽“无效”不等于 tuple 变短。

当前版本存在 `attention_mode=0` 的签名默认值与参数段“仅支持 2”之间的冲突。默认值和支持集分别保留并写 `DOC_CONFLICT`，不能自行修正。也不要把同族量化算子的保留 dequant 参数或 packed D 规则带入本算子。
