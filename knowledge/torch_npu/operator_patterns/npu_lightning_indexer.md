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
- TND 的 actual sequence 参数、PA 的 block table 和 actual key length 分场景表达 presence、长度和累计语义。
- `sparse_count` 的合法域在当前版本是非连续集合：`[1, 2048]` 与离散的 3072/4096/5120/6144/7168/8192。必须使用区间与离散值的并集表达，不能错误放宽为 `[1,8192]`。
- 当前文档 `sparse_mode` 支持闭集 0/3；与 pre/next token 的关系只在文档指定 mode 下使用。
- 原型固定返回两个 Tensor。`return_value=False` 时第二槽可能无效或占位，但不能删除该输出；按返回值说明记录 dtype/shape/有效性。

示例只能帮助核对 layout 轴，不能据其单一尺寸收窄范围。尤其不要臆造 `sparse_count <= S2`：量化同族示例可能明确使用大于序列长度的 sparse_count，当前算子也只遵循自身文档。
