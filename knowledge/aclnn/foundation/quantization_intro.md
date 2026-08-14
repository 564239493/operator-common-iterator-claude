---
module: quantization_intro
scope: foundation
description: 官方 CANN 算子量化概念与量化粒度原文参考
default_load: false
triggers:
  - kind: doc_contains
    value: "量化|反量化|quant|pertensor|perchannel|pertoken|pergroup|perblock"
depends_on: []
---

# 量化介绍（官方概念参考，按需加载）

> 本模块是 CANN 官方 `量化介绍.md` 的原始概念参考，用于解释文档中出现的量化术语与
> 量化粒度。**派生约束规则**（如何把量化粒度落为参数 shape 检查与场景门禁）在
> `features/quantization.md`；两者区分显示，本模块不替代派生规则。

量化广泛应用于深度学习模型中，特别是在推理过程中。通过量化，模型可以在硬件上更高效地
运行，减少计算资源的消耗和加速推理过程，同时降低模型的存储需求。

CANN 算子量化是指对神经网络中 Matmul 等矩阵（cube）类算子的输入 Tensor 从高 bit 到
低 bit 转换的计算过程，同时生成对应的量化参数 scale。当低 bit 的 cube 计算完成后，
可通过量化参数 scale 将低 bit 数值转换回高 bit 数值，从而保证整体计算结果的正确性
（效果与直接用高 bit 计算近似等价），并有效提升计算效率。

- 静态量化：使用预先确定的量化参数进行量化。推理场景下对权重 weight 的量化一般采用
  静态量化，量化算子性能会更好些。
- 动态量化：使用输入数据在线计算量化参数进行量化。推理场景下对激活 activation 的量化
  一般采用动态量化，更能适应数据的变化，精度更高；训练场景下为了提升量化精度，也一般
  采用动态量化。注意，动态量化因为在线生成量化参数，量化算子性能会略差些。

## 量化模式

量化模式（又称量化粒度）是指对算子的不同输入 Tensor 采用不同的量化计算级别，常见的
量化计算模式包括：

> 说明：
> - m、n、k 变量分别表示 Tensor 计算的不同轴大小。
> - 左矩阵、右矩阵分别指 cube 算子中用于矩阵乘法计算的两个输入 Tensor，一般左矩阵
>   代表激活 activation、右矩阵代表权重 weight，请用户按实际情况理解和使用。

- pertensor 量化（简称 T 量化）：量化对象既可以是左矩阵，也可以是右矩阵，每个 Tensor
  共用一个相同的量化参数。
  - 假设左矩阵 shape 为 `(m, k)`，右矩阵 shape 为 `(k, n)`，k 为 reduce 轴，生成量化
    参数的 shape 为 `(1,)`。
- perchannel 量化（简称 C 量化）：量化对象是右矩阵，每个 channel 分别使用独立的量化
  参数。
  - 假设右矩阵 shape 为 `(k, n)`，k 为 reduce 轴，生成量化参数的 shape 为 `(n,)`。
- pertoken 量化（简称 K 量化）：量化对象是左矩阵，每个 token 分别使用独立的量化参数。
  - 假设左矩阵 shape 为 `(m, k)`，k 为 reduce 轴，生成量化参数的 shape 为 `(m,)`。
- pergroup 量化（简称 G 量化）：量化对象既可以是左矩阵，也可以是右矩阵，在 reduce 轴
  上对数据分组，每组使用独立的量化参数。
  - 假设左矩阵 shape 为 `(m, k)`，k 为 reduce 轴，在 k 轴上分组，group size 为 gs，
    生成量化参数的 shape 为 `(m, k/gs)`。
  - 假设右矩阵 shape 为 `(k, n)`，k 为 reduce 轴，在 k 轴上分组，group size 为 gs，
    生成量化参数的 shape 为 `(k/gs, n)`。
- perblock 量化（简称 B 量化）：量化对象既可以是左矩阵，也可以是右矩阵，在所有轴上对
  数据分块，每块使用独立的量化参数。
  - 假设左矩阵 shape 为 `(m, k)`，k 为 reduce 轴，在 m、k 轴上分别按 `(bs, bs)` 块对
    数据分组，bs 为 block size，生成量化参数的 shape 为 `(m/bs, k/bs)`。
  - 假设右矩阵 shape 为 `(k, n)`，k 为 reduce 轴，在 k、n 轴上分别按 `(bs, bs)` 块对
    数据分组，bs 为 block size，生成量化参数的 shape 为 `(k/bs, n/bs)`。

## 常见组合量化

- 全量化：一般是指对左、右矩阵均进行量化的模式，包括
  - pertensor-perchannel 量化模式（简称 T-C 量化模式）
  - pertoken-perchannel 量化模式（简称 K-C 量化模式）
  - pergroup-perblock 量化模式（简称 G-B 量化模式）
  - pertensor-perchannel-pergroup 量化模式（简称 T-CG 量化模式）
  - perblock-perblock 量化模式（简称 B-B 量化模式）
- 伪量化：一般是指对权重矩阵（weight）进行量化的模式，包括 perchannel 量化模式
  （简称 C 量化模式）。
- mx 量化：本质是 Microscaling 量化，通过动态调整缩放因子，在极低比特下（如 1bit）
  保持模型精度。这里指 pergroup-pergroup 量化模式（简称 G-G 量化模式），是对于量化
  参数类型为 `FLOAT8_E8M0` 且 group size 为 32 的特例。
