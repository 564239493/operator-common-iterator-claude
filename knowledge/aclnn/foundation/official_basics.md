---
module: official_basics
scope: foundation
description: ACLNN 官方数据结构、数据类型、数据格式与非连续 Tensor 基础概念
default_load: true
triggers: []
depends_on: []
---

# ACLNN 官方基础概念（默认加载）

> 来源：`D:/operator_project/aclnn_doc/数据结构.md`、`数据类型.md`、`数据格式.md`、
> `非连续的Tensor.md`。这些内容用于解释文档术语和规范化字段，不代表每个算子都
> 支持全部 dtype、format 或非连续 Tensor；具体候选始终以当前算子文档为准。

## 数据结构

- `aclTensor` 管理张量；`aclTensorList` 管理多个张量。
- `aclScalar` 管理标量；`aclScalarList` 管理多个标量。
- `aclIntArray`、`aclFloatArray`、`aclBoolArray` 分别是 int、float32、bool 数组。
- `aclOpExecutor` 与 `aclrtStream` 是两段式调用流程对象，不是业务输入/输出。

## 数据类型

官方文档常用简写：`ACL_FLOAT`→`FLOAT/FLOAT32`、`ACL_FLOAT16`→`FLOAT16`、
`ACL_BF16`→`BF16/BFLOAT16`、`ACL_DOUBLE`→`DOUBLE/FLOAT64`，其余常见项包括
`INT4/INT8/INT16/INT32/INT64`、`UINT1/UINT8/UINT16/UINT32/UINT64`、`BOOL`、
`STRING`、`COMPLEX32/64/128`、`HIFLOAT8` 和各类 `FLOAT8/FLOAT6/FLOAT4`。
映射只做名称规范化，不得把未在当前算子文档出现的类型补入候选。

## 数据格式

两段式文档常把 `ACL_FORMAT_XXXX` 简写为 `XXXX`。常见格式包括 `ND`、`NCHW`、
`NHWC`、`HWCN`、`NDHWC`、`NCDHW`、`NC`、`NCL`；私有格式只有当前算子文档明确
支持时才能使用。非 ND 格式通常承载轴语义，但 format 与 rank 的具体合法关系仍需
当前文档或命中特征知识证明。

## 非连续 Tensor

非连续 Tensor 由 `shape + strides + offset` 表示。官方基础文档说明多数 API 输入可
支持非连续 Tensor，但这不能替代当前参数表的连续性标记：

- 当前参数行明确 `√`/支持时，`is_support_discontinuous=true`；
- 明确 `×`/不支持时为 false；
- 文档没有参数级证据时不得因“多数 API”而强行写 true。

