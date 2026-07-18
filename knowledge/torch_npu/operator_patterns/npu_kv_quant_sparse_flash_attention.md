---
module: npu_kv_quant_sparse_flash_attention
description: npu_kv_quant_sparse_flash_attention 的 26.0.0 专项完整性与冲突检查单
triggers:
  - kind: operator_name_eq
    value: "torch_npu.npu_kv_quant_sparse_flash_attention"
depends_on: ["attention_family", "quantization"]
---
# npu_kv_quant_sparse_flash_attention 专项检查单

以下是 26.0.0 文档的审校索引。必须在当前输入文档中逐条找到证据后才写入 JSON；若版本不同，以输入文档为准。

## 场景骨架

- 产品覆盖 A2/A3 推理，query layout 分 BSND/TND，KV layout 分 BSND/TND/PA；先构造这六类可行或不可行组合，再装配条件。
- query 为 fp16/bf16，文档给出 head 数的离散幂值集合与固定 head dim；key/value 为 int8、KV head 数固定，并使用打包后的最后一维。不要把 query 的 D、逻辑 value D 和 KV 打包 D 混为同一轴。
- 非 PA 场景通常要求 query/key/value 的 batch 或 token 轴相容；PA 场景改由 block table、KV 实际长度和 block size 约束。
- TND 的 actual sequence 参数具有前缀和语义且按场景必传；PA 的 KV 实际长度和 block table 按场景必传。用条件 presence 表示。
- TND 中 `query.shape[0]=Q_T`、`key.shape[0]=KV_T`，actual sequence Tensor 的
  `shape[0]=B`。禁止写 `actual_seq_lengths_*.shape[0] == query.shape[0]`；B 与 T 的
  关联在前缀和末值而非 Tensor 长度。PA 中 `key.shape[0]=block_num`，也禁止与 query
  batch 轴无条件相等。

## 必查参数关系

- `sparse_indices` 的 rank/shape 随 query layout 变化，dtype 为 int32；提取 `sparse_size > 0` 等明确条件。文档只说有效索引排在无效索引之前但未定义哨兵值时，记录 `DOC_GAP`，不要猜 `-1` 或其他值。
- shape 模板中的 B/Q_S/Q_T/KV_N 同名轴必须逐项落成关系；只提 rank 和
  `sparse_size > 0` 属于不完整提取。
- `key_quant_mode`、`value_quant_mode` 在原型中无默认值，应保持 required；支持模式组合按文档闭集提取。
- `key_dequant_scale`、`value_dequant_scale` 在当前版本为保留/不使用参数时，保留输入槽并表达 None-only，不要删除。
- `sparse_block_size` 的离散支持集与 PA block size 的整除/倍数关系分别提取；`tile_size`、`rope_head_dim`、repository mode 等参数只有文档明确限制时才写。
- `pre_tokens`、`next_tokens` 的 int64 上界是值域，不是默认值推断。原型中的 `2^63-1` 文本与结构化整数表示按基础提示词处理。

## 当前版本冲突哨兵

- 原型 `attention_mode=0`，参数说明却称仅支持 2。保留默认值与支持限制，并在相关描述中写 `DOC_CONFLICT`；禁止静默把默认值改成 2 或把 0 加进支持集。
- 参数表对 value/KV 打包最后一维的描述与调用示例中的 value/output 末维可能不一致。以参数/约束硬规格生成结构化规则，同时记录示例冲突；不要从示例单独改写 D。
- 返回值文字称 shape/dtype 与 query 一致时，与示例输出末维冲突也要显式记录，不能选择性忽略。
