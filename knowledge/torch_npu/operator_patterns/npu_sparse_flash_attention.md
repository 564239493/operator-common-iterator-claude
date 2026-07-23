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
- **shape 模板别名等价**：文档 NOTE 定义了符号别名 Q_S=S1, KV_S=S2, Q_N=N1, KV_N=N2, T1/T2。
  同一 layout 下各参数的 shape 模板必须利用这些别名逐轴建立跨参数等式：
  BSND: query=[B,**S1**,N1,D], sparse_indices=[B,**Q_S**,KV_N,K] → `sparse_indices.shape[1] == query.shape[1]`
  TND:  query=[**T1**,N1,D], sparse_indices=[**Q_T**,KV_N,K] → `sparse_indices.shape[0] == query.shape[0]`
  不能只提两个 rank 而遗漏同名轴的跨参数对齐。此模式同样适用于 softmax_max/sum 的 BSND/TND shape
  模板中 S1,N2 与 query/key 的对齐。
- `query_rope`/`key_rope` 的固定 rope dim (64)、dtype (fp16/bf16)、成对 presence
  只按文档条件提取。文档明确 `attention_mode=2` (MLA-absorb) 时 rope 参与拼接计算，
  因此必须提取 presence 约束：`(attention_mode.range_value != 2) or (query_rope is not None)`，
  以及 `(attention_mode.range_value != 2) or (key_rope is not None)`。
  不要反过来（不要写 `attention_mode != 2 or rope is None`），也不要因为参数名存在就推断 rope 全局必传。
  另注意文档中 D 的 rope 部分固定为 64，需与 query/key 中 D 的 nope 部分 512 分别表达。
- `sparse_indices`、`sparse_block_size`、`sparse_mode`、`pre_tokens`、`next_tokens` 的关系按 mode/layout 场景保存；稀疏 block 的离散幂值集合不要压成连续区间。
- TND actual sequence 的前缀和/必传规则与 PA 的 block table、KV actual sequence 规则分开建模。
  当 query/KV 都是 TND 时，两条 actual sequence 向量描述同一个 B，必须提取
  `actual_seq_lengths_query.shape[0] == actual_seq_lengths_kv.shape[0]`；不能把
  query/key 的 T 轴误当成 B。前缀和的末值分别对应 query 的 T1 和 key 的 T2。
  当前 DSL 无法表达逐元素单调性时，保留
  `[SCHEMA_GAP:sequence_element_relation][GENERATOR_GAP:tensor_content_builder]`，
  但仍须提取可表达的 rank、presence 和两个向量的长度等式。
- 文档规定 KV_N 只支持 1，因此 `sparse_indices` 的 KV_N 轴也必须在所有 KV layout
  下绑定为 1：BSND query 使用 `sparse_indices.shape[2] == 1`，TND query 使用
  `sparse_indices.shape[1] == 1`。不能只在 BSND+BSND 分支绑定而漏掉 PA_BSND。
- PA_BSND 下同时提取 `block_table.shape[0] == B`、
  `actual_seq_lengths_kv.shape[0] == B` 及 block_table 索引必须落在已有 block 范围内。
  若当前 schema 无法表达 tensor 内容范围，明确标记 generator gap，不得生成任意有符号
  int32 极值作为正向基础用例。
- 返回原型固定为三个 Tensor 槽。`return_softmax_lse` 只在文档限定的训练/非 PA/非图等场景有效时，用条件规则表达；其他场景下额外返回槽“无效”不等于 tuple 变短。

当前版本存在 `attention_mode=0` 的签名默认值与参数段“仅支持 2”之间的冲突。默认值和支持集分别保留并写 `DOC_CONFLICT`，不能自行修正。也不要把同族量化算子的保留 dequant 参数或 packed D 规则带入本算子。
