---
module: dimensions
scope: common
description: dimensions.value 的 rank 解析与 shape/长度边界
default_load: true
triggers: []
depends_on: []
---

# `dimensions.value` 解析规则

`dimensions` 只表示 `aclTensor`/`aclTensorList` 的 rank，不表示轴长度、数组长度或
TensorList 元素个数；非 Tensor 类型必须为 `[]`。“N 维”写离散 rank；连续范围按项目
v4 合同表达，多个离散 rank 不得误合并出文档未支持的中间 rank。`(N,C,H,W)`、`[B,S,H]`
等 shape 元组只证明 rank；轴值及参数间关系进入 `constraints_in_parameters`。
“最大长度 256”“数组支持 2-6 维”对 `aclIntArray` 等非 Tensor 表示
`array_length`/`len(array)`，禁止写 `array.shape`。

## §解析表

| 原文形态 | `dimensions.value` | 备注 |
| -------- | ------------------ | ---- |
| `"0-8"` / `"2~6"` | `[0, 8]` / `[2, 6]` | Rank 区间 |
| `"2D"` / `"3-D"` | `[2, 2]` / `[3, 3]` | Rank 精确（D 后缀） |
| `"1D~8D"` / `"2维~8维"` | `[1, 8]` / `[2, 8]` | 带 D / 维 后缀的区间 |
| `"1维"` / `"3维"` | `[1, 1]` / `[3, 3]` | 中文精确 |
| `"1维，最大长度256"` | `[1, 1]`（长度256 不在此字段） | 长度限制另入 `constraints_in_parameters` |
| `"(N,C,H,W)"` | `[4, 4]` | 符号元组，按逗号槽数 |
| `"(H*rankSize, N)"` | `[2, 2]` | 复合表达式，按槽数 |
| `"[2, 3, 4]"` | `[[2,2],[3,3],[4,4]]` | 纯数值 → per-dim |
| `"[8]"` | `[[8,8]]` | 单维数值 |
| `"[0-100, 0-200]"` | `[[0,100],[0,200]]` | per-dim 带区间 |
| `"标量"` / `"0-D"` / `"scalar"` | `[]` | 标量 |
| `""` / `"-"` / `"N/A"` | `[]` | 未说明 |
| `"与输入相同"` / `"与xxx一致"` | `[]` | 跨参数引用，留给约束表达 |

`[E,N1] / [N1]` 等多种 shape 要保留全部 rank 候选，并用 `shape_choice` 或
`shape_value_dependency` 保存分支，不能只留下无条件候选并集。

## §维数与长度区分

- “N 维”描述的是 tensor 的**维度数（rank）**，应输出 `[N, N]`；
- “最大长度 M” / “最大长度为 M”描述的是某一维的**大小限制**，**不属于 `dimensions`**；
- 该大小限制应由 `constraints_in_parameters` 中的 `self_shape_axis_value` 约束表达；
- **反例**：把 `"1维，最大长度256"` 解析为 `[[1, 256]]`（per-dim 格式）属于错误。

## §HTML列表型shape

当 shape 描述里出现 `<ul><li>` + 多种方括号变体（如 `[E, N1]/[N1]`）（量化参数特有）：

1. 从原文抽取**所有** `[...]` 方括号组；
2. 每个组内按逗号槽数 = rank；
3. 取所有变体的 rank 区间作为 `dimensions.value`；

示例：`<ul><li>per-channel...[E, N1]/[N1]</li><li>per-group...[E, G, N1]/[G, N1]</li></ul>`
→ `[E,N1]=2`、`[N1]=1`、`[E,G,N1]=3`、`[G,N1]=2` → 最终 `dimensions.value=[1, 3]`。

## §校验规则

- rank 格式：`0 ≤ min ≤ max ≤ 10`；
- per-dim 格式：每维 `min ≤ max`（或 `null`），最多 10 维；
- `[]` 永远合法。

## §裸数值区间 / 悬空参数引用的维度须交叉核对示例

当张量参数表「维度(shape)」列是纯数值区间（如 `0-8`），或「使用说明」引用签名不存在的
参数名（如 `self`）时，该列取值**不得**直接形式化为 `dimensions.value` 的 rank 区间；必须
交叉核对「调用示例」的显式 shape 声明（如 `outShape = {batch, n}`）确定真实 rank，据示例
槽数写固定 rank（如 `[2,2]`），并补 `y.shape[0]==batch`、`y.shape[1]==n` 的
`shape_value_dependency`；`src_text` 同时摘录「维度(shape)」列与「调用示例」两处原文；
悬空参数（self）不生成跨参数约束。

**反例**：aclnnGroupedMatmulFinalizeRouting 输出 y「维度(shape)」列写 `0-8`、「使用说明」写
`shape与self相同`（该算子无 self 参数），提取器直接形式化 `[0,8]`，生成器产出 0 维/8 维 y，
100/100 失败（`must have at least 2 dimensions` / `should be equal to 2, but is 8`）；正确做法
是核对 `outShape={batch,n}` → 固定 `[2,2]`。
