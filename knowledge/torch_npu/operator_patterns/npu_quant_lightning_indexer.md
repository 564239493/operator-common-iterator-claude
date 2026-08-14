---
module: npu_quant_lightning_indexer
description: npu_quant_lightning_indexer 专项量化场景与签名冲突检查单
triggers:
  - kind: operator_name_eq
    value: "torch_npu.npu_quant_lightning_indexer"
depends_on: ["attention_family", "quantization"]
---
# npu_quant_lightning_indexer 专项检查单

- query/key 的 dtype 必须按当前平台段提取；不要继承非量化 lightning indexer 的
  fp16/bf16 输入支持，也不得把某个平台段的 dtype 组合无依据复制到其他平台。
- query/key 的 D 都固定为 128，N2 固定为 1。N1 的平台取值和 N1/N2 关系应按文档或
  可追溯补充证据提取；不能只提取 `N1 <= 64` 而漏掉执行所需的 head 比例关系。
- layout 与 shape 必须写成一个完整分支或条件蕴含，禁止把单个场景写成全局正向
  `and`。例如 PA 条件必须写成：

  ```python
  (layout_key.range_value != "PA_BSND") or (
      block_table is not None
      and len(block_table.shape) == 2
  )
  ```

  不得写成 `(layout_key.range_value == "PA_BSND") and (...)`，否则会强制所有用例
  进入 PA 场景。写完后至少检查 BSND、TND、PA_BSND 三组真值。
- `weights` 和 `query_dequant_scale` 跟随 `layout_query`：
  BSND 对应 `[B,S1,N1]`，TND 对应 `[T1,N1]`。两支应合并为一个 OR 表达式，不能拆成
  两条分别全局断言的正向 conjunction。
- `key_dequant_scale` 跟随 `layout_key`，不能用 `layout_query` 门控。文档明确给出的
  PA_BSND shape 为 `[block_count,block_size,N2]`；BSND/TND 若当前文档没有明确 shape，
  标记 `DOC_GAP`，不要仅凭对称性伪造成文档约束。执行日志或源码补充出的关系须保留
  `origin`/证据来源。
- `query_quant_mode`、`key_quant_mode` 与 scale shape/presence 组成场景元组，不能只分别生成两个 mode 枚举。
- 原型中的两个 quant mode 没有默认值，按调用结构应为 required；参数表若称“可选”，记录 `DOC_CONFLICT`，不得凭该文字添加 None/default。
- 所有 Python 标量必须保留参数类型与底层 dtype 两条通道，禁止把 dtype 写成 `N/A`：
  `layout_query/layout_key` 使用 `dtype.value=["string"]`；
  `query_quant_mode/key_quant_mode` 使用整数 dtype（源码属性为 `int32_t`）；
  `sparse_count/sparse_mode` 使用 `["int32"]`；
  `pre_tokens/next_tokens` 使用 `["int64"]`。如果重新提取时这些字段从具体 dtype
  回退为 `N/A`，属于约束回归，必须在 GENERATE 前阻止。
- `pre_tokens`、`next_tokens` 当前仅支持精确整数 `9223372036854775807`
  (`2^63-1`)。两者分别写单元素标量 enum，禁止经过 float/fp16/fp32 表示；
  `9.223372036854776e+18` 会舍入为 `2^63`，不是合法默认值。
- `sparse_count` 当前文档范围为 `[1,2048]`；不要复制非量化算子的额外离散值。
- `actual_seq_lengths_query` / `actual_seq_lengths_key` 除 `[B]` shape 和条件 presence
  外，还必须提取元素值域：不小于 0，不超过对应逻辑序列长度；TND 为非递减前缀和。
  当前 range-only TTK 无法保证多元素前缀和时标记
  `[GENERATOR_GAP:literal_tensor_builder]`，不能用任意 int32 范围代替。
- PA_BSND 下 `block_table` 必须为二维，第一维等于有效 B；元素是 block 映射索引，
  必须非负且小于 `block_count`。第二维与 `actual_seq_lengths_key`、`block_size`
  的容量关系应进入跨参数约束；不能只保留 int32 dtype。
- 文档若要求 scale 与输入数值的乘积处于 fp16 可表示范围，应作为值域前置条件保留。当前 DSL 无法严谨表达逐元素乘积时写 `SCHEMA_GAP`，不能丢弃。
- 示例中 `sparse_count` 可以大于 S2，因此禁止从常识生成 `sparse_count <= S2`。

## 提取后专项自检

1. 函数签名中不得出现文档原型不存在的 `query_dtype` / `key_dtype` 参数。
2. dtype 是单值属性，使用 `query.dtype == "int8"`，不得写 `query.dtype[0]`。
3. 每个平台桶分别检查 weights/scale rank、dtype tuple 和 layout 条件，不得漏桶。
4. 对每条 `layout == X` 开头的关系做真值检查：其他 layout 必须能够通过该关系。
5. BSND 与 TND 的 optional sequence tensor 允许按原型传 None；只有文档明确的场景才
   条件必传，不能改成全局 required。
6. 检查生成后的所有标量运行时类型：layout 必须为 `str`，mode/count/token 必须为
   Python `int`；`pre_tokens/next_tokens` 必须逐字等于 `9223372036854775807`。
