---
module: inplace_and_stateful_ops
description: 原地更新、cache 写入及有状态 torch_npu 接口审校规则
triggers:
  - kind: doc_contains
    value: "(?i)(原地更新|原地修改|in-place|inplace|cache.*更新|更新.*cache|状态)"
  - kind: file_name_regex
    value: "_\.md$"
depends_on: []
---
# 原地与有状态接口审校知识

注意：文件名触发只用于以下划线结尾的原地 API；普通文件不应仅因扩展名命中。若当前文档不描述副作用，本模块不添加任何事实。

- 识别哪些输入被写回、哪些输出与输入别名、更新的索引/范围，以及失败或无效索引的行为。
- 返回 `None`、返回被修改 Tensor、返回新 Tensor 与固定占位输出必须严格按原型和返回值章节区分。
- cache 更新通常依赖 cache mode、block table/cache index、actual sequence length 和量化 mode；将它们放在同一场景分支。
- 当前 `OperatorRule` 不能完整表达别名、mutation、读写集合和执行后状态。用 `description` + `SCHEMA_GAP` 保留语义，绝不能误写成普通 shape 相等后就认为已完整表达。
