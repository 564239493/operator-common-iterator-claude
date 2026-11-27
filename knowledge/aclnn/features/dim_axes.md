---
module: dim_axes
scope: feature
description: dim_axes 场景轴取值模型、未覆盖张量 shape_axes 与「dim_axes 优先且只收窄」派生规则
default_load: false
triggers:
  - kind: doc_contains
    value: "dim_axes|axis_pick|layout_pick"
depends_on: [expression_language]
---

# dim_axes 场景模型（派生规则）

> 本模块只给派生规则；dim_axes.json 的字段定义以 `agent/generators/configs/dim_axes.json`
> 与 `agent/generators/operator_param_models/dim_axes.py` 为准，不在此复制 schema。

## 1. 模型定位

- **axes**：每个场景一套轴的取值模型（`typical` 典型值 / `ranges` 区间（可多段）/
  `expr` 派生轴）。它只描述「每个轴可以取什么值」，与具体参数名弱相关。
- **layouts**：只描述**部分**算子张量在该布局下的轴排列顺序（张量名 → 轴 token 序列）。
  不是所有张量都必须在 layouts 中。
- **axis_pick / layout_pick**：`scheduled` 时按典型值→区间代表值→段内随机推进；
  `round_robin` 布局按用例序号轮转。

## 2. 未在 layouts 声明的张量

- dim_axes `layouts` 之外、但文档给出 shape 轴的张量（如 `learnableSink=(Q_N)`、
  `pseShift`、左/右 padding、公共前缀），由**约束提取**产出参数卡 `shape_axes`
  （token 用场景轴名或整数常量，如 `["N"]`、`["B","N","S","S_k"]`、`[1]`）。
- 生成阶段用 `shape_axes` + 当前场景轴取值组装 shape；无 `shape_axes` 才回退文档/角色
  shape。**不得**因张量不在 layouts 就 fail-closed，也**不得**为了覆盖它去改 dim_axes
  布局或把它一律置空。
- 已由 layouts 覆盖的张量不再写 `shape_axes`。

## 3. dim_axes 优先且只收窄

- 命中 dim_axes 时，**以 dim_axes 场景定义的张量形状为权威**。
- 文档提取的约束与之冲突时：
  1. **禁止删除/放宽**文档约束；
  2. **禁止新增**文档不存在的约束；
  3. 只允许把相关**离散参数取值域**收窄为与 dim_axes 固定形状可满足的子集
     （例：场景 `attenMask` 固定 2048×2048 时，`sparseMode` 去掉需要完整 S×S mask 的
     0/1 模式）。
- 若文档约束比 dim_axes 更严（如需某张量维度为常量），应从 **dim_axes 侧收窄**轴值/
  布局，不得放宽文档；收窄后仍不可满足则报告冲突，禁止臆造或扩大。

## 4. 单布局单形状局限

一个布局只能给张量一种形状，无法表达按参数分支的多形状张量（典型：`attenMask` 在
`sparseMode` 0/1 需完整 `S×S`、2/3/4 需固定 2048×2048）。此时必须：

- 在 dim_axes 场景里选定一个 regime（如把 `attenMask` 固定为 `[2048,2048]`），并让
  参数取值域与之相容；或
- 在场景参数取值阶段用 `param_modes` 把冲突参数收窄到该 regime；必要时拆分不同
  scene 分别覆盖不同 regime。

不要指望在生成/检查阶段靠放宽文档约束兜底。

## 5. 覆盖与用例数

- `required_cases = max(布局数, 各轴 coverage_schedule 长度)`，其中 coverage_schedule =
  典型值 + 每个区间段代表值（段下界）。
- `--case-count` 是每平台总数；多场景时按场景数均分。低于 `required_cases` 时生成会
  fail-closed 报错（`ZERO_CASES_GENERATED`），不得静默少覆盖。
