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

FFNV3 是两级 MatMul，参数表中的同名维度符号必须绑定到真实 Tensor 轴，不能只保留
`K1 == N2` / `N1 == K2` 等符号关系而漏掉输入 Reduce 轴。无 expert 时至少产出：

```text
x.shape[-1] == weight1.shape[-2]
weight1.shape[-2] == weight2.shape[-1]
```

其中第一条对应 `x=[M,K1]` 与 `weight1=[K1,N1]` 的第一个 MatMul Reduce 轴；第二条
对应文档公共约束 `K1=N2`。有 expert 时 `weight1=[E,K1,N1]`，仍使用
`x.shape[-1] == weight1.shape[-2]`。GLU 激活还必须按 §F 产出
`weight1.shape[-1] == 2 * weight2.shape[-2]`。

### §B expert 模式 shape 变体

下表把 `weight1` / `weight2` / `bias*Optional` / `deqScale*Optional` / `antiquantScale*Optional` / `antiquantOffset*Optional` 在「无 expert / 有 expert」两态下的 shape 列齐；weight rank 与 expert presence 必须使用表后 `if/else` 合法组合表达，不使用布尔 `==`，也不只用 rank 自身充当场景门控：

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

expert presence 与 weight rank 的双向关系必须按当前生成链路兼容形式枚举两个合法
分支，禁止写布尔 `==`，也禁止遗漏 `else` 而把所有场景固定为无 expert：

```text
(len(weight1.shape) == 2 and len(weight2.shape) == 2) if (expertTokensOptional is None) else (len(weight1.shape) == 3 and len(weight2.shape) == 3)
```

有 expert 时，表中的三个 `E` 是同一个专家数，必须用真实轴绑定，不能留下游离的
`E.range_value`：

```text
expertTokensOptional is None or (len(expertTokensOptional.shape) == 1 and expertTokensOptional.shape[0] <= 256 and weight1.shape[0] == weight2.shape[0] and expertTokensOptional.shape[0] == weight1.shape[0])
```

### §C non-quant / quant / pseudo-quant 场景与 dtype 分组

C.1 三类场景的 presence 必须机器可判定，不能只写在 `description/src_text`：

- non-quant：`scaleOptional`、`offsetOptional`、`deqScale1Optional`、
  `deqScale2Optional`、全部 `antiquantScale*Optional` 和 `antiquantOffset*Optional`
  均为 `None`；
- quant：全部 `antiquantScale*Optional` / `antiquantOffset*Optional` 为 `None`；
- pseudo-quant：`scaleOptional`、`offsetOptional`、`deqScale1Optional`、
  `deqScale2Optional` 为 `None`。

当当前 run 的场景指令只选择 non-quant 时，必须直接产出第一组全空约束，禁止仍从这些
量化参数的普通 optional presence 域中随机取“存在”。若当前 run 同时保留多种模板，
量化组 `Q={scaleOptional, offsetOptional, deqScale1Optional, deqScale2Optional}` 与
伪量化组 `A={antiquantScale1Optional, antiquantScale2Optional,
antiquantOffset1Optional, antiquantOffset2Optional}` 必须互斥。对 `Q × A` 的每一对参数
分别产出一条独立约束：

```text
not (scaleOptional is not None and antiquantScale1Optional is not None)
not (scaleOptional is not None and antiquantScale2Optional is not None)
...
not (deqScale2Optional is not None and antiquantOffset2Optional is not None)
```

共 16 条。不得把两组“全空”写成 OR-of-ANDs；当前缺参清理器在两个 OR 分支均化简为
False 时会把空 OR 变成 True，导致量化组和伪量化组同时 present 仍被放行。上述
`not (Q is not None and A is not None)` 在任一参数缺席时稳定化简为 True，在两者同时
存在时化简为 False，并允许两组同时为空的 non-quant。

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
- `innerPrecise` 与 `tokensIndexFlag` 是函数签名中的值类型必选参数，不抽取
  `is None` / `is not None` presence 约束；“可选高精度/高性能”描述的是取值模式，
  不是参数可以缺席；
- BFLOAT16 非 quant 模式：`innerPrecise == 0`；
- 加速卡模式：`innerPrecise == 1`。

### §E 公共形状等价与边界

`K1 == N2`、`K1 < 65536`、`K2 < 65536`、M 轴（按 32 字节对齐后）落在 INT32 范围内。

`expertTokensOptional`（存在时）：`len(expertTokensOptional.shape) == 1`、
`expertTokensOptional.shape[0] == weight1.shape[0]`、
`weight1.shape[0] == weight2.shape[0]`、`expertTokensOptional.shape[0] <= 256`；
以上条件放在 `expertTokensOptional is None or (...)` 守卫后，不抽取游离的 `E.range_value`。

`tokensIndexFlag == true` 且 expert 模式：`expertTokensOptional` 的相邻元素必须**单调非递减**。若生成器侧无法形式化表达值层单调性，则保持 source 文本且**仍**要求 tensor presence/shape 约束入约束。

### §F 平台 scenes 矩阵

#### F.1 A2

BFLOAT16 scenes 仅 **Atlas 800I A2 推理产品**支持（落为 `platform=Atlas 800I A2` 独占条目）。

#### F.2 scenes 支持（按 `activation` × quant/non-quant × expert）

| `activation` | expert / non-expert 模式 | supported scenes |
|---|---|---|
| `gelu` / `fastgelu` / `relu` / `silu` | expert 或 non-expert | FLOAT16 高精度 / 高性能、BFLOAT16、quant、pseudo-quant |
| `geglu` / `swiglu` / `reglu` | 仅 non-expert | FLOAT16 高性能（`innerPrecise == 1`） |

对 `geglu/swiglu/reglu`，不能只限制 `y.dtype`。必须产出下列可执行约束共同绑定
必选 Tensor dtype/rank、专家 presence 和计算模式；不要把 presence 守卫与属性条件
混在同一个 `and` 中，否则当前求解器会将其解释为蕴含而弱化约束：

```text
not(activation.range_value in ["geglu", "swiglu", "reglu"]) or (x.dtype == "FLOAT16" and weight1.dtype == "FLOAT16" and weight2.dtype == "FLOAT16" and y.dtype == "FLOAT16" and len(weight1.shape) == 2 and len(weight2.shape) == 2)
not(activation.range_value in ["geglu", "swiglu", "reglu"]) or expertTokensOptional is None
not(activation.range_value in ["geglu", "swiglu", "reglu"]) or innerPrecise.range_value == 1
```

GLU 分支的 optional bias 若存在也只能为 FLOAT16，另产出：

```text
not(activation.range_value in ["geglu", "swiglu", "reglu"]) or ((bias1Optional is None or bias1Optional.dtype == "FLOAT16") and (bias2Optional is None or bias2Optional.dtype == "FLOAT16"))
```

GLU 只支持 non-quant，因此同一分支还必须保证 §C.1 的所有 quant / pseudo-quant 参数
均为 `None`，必须另产出可执行约束，不能只留自然语言：

```text
not(activation.range_value in ["geglu", "swiglu", "reglu"]) or (scaleOptional is None and offsetOptional is None and deqScale1Optional is None and deqScale2Optional is None and antiquantScale1Optional is None and antiquantScale2Optional is None and antiquantOffset1Optional is None and antiquantOffset2Optional is None)
```

若场景指令已将 activation 收窄为 GLU 集合，可将上述蕴含分别化简为直接约束，但不得
把 presence 和属性条件重新合并成会触发守卫蕴含改写的同一个 `and`，也不得删除任何
dtype、rank、presence、non-quant 或 `innerPrecise` 条件。

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
scaleOptional is None or scaleOptional.shape != [1] or offsetOptional is not None
scaleOptional is None or scaleOptional.shape != [1] or offsetOptional is None or offsetOptional.shape == [1]

# expert presence/rank 合法组合及共同 E 轴
(len(weight1.shape) == 2 and len(weight2.shape) == 2) if (expertTokensOptional is None) else (len(weight1.shape) == 3 and len(weight2.shape) == 3)
expertTokensOptional is None or (len(expertTokensOptional.shape) == 1 and expertTokensOptional.shape[0] <= 256 and weight1.shape[0] == weight2.shape[0] and expertTokensOptional.shape[0] == weight1.shape[0])

# optional shape 直接引用真实 weight 轴，不创建 E/K/N 隐式随机变量
bias1Optional is None or ((len(weight1.shape) == 2 and bias1Optional.shape == [weight1.shape[-1]]) or (len(weight1.shape) == 3 and bias1Optional.shape == [weight1.shape[0], weight1.shape[-1]]))
bias2Optional is None or ((len(weight2.shape) == 2 and bias2Optional.shape == [weight2.shape[-1]]) or (len(weight2.shape) == 3 and bias2Optional.shape == [weight2.shape[0], weight2.shape[-1]]))
deqScale1Optional is None or ((len(weight1.shape) == 2 and deqScale1Optional.shape == [weight1.shape[-1]]) or (len(weight1.shape) == 3 and deqScale1Optional.shape == [weight1.shape[0], weight1.shape[-1]]))
deqScale2Optional is None or ((len(weight2.shape) == 2 and deqScale2Optional.shape == [weight2.shape[-1]]) or (len(weight2.shape) == 3 and deqScale2Optional.shape == [weight2.shape[0], weight2.shape[-1]]))
scaleOptional is None or (scaleOptional.shape == [1] or scaleOptional.shape == [weight1.shape[-1]] or (len(weight1.shape) == 3 and scaleOptional.shape == [weight1.shape[0]]) or (len(weight1.shape) == 3 and scaleOptional.shape == [weight1.shape[0], weight1.shape[-1]]))
offsetOptional is None or (offsetOptional.shape == [1] or (len(weight1.shape) == 3 and offsetOptional.shape == [weight1.shape[0]]))

# 量化互斥：对 Q×A 的 16 对参数逐对产出，以下为首尾示例
not (scaleOptional is not None and antiquantScale1Optional is not None)
not (deqScale2Optional is not None and antiquantOffset2Optional is not None)

# INT4 末维偶数
weight1.dtype != "INT4" or (weight1.shape[-1] % 2 == 0)
weight2.dtype != "INT4" or (weight2.shape[-1] % 2 == 0)

# 公共真实轴关系与边界
x.shape[-1] == weight1.shape[-2]
weight1.shape[-2] == weight2.shape[-1]
weight1.shape[-2] < 65536
weight2.shape[-2] < 65536
```

## 规则要点

1. **不要写硬编码特例到 v4 主提示词**——本模块始终按算子名触发装配，不污染通用规则。
2. **`src_text` 双轨制**：source-backed 规则 `src_text` 引用文档原文；NPU 反馈规则 `src_text` 写 `"NPU 真机测量反馈"`，避免误把真机数据当文档事实。
3. **expert 模式 shape 变体必须带 `if len(weight.shape) == 3`** 分支，否则无 expert 的常见路径会被错误约束到 expert 形态。
4. **量化互斥** 是 C.1，必须用一条 OR 表达式覆盖两组同时为 `None` 的合法空白情形，不要写成"必须有 quant"或"必须有 pseudo-quant"。
5. **不替代通用 dtype 互推导 / 类型提升 / broadcast 规则**：本模块专注 FFNV3 算子内特异性，公共规则仍由 `knowledge/aclnn/features/broadcast.md` 承载。
6. **.claude/settings.json / guard_project_writes.py**：本模块不引入新的硬编码常量或平台枚举；新增平台时按 v4 §5.1 流程扩展。
7. **两级 MatMul 轴完整性**：反扫必须同时看到 `x.shape[-1] == weight1.shape[-2]`、
   `K1 == N2` 的真实轴等式以及 activation 对 `N1/K2` 的分支约束；缺少任一项都视为
   约束提取不完整。
8. **GLU 联合场景完整性**：`geglu/swiglu/reglu` 分支必须同时约束四个必选 Tensor 为
   FLOAT16、无 expert、weight1/weight2 均为 2D、`innerPrecise=1`、non-quant 参数组
   全空，且 bias1/bias2 若存在必须为 FLOAT16；禁止拆成只有 `y.dtype` 的弱约束。
9. **presence 等价兼容性**：所有“expertTokens 缺席 ↔ weight 为 2D”关系必须用
   `if/else` 同时枚举“缺席+2D”和“存在+3D”两个合法分支，禁止输出布尔 `==` 或
   presence/rank OR-of-ANDs；不得遗漏 `else` 而把双向关系退化成无条件缺席。
