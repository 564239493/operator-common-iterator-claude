---
module: sparse_mode
scope: feature
description: 官方 attention sparseMode 场景识别
default_load: false
triggers:
  - kind: doc_contains
    value: "sparseMode|sparse_mode|稀疏模式"
depends_on: [expression_language, sparse_mode_foundation]
---

# sparseMode 场景知识（派生规则）

> 官方 sparseMode 0～8 各模式语义与 preTokens/nextTokens/attenMask 取值表见 `foundation/sparse_mode`（由 depends_on 拉入）；本模块只给派生规则，不重复取值表。

只有当前算子文档声明支持的取值才能进入枚举；不得因为公共文档存在 0～8 就补齐全集。

每个支持 mode 都要作为场景 guard，联合绑定当前文档规定的 `attenMask` format/shape、
`preTokens`/`nextTokens` 是否生效及其他 presence 关系。公共示意图和性能说明不是硬
约束；当前算子文档与公共说明冲突时以当前算子文档为准并保留冲突证据。

