---
module: sparse_mode_foundation
scope: foundation
description: 官方 attention sparseMode 场景与参数语义原文参考
default_load: false
triggers:
  - kind: doc_contains
    value: "sparseMode|sparse_mode|稀疏模式|attenMask|preTokens|nextTokens"
depends_on: []
---

# sparseMode 介绍（官方概念参考，按需加载）

> 本模块是 CANN 官方 `sparse_mode参数说明.md` 的原始概念参考，含 sparseMode 0～8 各
> 模式的语义与 preTokens/nextTokens/attenMask 取值约束。**派生约束规则**（如何把
> 支持的 mode 落为场景 guard 并联合门控）在 `features/sparse_mode.md`；两者区分显示，
> 本模块不替代派生规则。原理图见官方文档，本文本以语义描述为准。

## sparseMode 取值表

| sparseMode | 含义 | 备注 |
| --- | --- | --- |
| 0 | defaultMask 模式。 | - |
| 1 | allMask 模式。 | - |
| 2 | leftUpCausal 模式。 | - |
| 3 | rightDownCausal 模式。 | - |
| 4 | band 模式。 | - |
| 5 | prefix 非压缩模式。 | varlen 场景不支持。 |
| 6 | prefix 压缩模式。 | - |
| 7 | varlen 外切场景，rightDownCausal 模式。 | 仅 varlen 场景支持。 |
| 8 | varlen 外切场景，leftUpCausal 模式。 | 仅 varlen 场景支持。 |

attenMask 的工作原理为，在 Mask 为 True 的位置遮蔽 query(Q) 与 key(K) 的转置矩阵
乘积的值。$QK^T$ 矩阵在 attenMask 为 True 的位置会被遮蔽。

## sparseMode=0

sparseMode 为 0 时，代表 defaultMask 模式。

- 不传 mask：如果 attenMask 未传入则不做 mask 操作，attenMask 取值为 None，忽略
  preTokens 和 nextTokens 取值。
- nextTokens 取值为 0，preTokens 大于等于 Sq，表示 causal 场景 sparse，attenMask 应
  传入下三角矩阵，此时 preTokens 和 nextTokens 之间的部分需要计算。
- preTokens 小于 Sq，nextTokens 小于 Skv，且都大于等于 0，表示 band 场景，此时
  preTokens 和 nextTokens 之间的部分需要计算。attenMask 应传入 band 形状矩阵。
- nextTokens 为负数，以 preTokens=9，nextTokens=-3 为例，preTokens 和 nextTokens
  之间的部分需要计算。
  - **说明：nextTokens 为负数时，preTokens 取值必须大于等于 nextTokens 的绝对值，
    且 nextTokens 的绝对值小于 Skv。**
- preTokens 为负数，以 nextTokens=7，preTokens=-3 为例，preTokens 和 nextTokens
  之间的部分需要计算。
  - **说明：preTokens 为负数时，nextTokens 取值必须大于等于 preTokens 的绝对值，
    且 preTokens 的绝对值小于 Sq。**

## sparseMode=1

sparseMode 为 1 时，代表 allMask，即传入完整的 attenMask 矩阵。该场景下忽略
nextTokens、preTokens 取值。

## sparseMode=2

sparseMode 为 2 时，代表 leftUpCausal 模式的 mask，对应以左上顶点划分的下三角场景
（参数起点为左上角）。该场景下忽略 preTokens、nextTokens 取值。传入的 attenMask 为
优化后的压缩下三角矩阵（2048×2048）。

## sparseMode=3

sparseMode 为 3 时，代表 rightDownCausal 模式的 mask，对应以右下顶点划分的下三角场景
（参数起点为右下角）。该场景下忽略 preTokens、nextTokens 取值。attenMask 为优化后的
压缩下三角矩阵（2048×2048）。

## sparseMode=4

sparseMode 为 4 时，代表 band 场景，即计算 preTokens 和 nextTokens 之间的部分，参数
起点为右下角，preTokens 和 nextTokens 之间需要有交集。attenMask 为优化后的压缩下三角
矩阵（2048×2048）。

## sparseMode=5

sparseMode 为 5 时，代表 prefix 非压缩场景，即在 rightDownCausal 的基础上，左侧加上
一个长为 Sq、宽为 N 的矩阵，N 的值由可选输入 prefix 获取，例如 batch=2 场景下 prefix
传入数组 `[4,5]`，每个 batch 轴的 N 值可以不一样，参数起点为左上角。

该场景下忽略 preTokens、nextTokens 取值，attenMask 矩阵数据格式须为 BNSS 或 B1SS。

## sparseMode=6

sparseMode 为 6 时，代表 prefix 压缩场景，即 prefix 场景时，attenMask 为优化后的压缩
下三角 + 矩形的矩阵（3072×2048）：其中上半部分 `[2048, 2048]` 的下三角矩阵，下半部分
为 `[1024, 2048]` 的矩形矩阵，矩形矩阵左半部分全 0，右半部分全 1。该场景下忽略
preTokens、nextTokens 取值。

## sparseMode=7

sparseMode 为 7 时，表示 varlen 且为长序列外切场景（即长序列在模型脚本中进行多卡切
query 的 sequence length）；用户需要确保外切前为使用 sparseMode 3 的场景；当前 mode
下用户需要设置 preTokens 和 nextTokens（起点为右下顶点），且需要保证参数正确，否则会
存在精度问题。

- 卡1的最后一块 mask 为 band 类型的 mask，配置 preTokens=6（保证大于等于最后一个
  Skv），nextTokens=-2，actual_seq_qlen 应传入 `{3,5}`，actual_seq_kvlen 应传入
  `{3,9}`。
- 卡2的 mask 类型切分后不变，sparseMode 为 3，actual_seq_qlen 应传入 `{2,7,11}`，
  actual_seq_kvlen 应传入 `{6,11,15}`。

**说明**：

- sparseMode=7，band 表示的是最后一个非空 tensor 的 Batch 的 sparse 类型；如果只有
  一个 batch，用户需按照 band 模式的要求来配置参数；sparseMode=7 时，用户需要输入
  2048×2048 的下三角 mask 作为该融合算子的输入。
- 基于 sparseMode=3 进行外切产生的 band 模式的 sparse 参数应符合以下条件：
  - `preTokens >= last_Skv`。
  - `last_Sq - last_Skv <= nextTokens <= 0`。
  - 当前模式下不支持可选输入 pse。
- 非 band 模式的 batch 应满足：`Sq <= Skv`。

## sparseMode=8

sparseMode 为 8 时，表示 varlen 且为长序列外切场景；用户需要确保外切前为使用
sparseMode 2 的场景；当前 mode 下用户需要设置 preTokens 和 nextTokens（起点为右下
顶点），且需要保证参数正确，否则会存在精度问题。

- 卡1的 mask 类型切分后不变，sparseMode 为 2，actual_seq_qlen 应传入 `{3,5}`，
  actual_seq_kvlen 应传入 `{3,7}`。
- 卡2的第一块 mask 为 band 类型的 mask，配置 preTokens=4（保证大于等于第一个 Skv），
  nextTokens=1，actual_seq_qlen 应传入 `{3,8,12}`，actual_seq_kvlen 应传入
  `{4,9,13}`。

**说明**：

- sparseMode=8，band 表示的是第一个非空 tensor 的 Batch 的 sparse 类型；如果只有
  一个 batch，用户需按照 band 模式的要求来配置参数；sparseMode=8 时，用户需要输入
  2048×2048 的下三角 mask 作为该融合算子的输入。
- 基于 sparseMode=2 进行外切产生的 band 模式的 sparse 的参数应符合以下条件：
  - `preTokens >= first_Skv`。
  - `nextTokens >= first_Sq - first_Skv`，根据实际情况进行配置。
  - 当前模式下不支持可选输入 pse。
