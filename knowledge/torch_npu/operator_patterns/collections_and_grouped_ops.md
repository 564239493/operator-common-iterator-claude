---
module: collections_and_grouped_ops
description: TensorList、整数序列、分组及 MoE 类接口审校规则
triggers:
  - kind: operator_name_regex
    value: "(?i)(grouped|moe|alltoall|scatter|gather)"
  - kind: doc_contains
    value: "(?i)(List\[Tensor\]|TensorList|List\[int\]|group_list|分组)"
depends_on: []
---
# 集合与分组接口审校知识

- 分别提取容器是否可为空、列表长度、每个元素的 dtype/rank/shape，以及元素之间是否同 dtype/同 shape；这些不是同一条约束。
- `List[int]` 可能是 shape、axis、实际序列长度或分组边界。根据参数语义处理，不能统一当作 Tensor。
- group list 可能表示前缀和或每组数量；只在文档明确时添加单调性、末值或总和关系。
- Tensor 与 TensorList 的联合输入、不同 layout 下容器类型变化属于场景分支。当前 schema 不能无损表达联合类型时标记 `SCHEMA_GAP` 并保留原始声明。
- “所有元素”规则若 DSL 不允许 `all/any`，不要写不可执行表达式；将可表达的容器级信息结构化，其余逐元素语义写入描述并标记 schema 缺口。
