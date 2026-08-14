---
module: nz_matmul
scope: feature
description: NZ/FRACTAL_NZ 张量布局与块尺寸通用硬约束
default_load: false
triggers:
  - kind: format_any
    value: ["NZ", "FRACTAL_NZ", "FRACTAL_NZ_C0_16", "FRACTAL_NZ_C0_32"]
depends_on: [expression_language]
---

# NZ / FRACTAL_NZ 通用知识（按需加载）

> 原为 `prompts/history/operator_constraints_extract_v4.md` §4.6.5 + §6.3 模式 5，按算子特征由
> `select_prompt.py` 装配。原 § 编号保留，便于交叉引用按标题文本定位。本模块只含
> **通用** NZ 块尺寸规则；`aclnnBatchMatMulWeightNz` 的转置隐式 bool 与
> `shape_value_dependency` 门控模板在精确算子模块
> `operators/batch_matmul_weight_nz.md` 中加载。

#### 4.6.5 NZ 格式块尺寸硬约束（v2 新增，通用规则）

> 覆盖**所有**昇腾 NZ / FRACTAL_NZ / FRACTAL_NZ_C0_16 张量算子（如
> `aclnnBatchMatMulWeightNz`、`aclnnGroupedMatmulV5` 等），不针对单一算子。

##### A. 适用判定

参数 `format.value`（按平台）包含 `"NZ"` / `"FRACTAL_NZ"` /
`"FRACTAL_NZ_C0_16"` 时，先区分 cases 使用哪一种 shape 表示：

1. **物理 NZ shape**：`dimensions.value` 为 `[5, 5]`，或文档明确给出
   `(b,n1,k1,k0,n0)` / `(b,k1,n1,n0,k0)` 等物理维度元组，执行 §B～§C.1；
2. **逻辑矩阵 shape**：cases 中仍用 2D `(K,N)` / `(N,K)` shape，仅通过
   `format="NZ"` 触发内部转换；且算子文档、所链接的 NZ 格式规范或经过成功/失败样本
   真值方向验证的运行证据明确要求逻辑 K/N 按块对齐时，执行 §C.2。

禁止把物理 5D NZ 的 `shape[3]==16/shape[4]==16` 直接套到 2D 逻辑 shape；也禁止只因
出现 `format="NZ"` 就臆造块尺寸。2D 对齐的块值、适用 dtype 和轴必须有明确依据。

##### B. NZ 维度元组识别（关键：必须区分转置 / 非转置两种布局）

文档可能同时出现两种 NZ 形态：

| 布局 | 维度元组（mat2 形状说明） | 块尺寸赋值 | 典型算子上下文 |
| ---- | ------------------------- | ---------- | -------------- |
| **非转置 NZ**（B 矩阵不转置） | `(b, n1, k1, k0, n0)` | `k0 = 16`、`n0 = 16` | mat2 沿 K×N 摆放，未发生 transpose |
| **转置 NZ**（B 矩阵转置） | `(b, k1, n1, n0, k0)` | `n0 = 16`、`k0 = 16` | mat2 沿 N×K 摆放，转置后第 3 维为 `n0`、第 4 维为 `k0` |

**注意**：两种布局的**块尺寸都等于 16**，但 `k0` / `n0` **落在 shape 的轴位不同**。
提取时必须按文档原文维度元组的顺序逐位对齐，**不允许**直接以 `k0=16`、`n0=16`
得出"shape[3]=16 且 shape[4]=16"的统一结论——必须区分：

- 非转置：`mat2.shape[3] == 16`（对应 `k0`）、`mat2.shape[4] == 16`（对应 `n0`）；
- 转置：`mat2.shape[3] == 16`（对应 `n0`）、`mat2.shape[4] == 16`（对应 `k0`）。

两种布局的"块尺寸等于 16"在数值上等价，**但表达必须分别落库**，否则后续会
被 `shape_value_dependency` 的 ceil 关系合并匹配掩盖。

##### C. 必须产出的 `constraints_in_parameters` 条目

###### C.1 物理 5D NZ shape

对每个支持 NZ 的平台，必须在 `constraints_in_parameters[平台]` 中追加**至少**以下
两条 `shape_equality`（也可用 `shape_value_dependency`，但推荐 `shape_equality`
因更易被生成器识别为硬等式）：

```text
# 非转置 NZ（文档原文出现 (b, n1, k1, k0, n0) 且 k0=16, n0=16）
expr_type: shape_equality
expr: mat2.shape[3] == 16
relation_params: ["mat2"]
src_text: "NZ格式各个维度表示：（b, n1，k1，k0，n0），其中k0 = 16， n0为16。"

expr_type: shape_equality
expr: mat2.shape[4] == 16
relation_params: ["mat2"]
src_text: "NZ格式各个维度表示：（b, n1，k1，k0，n0），其中k0 = 16， n0为16。"
```

```text
# 转置 NZ（文档原文出现 (b, k1, n1, n0, k0) 且 n0=16, k0=16）
expr_type: shape_equality
expr: mat2.shape[3] == 16
relation_params: ["mat2"]
src_text: "NZ格式各个维度表示：（b, k1，n1，n0，k0），其中n0 = 16， k0为16。"

expr_type: shape_equality
expr: mat2.shape[4] == 16
relation_params: ["mat2"]
src_text: "NZ格式各个维度表示：（b, k1，n1，n0，k0），其中n0 = 16， k0为16。"
```

**规则要点**：

1. **不可省略**：若文档描述了 NZ 块尺寸为 16，无论是否同时出现 `ceil(...)` 关系，
   `mat2.shape[3] == 16` 与 `mat2.shape[4] == 16` **必须**落库。
2. **不可依赖 `shape_value_dependency` 推导**：现有的
   `(self.shape[2] + 15) // 16 == mat2.shape[1] or ...mat2.shape[2]` 仅约束
   `shape[1]`/`shape[2]`，**无法**反推 `shape[3]`/`shape[4]` 必须为 16。
3. **不可写成不等式**：`0 < mat2.shape[3]` 这种不等式无法表达块尺寸硬约束。
4. **同一平台的两种布局必须分别落库**：当文档同时描述非转置 + 转置 NZ 时，
   必须分别为两套布局产出 `shape[3]==16` / `shape[4]==16` 约束对（每对两条，
   共 4 条），并在 `src_text` 中分别摘录对应原文。**不允许**用单条
   `mat2.shape[3] in [16]` / `mat2.shape[3] == 16` 一笔带过。
5. **`k0` / `n0` 不写入 `relation_params`**：尽管 `src_text` 引用 `k0=16`、`n0=16`，
   关系参数列表只写 `["mat2"]`；`k0` / `n0` 已在隐式参数知识中标为 constant，无需
   在约束条目中再列。

###### C.2 2D 逻辑 shape + NZ format

当用例结构保留二维逻辑矩阵 shape，而文档/格式规范/已确认内核诊断明确要求 K、N
分别按 `BK`、`BN` 对齐时，必须生成带 format（必要时再带 dtype）门控的整除约束：

```text
expr_type: shape_value_dependency
expr: not(weight.format == "NZ") or not(weight.dtype == "FLOAT16" or weight.dtype == "BFLOAT16") or (weight.shape[0] % 16 == 0 and weight.shape[1] % 16 == 0)
relation_params: ["weight"]
src_text: "<摘录算子文档/链接格式规范中的 NZ 对齐要求；若来自已确认内核诊断，明确标注验证范围>"
```

- 二维矩阵的两轴都要求 16 对齐时，转置只交换 K/N 名称，不改变“两维均 `%16==0`”的
  表达式，无需复制转置分支。
- 若不同 dtype 的块大小不同，必须拆成 dtype 门控分支，不得一律写 16。
- 若只有 K 或 N 单轴要求对齐，必须结合真实 transpose/布局状态定位对应 shape 轴；不得
  为求简便同时约束两轴。
- 此规则约束的是 shape，不得写入 `allowed_range_value`。
- 内核错误消息中即使写成 `(K,N) divisible by [BK,BN]`，也不能跳过真值方向校验。
  错误文本本身不构成双轴对齐依据，不得单独据此沉淀 K/N 双轴约束。
  应同时检查失败样本和成功反例：若存在 K 非对齐但成功的样本，禁止继续沉淀双轴约束；
  需按转置状态分别计算逻辑 K/N，找出真正决定成功/失败的轴。经验样本仍不能区分两个
  候选规则时，必须在 operator 知识中记录不确定性和补测判别样本，不得伪装成文档定论。

##### D. `allowed_range_value` 不承载块尺寸硬约束（语义修正）

> **关键澄清**：`allowed_range_value` 是**参数取值范围**，对 tensor 参数指**元素数据值**
> 范围。生成器按**元素值**解释，从不解释为逐维 shape 区间。**任何 shape 维度硬约束
> 一律落 `constraints_in_parameters`（见 §C 的 `shape_equality`），禁止塞进
> `allowed_range_value`。**

按平台，对 `mat2`（或任何 NZ 张量）的 `allowed_range_value` 字段：

1. 仅约束 shape 块尺寸（`k0=16`、`n0=16`）、未约束 mat2 元素取值的算子，
   `allowed_range_value.value=[]`（空）、`src_text=""`；块尺寸 16 的硬约束由 §C 的
   `shape_equality` 独立承载，与本字段无关；
2. **禁止**把 `[[16, 16], [16, 16]]` / `[[16, 16]]` 写入 `allowed_range_value`
   冒充 shape 块尺寸约束——生成器会按元素值范围解释：pairwise 路径因 `[16, 16]`
   非 scalar 被丢弃，Z3 路径会生成 `mat2.range_value[0] > 16 and < 16` 的空区间
   （UNSAT），二者均为语义错位；
3. 仅当文档**显式约束该 tensor 元素的数值取值范围**（如"取值 [0, 1]"、"仅 0/1"）
   时，才按 range / enum 通用规则填 `allowed_range_value`，端点严禁为 `null`；shape
   维度的其他取值（如 pad 支持 `32`）仍走 `shape_equality`，不入本字段。

**反例（禁止）**：
- `allowed_range_value.value=[[16, 16], [16, 16]]`、`type=range`，企图表达
  `shape[3]` / `shape[4]` 块尺寸 → 语义滥用，生成器按元素值解释，违本节 D.2。
- `allowed_range_value.value=[[16, 16]]` 但 `constraints_in_parameters` 无
  `mat2.shape[3]==16` / `mat2.shape[4]==16` → 漏抓，违规则 C.1。
- `constraints_in_parameters` 仅写 `mat2.shape[3] == 16`（未写 `shape[4]`）→
  不完整，违规则 C.1。

##### E. 与隐式维度变量的协作

- `k0` / `n0` 一律标为 `constant`，`constant_value=16`；**不**在 `inputs` 中产出
  隐式维度变量卡片（区别于 `(N, C, H, W)` 中的 `N`、`C` 等）。
- 块尺寸 `k0 = 16` / `n0为16` 的原文溯源由 §C 各 `shape_equality` 条目的
  `src_text` 承载（摘录 NZ 维度元组原文）；`allowed_range_value` 留空时无需 `src_text`。
- `mat2.allowed_range_value` 仅在文档显式约束元素取值时填写，端点严禁为 `null`；
  shape 块尺寸约束不入本字段（见 §D）。

#### 模式 5：NZ 块尺寸硬约束（v2 新增）

**适用场景**：5D NZ 张量的 `shape[3]` 与 `shape[4]` 必须等于块尺寸 16（`k0` 或 `n0`）。

```text
# 非转置 NZ：shape[3]=k0=16, shape[4]=n0=16
mat2.shape[3] == 16
mat2.shape[4] == 16

# 转置 NZ：shape[3]=n0=16, shape[4]=k0=16
mat2.shape[3] == 16
mat2.shape[4] == 16
```

两种布局的 `expr` 形式完全相同，但 `src_text` **必须**分别摘录对应原文
（如非转置摘 `(b, n1, k1, k0, n0)，其中k0 = 16， n0为16`；转置摘
`(b, k1, n1, n0, k0)，其中n0 = 16， k0为16`），且作为 `constraints_in_parameters`
中**不同条目**落库。

#### 模式 6：2D 逻辑矩阵的 NZ 对齐（v3 新增）

**适用场景**：cases 仍使用二维逻辑 shape，且有明确依据说明当前 dtype 下 K/N 均需
按 16 对齐。

```text
not(weight.format == "NZ")
or not(weight.dtype == "FLOAT16" or weight.dtype == "BFLOAT16")
or (weight.shape[0] % 16 == 0 and weight.shape[1] % 16 == 0)
```

该模式与模式 5 互斥：模式 5 约束物理 5D shape 的内块轴等于 16；模式 6 约束二维逻辑
shape 的矩阵轴可被 16 整除。

## 边界

本模块不包含任何 `aclnnBatchMatMulWeightNz` 专属变量（`self_transposed`/
`mat2_transposed`）或候选顺序。该算子的转置隐式 bool 与 `shape_value_dependency`
门控模板只在精确模块 `operators/batch_matmul_weight_nz.md` 中加载。
