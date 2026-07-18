---
module: npu_quant_lightning_indexer
description: npu_quant_lightning_indexer 的 26.0.0 专项量化场景与签名冲突检查单
triggers:
  - kind: operator_name_eq
    value: "torch_npu.npu_quant_lightning_indexer"
depends_on: ["attention_family", "quantization"]
---
# npu_quant_lightning_indexer 专项检查单

- query/key 为 int8，当前文档分别固定 query/key head 数和 head dim；不要继承非量化 lightning indexer 的 fp16/bf16 输入支持。
- `weights`、`query_dequant_scale`、`key_dequant_scale` 的 dtype/rank/shape 依 layout 或 PA 场景变化。特别检查 key dequant scale 是否只对 PA 给出 shape；未覆盖场景写 `DOC_GAP`，不要类推。
- `query_quant_mode`、`key_quant_mode` 与 scale shape/presence 组成场景元组，不能只分别生成两个 mode 枚举。
- 原型中的两个 quant mode 没有默认值，按调用结构应为 required；参数表若称“可选”，记录 `DOC_CONFLICT`，不得凭该文字添加 None/default。
- `sparse_count` 当前文档范围为 `[1,2048]`；不要复制非量化算子的额外离散值。
- 文档若要求 scale 与输入数值的乘积处于 fp16 可表示范围，应作为值域前置条件保留。当前 DSL 无法严谨表达逐元素乘积时写 `SCHEMA_GAP`，不能丢弃。
- 示例中 `sparse_count` 可以大于 S2，因此禁止从常识生成 `sparse_count <= S2`。
