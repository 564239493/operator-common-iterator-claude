---
module: npu_fused_infer_attention_score
description: npu_fused_infer_attention_score 的 26.0.0 大型场景矩阵与输出检查单
triggers:
  - kind: operator_name_eq
    value: "torch_npu.npu_fused_infer_attention_score"
depends_on: ["attention_family", "quantization", "collections_and_grouped_ops"]
---
# npu_fused_infer_attention_score 专项检查单

当前 26.0.0 文档很长并含多张 HTML 条件表。必须处理完整参数段、表格和约束段，禁止只读取开头的 query/key/value 描述。

## 场景拆分顺序

建议按以下键逐层构造场景，而不是生成一个全局约束交集：

1. 产品及推理/图模式范围；
2. `Q_S == 1`（decode）或 `Q_S > 1`（prompt）；
3. `input_layout`，包括普通、TND/NTD、PA 以及文档定义的 transformed/NZ 组合布局；
4. 普通 KV Tensor 或 TensorList/分段输入；
5. page attention、shared prefix、padding、rope 的 presence；
6. 非量化、量化、伪量化/antiquant 及 scale 表示方式；
7. sparse mode、mask/pse 与 `softmax_lse_flag`。

每个叶场景绑定 query/key/value dtype、rank/shape、head 关系、序列参数、block 规则、辅助 Tensor presence 和输出规则。

## 必查关系

- `num_heads`、`num_key_value_heads` 与 Q/KV 的 N/H 轴关系随 layout 改变；GQA/MQA、NZ/transformed layout 的整除和 head 组合不能套用普通 BSH 公式。
- `actual_seq_lengths` 与 `actual_seq_lengths_kv` 是 Python `List[int]`，不是 Tensor。TND/NTD 场景的前缀和、batch 数上限、末值与 T 关系要保留；当前 schema 无法完整表达序列内容时标记 `SCHEMA_GAP`。
- `actual_shared_prefix_len` 是 optional `List[int]`，不是 Tensor。文档的“存在时 shape
  为 `[1]`”必须写 `array_length.value=[1]`，不得误写成
  `allowed_range_value=[1,null]`。它的唯一元素还必须不大于
  `key_shared_prefix` 和 `value_shared_prefix` 各自在当前 `input_layout` 下的 S 轴；
  用带 `actual_shared_prefix_len is None`、共享前缀参数 presence 和 layout 分支守卫的
  `shape_value_dependency` 表达。BSH/BSND 的 S 轴为 1，BNSD 的 S 轴为 2；其他布局
  只按当前输入文档明确的轴定义提取，不得统一套用 `shape[1]`。
  长度固定为 1 后，元素关系使用 `actual_shared_prefix_len[0]`，不要拿整个
  `.range_value` 序列与整数比较。至少生成：
  `(actual_shared_prefix_len is None) or ((key_shared_prefix is not None) and (value_shared_prefix is not None))`，
  以及分别针对 key/value、按布局选择 S 轴的上界关系，例如
  `(actual_shared_prefix_len is None) or (key_shared_prefix is None) or (input_layout.range_value not in ["BSH", "BSND"]) or (actual_shared_prefix_len[0] <= key_shared_prefix.shape[1])`；
  BNSD 分支把最后一轴索引改为 `shape[2]`。value 侧生成对应的独立约束。
- `key_shared_prefix`、`value_shared_prefix` 自身的 shape 和互等关系也必须带 optional
  presence 守卫，避免参数缺省时仍访问 `.shape`。
- PageAttention 的 `block_table`、`block_size`、KV cache shape、有效 block id 和 padding 规则按 `Q_S`、产品与 layout 条件化。文档声明 block id 不校验时，写成用户前置条件。
- query/key rope、shared prefix、query/KV padding、mask、pse 的 dtype/shape/presence 都有独立条件和互斥项，不能根据名字自动成对必选。
- `sparse_mode` 与 mask shape、pre/next token 的规则按 mode 分支；文中的 `sparse_modew` 等明显笔误保留为 `DOC_GAP`，不要创建不存在的输入名。
- 空 query/KV 的输出可能分别为空 Tensor 或全零 Tensor；按文档条件提取，不要统一成“支持空输入”。

## 量化与 dtype

- 量化表按行保存 query/key/value dtype、dequant/quant/antiquant scale 与 offset 的 presence、dtype/shape以及输出 dtype。
- combined antiquant 与 key/value separate antiquant 是不同场景；per-tensor/per-channel 等 mode 也分别处理。
- query 参数首段只列 fp16/bf16，但后续约束和量化表出现 int8 query 场景。保留场景表中的条件支持，并给全局 query dtype 描述加 `DOC_CONFLICT`；禁止把 int8 提升成所有场景均支持。

## 返回值与软提示

- 原型固定返回两个 Tensor。`softmax_lse_flag=False` 时，第二槽在当前文档是 shape `[1]`
  的 float32 零 Tensor，而不是省略返回值；shape 1 可结构化，恒零内容标记
  `SCHEMA_GAP:constant_tensor_contents`。
- 输出 shape/dtype 随 layout、Q_S、量化和 rope 场景派生；不要只写“与 query 相同”后遗漏例外。
- “建议 padding”“推荐 128”“可能超时”“性能更优”等仅记录为软说明，不能生成硬范围或整除约束。
