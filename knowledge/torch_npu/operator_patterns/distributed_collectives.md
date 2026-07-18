---
module: distributed_collectives
description: torch_npu 集合通信及通信融合算子的 rank/group/count 审校规则
triggers:
  - kind: operator_name_regex
    value: "(?i)(all_gather|all_to_all|alltoall|all_reduce|reduce_scatter|npu_broadcast|moe_distribute_(?:dispatch|combine))"
depends_on: ["collections_and_grouped_ops"]
---
# 分布式集合通信家族审校知识

- `group`/`hcom`、`world_size`、`rank_id` 成组读取；只有文档明确时才写
  `0 <= rank_id < world_size`，合法 world size 仍需按产品/算法场景限制。
- EP/TP 的 group、world size、rank id 是不同通信域。空字符串或 0 可能表示关闭某一域，
  不能全局判为非法。
- send/recv counts 的容器长度、每元素值和总和分别关联 world size、专家数和本地 token
  轴；当前 schema 无法表达动态逐元素/总和时保留 `SCHEMA_GAP`。
- 跨卡 rank 唯一、各卡 shape/dtype 一致、全通信域 token 守恒是跨设备约束，不得伪造
  成当前单卡 Tensor 的相等关系。
- `comm_alg`/`comm_mode`、产品和 world size 的条件表保持行内组合，不拆成自由枚举。
- `gather_output`、共享专家等开关可能只控制固定返回槽的有效性；不要缩短 tuple。
- 融合 matmul 时区分通信前后的 M/token 轴，并把 transpose flag 绑定到正确的 local 或
  gathered Tensor；同时应用矩阵乘模块时，两者场景应合并而非互相覆盖。
