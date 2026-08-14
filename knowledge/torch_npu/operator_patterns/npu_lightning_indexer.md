---
module: npu_lightning_indexer
description: npu_lightning_indexer 的 26.0.0 专项 layout、稀疏数量与固定返回检查单
triggers:
  - kind: operator_name_eq
    value: "torch_npu.npu_lightning_indexer"
depends_on: ["attention_family"]
---
# npu_lightning_indexer 专项检查单

- query/key 为相同 fp16 或 bf16 dtype，当前文档固定 head dim，query head 数有上界，key head 数固定。按 BSND/TND/PA 等当前文档 layout 分支写 rank 与轴关系。
- `weights` 的 shape 随 layout 变化；其 dtype 可能为 float32 或与 query/key 相同。保持 dtype 与 layout/输入 dtype 的关联，不要生成不受支持的组合。
- TND 的 actual sequence 参数、PA 的 block table 和 actual key length 分场景表达 presence、长度和累计语义。可选属性约束使用
  `(param is None) or (...)`，场景必传使用 `(layout条件不成立) or (param is not None)`；
  不得输出 `.is_present`。layout/shape 分支必须保留为有效完整 `expr`，不得添加
  `# TODO:` 前缀跳过。
- `block_table` 的 presence 是双向硬约束：`layout_key == "PA_BSND"` 时必须存在，
  `layout_key != "PA_BSND"` 时必须为 `None`。分别写
  `(layout_key.range_value != "PA_BSND") or (block_table is not None)` 和
  `(layout_key.range_value == "PA_BSND") or (block_table is None)`。禁止把后一条写成
  `(not (layout_key.range_value == "PA_BSND")) or (block_table is None)`，该错误写法会
  与前一条共同令 PA 场景 UNSAT，却不限制 TND/BSND。
- `layout_query == "BSND"` 时，存在的 `actual_seq_lengths_query` 和
  `actual_seq_lengths_key` 都是长度为 query batch B 的一维 Tensor；分别用 optional
  守卫约束其 `shape[0] == query.shape[0]`。不能只约束 query 侧而遗漏 key 侧。
- `layout_query == "TND"` 且 `layout_key == "TND"` 时，
  `actual_seq_lengths_query` 与 `actual_seq_lengths_key` 的元素个数表示同一个
  effective batch B，因此两者存在时必须额外提取跨参数关系：
  `(layout_query.range_value != "TND") or (layout_key.range_value != "TND") or
  (actual_seq_lengths_query is None) or (actual_seq_lengths_key is None) or
  (actual_seq_lengths_query.shape[0] == actual_seq_lengths_key.shape[0])`。
  文档分别写出的两个 `[B]` 不是两个互不相关的自由维度，也绝不能写成
  `actual_seq_lengths_*.shape[0] == query.shape[0]`；TND 中 query 第一维是总
  token 数 T，不是 B。
- TND 的 actual sequence Tensor 内容是非降前缀和，末元素对应各自 T；PA 的
  `block_table` 内容必须是有效 block id。若通用随机 Tensor 不能物化这些内容，应在
  TTK input builder 中构造，不能只靠 shape/range 元数据假装满足。
- `sparse_count` 的合法域在当前版本是非连续集合：`[1, 2048]` 与离散的 3072/4096/5120/6144/7168/8192。必须使用区间与离散值的并集表达，不能错误放宽为 `[1,8192]`。
- 当前文档 `sparse_mode` 支持闭集 0/3；与 pre/next token 的关系只在文档指定 mode 下使用。
- 原型固定返回两个 Tensor。`return_value=False` 时第二槽可能无效或占位，但不能删除该输出；按返回值说明记录 dtype/shape/有效性。涉及该开关的表达式必须写
  `return_value == False` 或 `return_value != True`，禁止写 `return_value is False`。

示例只能帮助核对 layout 轴，不能据其单一尺寸收窄范围。尤其不要臆造 `sparse_count <= S2`：量化同族示例可能明确使用大于序列长度的 sparse_count，当前算子也只遵循自身文档。
