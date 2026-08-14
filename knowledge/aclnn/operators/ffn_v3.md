---
module: ffn_v3
description: aclnnFFNV3 MoE/FFN 专家模式、quant/pseudo-quant 互斥、bias/deqScale/antiquant shape 变体、NPU 真机反馈规则。
triggers:
  - kind: operator_name_eq
    value: "aclnnFFNV3"
depends_on: []
---

# 模块 ffn_v3（按需加载）

> 本模块原为 `knowledge/operator_patterns/ffn_v3.md` 的实战经验（含 NPU 真机测量反馈），按算子名由 `scripts/select_prompt.py` 装配到活跃提示词末尾。该规则按 `operator_name=="aclnnFFNV3"` 触发，**不**扩散到其他算子。

## 适用判定

满足下列**任一**条件时，执行本模块规则：

1. 算子文档的标题或首段明确为 `aclnnFFNV3`；
2. 文档描述同时含 MoE/FFN 专家模式且参数表里出现 `weight1`、`weight2`、`expertTokensOptional`、`bias*Optional`、`deqScale*Optional`、`antiquant*Optional`、`scaleOptional`、`offsetOptional`。

## 必须产出

`constraints_in_parameters` 中**至少**包含下列条目类型（具体条目见本节后子节）：

- §A — A2 训练 / 加速卡激活函数 dtype 等式与 `N1 == K2` / `N1 == 2*K2` 维度耦合；
- §B — bias / deqScale / antiquant 在「有无 expert」二态下的 shape 变体（标 `if shape rank 2 vs 3` 分支）；
- §C — quant 模式 vs pseudo-quant 模式 dtype 分组（含 `y/x/bias/antiquant*` 与 weight dtype 配对）；
- §D — INT4 weight 末维偶数 + `innerPrecise` 取值与 dtype/scenes 的耦合；
- §E — `K1 == N2`、`K1 < 65536`、`K2 < 65536`、M 轴 32 字节对齐后落在 INT32 范围；
- §F — A2 上 BFLOAT16 仅 Atlas 800I A2 推理产品支持；激活函数维度的 scenes 支持矩阵；
- §G — 标 `src_text="NPU 真机测量"` 的 NPU feedback 规则（与 source-backed 规则分块）。

### §A 激活函数 / 维数耦合

A2 训练：

- `activation.allowed_range_value = ["fastgelu", "gelu", "relu", "silu", "geglu", "swiglu", "reglu"]`

加速卡：

- `activation.allowed_range_value = ["fastgelu", "gelu", "relu", "silu"]`

`N1 == K2`（对 `gelu/fastgelu/relu/silu`）/`N1 == 2 * K2`（对 `geglu/swiglu/reglu`）作为形状等式落库，`src_text` 引用文档「`activation` 维度映射」章节。

### §B expert 模式 shape 变体

下表把 `weight1` / `weight2` / `bias*Optional` / `deqScale*Optional` / `antiquantScale*Optional` / `antiquantOffset*Optional` 在「无 expert / 有 expert」两态下的 shape 列齐，使用 `if len(weight1.shape) == 3` 形式（参考 v4 §6.3 模式 8）：

| 参数 | shape 条件 | shape |
|---|---|---|
| `weight1` | 无 expert | `[K1, N1]` |
| `weight1` | 有 expert | `[E, K1, N1]` |
| `weight2` | 无 expert | `[K2, N2]` |
| `weight2` | 有 expert | `[E, K2, N2]` |
| `bias1Optional` | 无 expert，存在 | `[N1]` |
| `bias1Optional` | 有 expert，存在 | `[E, N1]` |
| `bias2Optional` | 无 expert，存在 | `[N2]` |
| `bias2Optional` | 有 expert，存在 | `[E, N2]` |
| `deqScale1Optional` | 无 expert，存在 | `[N1]` |
| `deqScale1Optional` | 有 expert，存在 | `[E, N1]` |
| `deqScale2Optional` | 无 expert，存在 | `[N2]` |
| `deqScale2Optional` | 有 expert，存在 | `[E, N2]` |
| `scaleOptional` | 无 expert，存在 | `[1]`（per-tensor）/ `[N1]`（per-channel） |
| `scaleOptional` | 有 expert，存在 | `[E]`（per-tensor）/ `[E, N1]`（per-channel） |
| `offsetOptional` | 无 expert，存在 | `[1]` |
| `offsetOptional` | 有 expert，存在 | `[E]` |
| `antiquantScale1Optional` / `antiquantOffset1Optional` | 无 expert，存在 | `[N1]`（per-channel）/ `[G, N1]`（per-group） |
| `antiquantScale1Optional` / `antiquantOffset1Optional` | 有 expert，存在 | `[E, N1]`（per-channel）/ `[E, G, N1]`（per-group） |
| `antiquantScale2Optional` / `antiquantOffset2Optional` | 无 expert，存在 | `[N2]`（per-channel）/ `[G, N2]`（per-group） |
| `antiquantScale2Optional` / `antiquantOffset2Optional` | 有 expert，存在 | `[E, N2]`（per-channel）/ `[E, G, N2]`（per-group） |

### §C quant 模式 / pseudo-quant 模式 dtype 分组

C.1 quant 与 pseudo-quant **互斥**：同一组可量化参数组（`scaleOptional` / `offsetOptional` / `deqScale*Optional` / `antiquant*Optional`）中，quant 与 pseudo-quant 二者只能出现其一。落库为 `(scaleOptional is None and antiquantScale1Optional is None and antiquantOffset1Optional is None and antiquantScale2Optional is None and antiquantOffset2Optional is None) or (deqScale1Optional is None and deqScale2Optional is None and offsetOptional is None)`。

C.2 quant 模式：

- `bias*Optional.dtype` ∈ {`INT32`}；
- `scaleOptional.dtype` ∈ {`FLOAT32`}；
- `offsetOptional.dtype` ∈ {`FLOAT32`}；
- `deqScale1Optional.dtype == deqScale2Optional.dtype`；
- `deqScale*Optional.dtype` 与 `y.dtype` 的耦合：
  - `y.dtype == FLOAT16`，per-tensor：`{UINT64, INT64, FLOAT32}`；
  - `y.dtype == FLOAT16`，per-channel：`{UINT64, INT64}`；
  - `y.dtype == BFLOAT16`：`{BFLOAT16}`。

C.3 pseudo-quant 模式（两套分组，按文档 dtype 表逐行落库）：

- 分组 1：`y/x/bias/antiquantScale/antiquantOffset = FLOAT16`，weight dtype ∈ {`INT8`, `INT4`}；
- 分组 2：`y/x/antiquantScale/antiquantOffset = BFLOAT16`、`bias = FLOAT32`，weight dtype ∈ {`INT8`, `INT4`}。

C.4 pseudo-quant per-group：`G > 0` 且 `K1 % G == 0`（group-1 参数） / `K2 % G == 0`（group-2 参数）。

### §D INT4 weight 末维偶数 + `innerPrecise`

- 若 `weight1.dtype == "INT4"`，则 `weight1.shape[-1] % 2 == 0`；`weight2` 同理。
- `innerPrecise.dtype == int`，`allowed_range_value = [0, 1]`；
- BFLOAT16 非 quant 模式：`innerPrecise == 0`；
- 加速卡模式：`innerPrecise == 1`。

### §E 公共形状等价与边界

`K1 == N2`、`K1 < 65536`、`K2 < 65536`、M 轴（按 32 字节对齐后）落在 INT32 范围内。

`expertTokensOptional`（存在时）：`len(expertTokensOptional.shape) == 1`、`expertTokensOptional.shape[0] == E.range_value`、`expertTokensOptional.shape[0] <= 256`。

`tokensIndexFlag == true` 且 expert 模式：`expertTokensOptional` 的相邻元素必须**单调非递减**。若生成器侧无法形式化表达值层单调性，则保持 source 文本且**仍**要求 tensor presence/shape 约束入约束。

### §F 平台 scenes 矩阵

#### F.1 A2

BFLOAT16 scenes 仅 **Atlas 800I A2 推理产品**支持（落为 `platform=Atlas 800I A2` 独占条目）。

#### F.2 scenes 支持（按 `activation` × quant/non-quant × expert）

| `activation` | expert / non-expert 模式 | supported scenes |
|---|---|---|
| `gelu` / `fastgelu` / `relu` / `silu` | expert 或 non-expert | FLOAT16 高精度 / 高性能、BFLOAT16、quant、pseudo-quant |
| `geglu` / `swiglu` / `reglu` | 仅 non-expert | FLOAT16 高性能（`innerPrecise == 1`） |

加速卡模式不支持 expert：`expertTokensOptional is None`、所有 quant / pseudo-quant 可选参数 `None`、`N1 == K2`。

### §G NPU 真机测量反馈（必须与 source-backed 规则分块）

下列规则来自 NPU 真机执行反馈，落库时**必须**在每条 `constraints_in_parameters` 的 `src_text` 字段写明 `"NPU 真机测量反馈"` 以与文档事实区分：

- `weight1.dtype == weight2.dtype`；
- 浮点 weight 模式要求 `y.dtype == x.dtype`；
- quant 模式要求 quant 参数组齐全：`scaleOptional`、`offsetOptional`、`deqScale1Optional`、`deqScale2Optional`；
- 测得的 non-expert per-tensor quant 路径下，`scaleOptional.shape == [1]` 且 `offsetOptional.shape == [1]`。有 expert 时 per-tensor 为 `[E]`，依 source doc，不属本条。

## 表达式模板（参考）

下列表达式作为起始模板，使用时按当前 operators/products 平台与 presence 调整：

```text
# NPU 真机测量反馈
weight1.dtype == weight2.dtype
y.dtype == x.dtype
(scaleOptional is not None and scaleOptional.shape == [1]) implies offsetOptional.shape == [1]

# shape 条件分支（参考 v4 §6.3 模式 8）
(weight1.shape == [E.range_value, K1.range_value, N1.range_value]) if (len(weight1.shape) == 3) else (weight1.shape == [K1.range_value, N1.range_value])
(weight2.shape == [E.range_value, K2.range_value, N2.range_value]) if (len(weight2.shape) == 3) else (weight2.shape == [K2.range_value, N2.range_value])
(bias1Optional is None) or ((bias1Optional.shape == [N1.range_value]) if (len(weight1.shape) == 2) else (bias1Optional.shape == [E.range_value, N1.range_value]))
(bias2Optional is None) or ((bias2Optional.shape == [N2.range_value]) if (len(weight2.shape) == 2) else (bias2Optional.shape == [E.range_value, N2.range_value]))
(deqScale1Optional is None) or ((deqScale1Optional.shape == [N1.range_value]) if (len(weight1.shape) == 2) else (deqScale1Optional.shape == [E.range_value, N1.range_value]))
(deqScale2Optional is None) or ((deqScale2Optional.shape == [N2.range_value]) if (len(weight2.shape) == 2) else (deqScale2Optional.shape == [E.range_value, N2.range_value]))
(scaleOptional is None) or (scaleOptional.shape == [1] or scaleOptional.shape == [E.range_value] or scaleOptional.shape == [N1.range_value] or scaleOptional.shape == [E.range_value, N1.range_value])
(offsetOptional is None) or (offsetOptional.shape == [1] or offsetOptional.shape == [E.range_value])

# 量化互斥
(scaleOptional is None and offsetOptional is None and deqScale1Optional is None and deqScale2Optional is None) or (antiquantScale1Optional is None and antiquantScale2Optional is None and antiquantOffset1Optional is None and antiquantOffset2Optional is None)

# INT4 末维偶数
(weight1.dtype == "INT4") implies (weight1.shape[-1] % 2 == 0)
(weight2.dtype == "INT4") implies (weight2.shape[-1] % 2 == 0)

# 公共维度边界
K1.range_value == N2.range_value
K1.range_value < 65536
K2.range_value < 65536
```

## 规则要点

1. **不要写硬编码特例到 v4 主提示词**——本模块始终按算子名触发装配，不污染通用规则。
2. **`src_text` 双轨制**：source-backed 规则 `src_text` 引用文档原文；NPU 反馈规则 `src_text` 写 `"NPU 真机测量反馈"`，避免误把真机数据当文档事实。
3. **expert 模式 shape 变体必须带 `if len(weight.shape) == 3`** 分支，否则无 expert 的常见路径会被错误约束到 expert 形态。
4. **量化互斥** 是 C.1，必须用一条 OR 表达式覆盖两组同时为 `None` 的合法空白情形，不要写成"必须有 quant"或"必须有 pseudo-quant"。
5. **不替代通用 dtype 互推导 / 类型提升 / broadcast 规则**：本模块专注 FFNV3 算子内特异性，公共规则仍由 `knowledge/aclnn/features/broadcast.md` 承载。
6. **.claude/settings.json / guard_project_writes.py**：本模块不引入新的硬编码常量或平台枚举；新增平台时按 v4 §5.1 流程扩展。
