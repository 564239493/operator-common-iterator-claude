---
module: indexed_access_and_update
description: torch_npu gather/scatter/index/slice 接口的索引、轴与更新语义审校规则
triggers:
  - kind: operator_name_regex
    value: "(?i)(quant_scatter|scatter_nd_update|scatter_pa_kv_cache|(?:^|\.)scatter_update|gather_sparse_index|npu_indexing|npu_slice)"
depends_on: []
---
# 索引访问与更新家族审校知识

- 区分 axis scatter 与 scatter_nd。后者只有文档明确采用标准 ND 语义时才写
  `updates.shape = indices.shape[:-1] + input.shape[indices.shape[-1]:]`；动态切片无法被
  当前 DSL 无损表示时标记 `SCHEMA_GAP`。
- 分别提取 index dtype/rank、坐标 tuple 长度和逐轴边界；只有文档明确时才允许负索引。
- 负 axis 示例不等于支持任意负轴，以参数范围/闭集和 rank 门控为准。
- 重复、重叠或越界索引可能导致未定义/非确定行为。“不校验、用户保证不越界/不重合”
  仍是用例生成前置条件。
- functional 与 `_` 版本可有相同 shape，但 mutation、地址复用和 alias 不同；原地版本
  与状态模块共同记录 `SCHEMA_GAP`，不能只复制 functional 输出规则。
- slice/indexing 的 begin/end/stride/mask 是多条等长序列及位掩码；分别约束长度和值，
  不能压成单个 scalar range。
- 仅为性能收益的 shape 乘积或对齐建议保持软信息。
