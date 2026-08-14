---
module: implicit_parameters
scope: common
description: 命名维度变量、常量和外部常量识别
default_load: true
triggers: []
depends_on: []
---

# 隐式参数（命名维度变量 / 外部常量）识别

## §隐式参数

文档中常在 shape 描述里出现 **形如 `(BS, H)`、`(H*rankSize, N)`、`[E, N1]/[N1]` 的命名变量**，它们**不是函数签名参数**，但被下游 `constraints_in_parameters` 中的表达式引用。**必须**抽取到 `inputs` 中（`is_operator_param: false`），按以下规则分类。同名符号只有文档明确绑定时才表示同一维度；登记后须覆盖全部支持平台。

## §A 标准命名维度变量

| 标识符 | 典型上下文 | 类别 |
| ------ | ---------- | ---- |
| `N` / `C` / `H` / `W` | `(N, C, H, W)` | dimension_variable |
| `BS` / `B` | `(BS, H)` | dimension_variable |
| `batchSize` / `numHeads` | `(batchSize, numHeads)` | dimension_variable |
| `k0` / `n0` / `m0` | `(k0, n0)` | dimension_variable（除非有等式赋值） |
| `dim` / `rank` / `seqLen` | `(dim, rank)` | dimension_variable |
| `E` / `G` / `N1` | `[E, N1]`、`[E, G, N1]` | dimension_variable |

## §B 复合表达式中的命名变量

| 表达式 | 抽取 |
| ------ | ---- |
| `H*rankSize` | `H` 为 dimension_variable；`rankSize` 为 external_constant |
| `BS/rankSize` | `BS` 为 dimension_variable；`rankSize` 为 external_constant |
| `A*B`（两者均独立出现） | `A`、`B` 均为 dimension_variable |

## §C 必须剔除的概念词/操作名/类型词

这些**不是**隐式参数：

| 类别 | 剔除清单 |
| ---- | -------- |
| **维度概念词**（"X维度"中的X表示含义，不是变量名） | `Reduce`、`GEMV`、`Attention`、`Conv` |
| **激活函数名** | `Softmax`、`ReLU`、`Sigmoid`、`GELU`、`SwiGLU` |
| **归一化操作名** | `LayerNorm`、`BatchNorm` |
| **卷积操作名** | `Conv`、`Conv2D`、`Conv3D` |
| **矩阵乘操作名** | `Matmul`、`BMM`、`MM` |
| **张量操作名** | `Transpose`、`Reshape`、`Permute` |
| **泛型描述词** | `shape`、`dtype`、`format`、`type`、`input`、`output`、`tensor`、`optional`、`true`、`false`、`none`、`null` |
| **基本类型词** | `float`、`double`、`int`、`char`、`void` |

## §D 常量识别（显式赋值 → 转为常量）

| 原文模式 | 标识符 | 分类 | `constant_value` |
| -------- | ------ | ---- | ---------------- |
| `"其中k0 = 16"` | `k0` | constant | `16` |
| `"n0为16"` | `n0` | constant | `16` |
| `"k0等于16"` / `"k0 is 16"` | `k0` | constant | `16` |
| `"其中 G = 128"` | `G` | constant | `128` |

**NZ 块尺寸常量（v2 新增）**：当文档同时存在 `"k0 = 16"`、`"n0 = 16"` 两条赋值（如
`"k0 = 16， n0为16"` / `"n0 = 16， k0为16"`），`k0`、`n0` 统一按 constant 处理，
`constant_value=16`；**不允许**为 `k0`、`n0` 在 `inputs` 中再产出 `dimension_variable`
卡片，亦不允许将其作为隐式变量在 `constraints_in_parameters` 中被 `k0.range_value`
形式引用。

## §E 外部常量识别（仅出现在复合表达式中 → external_constant）

| 标识符 | 出现位置 | 类别 | 说明 |
| ------ | -------- | ---- | ---- |
| `rankSize` | `H*rankSize` | external_constant | 平台相关（NPU 卡数） |
| `worldSize` | `BS/worldSize` | external_constant | 分布式训练相关 |
| `padSize` | `N+padSize` | external_constant | 视上下文决定 |

外部常量必须按平台分别给出 `allowed_range_value`（枚举式），如 `rankSize` 在 Atlas A2 上为 `[2,4,8]`，在 Atlas 350 上为 `[2,4,8,16]`。

## §F 漏抽取补充

正则可能漏掉仅在**约束描述文字**中出现、但未在任何 shape 元组里出现的变量（如 "rankSize 的取值依赖于 NPU 卡数"）。**应当**将其补加到 `inputs`，类别为 `external_constant`。

## §反扫校验

完成后反扫全部 expr：每个非函数签名标识符要么是已登记隐式输入，要么是允许的
Python 名称/常量；不得留下游无法解析的游离变量。`k0=16` 等显式赋值是常量，表达式
直接写数字，不伪造成业务参数。
