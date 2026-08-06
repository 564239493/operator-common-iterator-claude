---
module: type_conversion
scope: feature
description: 官方输入互推导与输出 dtype 互转换关系
default_load: false
triggers:
  - kind: doc_contains
    value: "互转换关系|输出.*数据类型.*推导|计算结果转换成输出类型"
depends_on: [broadcast, platform_dtype, expression_language]
---

# 官方 dtype 互转换关系

当前文档明确引用“互转换关系”或声明输出 dtype 可不同于输入推导后的计算 dtype 时才
加载。整数可向整数/浮点/复数转换，浮点可向浮点/复数转换，复数只在复数间转换，
BOOL 可向整数/浮点/复数转换；其他场景不支持。

该表用于检查输入推导结果与输出候选的合法组合，不能给当前算子补 dtype。若当前文档
要求输出等于推导结果，写 `type_dependency`；若允许合法转换，则约束必须同时保留输入
推导与输出转换门禁，排除官方表外组合。

