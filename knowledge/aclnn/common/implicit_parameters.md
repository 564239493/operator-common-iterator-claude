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

文档中常在 shape 描述里出现 **形如 `(BS, H)`、`(H*rankSize, N)`、`[E, N1]/[N1]` 的命名变量**。同一参数表、同一公式链或同一段 shape 说明中复用的同名符号，是文档对维度相等关系的明确绑定；必须把它们映射为 `constraints_in_parameters` 中的真实 Tensor 轴等式。只有该符号仍需独立参与表达式、平台分支或外部计算，不能完全由真实 Tensor 轴代替时，才把它抽取到 `inputs`（`is_operator_param: false`）。跨章节偶然同名且没有共同公式/shape 上下文时，不得臆断为同一维度。登记隐式参数后须覆盖全部支持平台。

### 轴关系优先原则

命名维度的首要用途是建立真实参数之间的轴关系，不是增加可独立随机取值的隐式输入：

1. 先建立“参数 + 轴位置”的符号表，例如 `x=[M,K1]` 得到
   `M -> x.shape[-2]`、`K1 -> x.shape[-1]`；
2. 同一符号再次出现时生成真实轴等式，例如 `weight1=[K1,N1]` 生成
   `x.shape[-1] == weight1.shape[-2]`；
3. 若一个符号仅用于连接 Tensor 轴，不再登记同名隐式参数，避免求解器同时随机生成
   Tensor shape 和 `K1.range_value` 而产生双重真源；
4. 若符号还参与 `K1 < 65536`、`H*rankSize` 等计算，可选择一个明确的真实轴作为真源
   直接写 `weight1.shape[-2] < 65536`，只有无法直接定位真实轴时才登记隐式参数；
5. 输出 shape 中的同名符号也必须绑定，不能只绑定输入之间的关系。

FFN 链式 MatMul 示例：

```text
x       = [M, K1]
weight1 = [K1, N1]
weight2 = [K2, N2]
y       = [M, N2]
```

应至少生成：

```text
x.shape[-1] == weight1.shape[-2]
x.shape[:-1] == y.shape[:-1]
weight2.shape[-1] == y.shape[-1]
```

正文若再声明 `K1=N2`、`N1=K2` 或 `N1=2*K2`，继续映射为对应真实轴等式。

若同名轴只在某个 presence 分支存在，例如 `weight1=[E,K1,N1]`、
`weight2=[E,K2,N2]` 与 `expertTokens=[E]`，应直接生成带缺席守卫的真实轴关系：

```text
expertTokens is None or (weight1.shape[0] == weight2.shape[0] and expertTokens.shape[0] == weight1.shape[0])
```

不要创建独立随机的 `E.range_value`。presence 与 rank 的双向绑定必须用条件表达式
枚举完整合法组合，例如 `(len(weight1.shape) == 2) if (expertTokens is None) else
(len(weight1.shape) == 3)`；禁止使用布尔 `==`，也禁止遗漏 `else` 而固定为缺席分支。

上述 rank 模板假定 `weight1` 是必选 Tensor。若被访问 shape 的目标参数本身 Optional，
必须先在同一分支声明其存在；不得在目标缺席分支访问 `.shape`。只有文档明确给出两态
完整映射时才生成上述 `if/else`，单向描述不得擅自补全反向关系。

## §A 标准命名维度变量

| 标识符 | 典型上下文 | 类别 |
| ------ | ---------- | ---- |
| `N` / `C` / `H` / `W` | `(N, C, H, W)` | dimension_variable |
| `BS` / `B` | `(BS, H)` | dimension_variable |
| `batchSize` / `numHeads` | `(batchSize, numHeads)` | dimension_variable |
| `k0` / `n0` / `m0` | `(k0, n0)` | dimension_variable（除非有等式赋值） |
| `dim` / `rank` / `seqLen` | `(dim, rank)` | dimension_variable |
| `E` / `G` / `N1` | `[E, N1]`、`[E, G, N1]` | dimension_variable |

表中标识符只有在“轴关系优先原则”无法用真实 Tensor 轴完整表达，且后续表达式确实需要
独立引用时才登记为 `dimension_variable`；不得看到大写符号就无条件新增隐式输入。

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
直接写数字，不伪造成业务参数。同时反扫所有 shape 表中的同名符号：每个跨参数复用
符号必须对应至少一条真实轴等式；链式 MatMul 必须包含输入与首个 weight 的 Reduce 轴
等式，不能只约束两个 weight。
