---
module: type_conversion_foundation
scope: foundation
description: 官方 aclTensor 数据类型互转换关系原文参考
default_load: false
triggers:
  - kind: doc_contains
    value: "互转换|格式转换|format转换|计算结果转换成输出类型"
depends_on: []
---

# 互转换关系（官方概念参考，按需加载）

> 本模块是 CANN 官方 `互转换关系.md` 的原始概念参考，用于解释文档中「输出 dtype 可
> 转换」的合法组合。**派生约束规则**（如何把合法转换组合落为
> `type_dependency` / 枚举门禁）在 `features/type_conversion.md`；两者区分显示，本模块
> 不替代派生规则。

## 互转换关系

当一个 API（如 `aclnnAdd`、`aclnnMul` 等）**输出的 aclTensor 数据类型**与**输入的
数据类型**推导后的计算类型不一致时，API 内部就会将计算结果转换成输出类型对应的数据类型。

数据类型转换需要满足以下规则，不满足规则的将不能进行转换，调用 API 时会出现参数校验
失败。

- 浮点类型：`ACL_FLOAT16`、`ACL_FLOAT`、`ACL_DOUBLE`、`ACL_BF16`。
- 整数类型：`ACL_INT8`、`ACL_UINT8`、`ACL_INT16`、`ACL_UINT16`、`ACL_INT32`、
  `ACL_UINT32`、`ACL_INT64`、`ACL_UINT64`。
- 复数类型：`ACL_COMPLEX64`、`ACL_COMPLEX128`。
- 整数类型间可以转换，也支持往浮点、复数类型转换。
- 浮点类型间可以转换，也支持往复数类型转换。
- 复数类型间可以转换。
- BOOL 支持往整数、浮点、复数类型转换。

除了以上场景，其他场景的转换均不支持。
