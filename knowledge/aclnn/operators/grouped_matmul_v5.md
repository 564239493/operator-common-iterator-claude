---
module: grouped_matmul_v5
scope: operator
description: aclnnGroupedMatmulV5 的支持场景表与 dimNum/rank 门控
default_load: false
triggers:
  - kind: operator_name_eq
    value: "aclnnGroupedMatmulV5"
depends_on: [expression_language]
---

# aclnnGroupedMatmulV5 专项：场景表 rank 门控（按需加载）

> 原为 `prompts/history/operator_constraints_extract_v4.md` §4.6.3 H，按算子精确命中由
> `select_prompt.py` 装配。原 § 编号保留，便于交叉引用按标题文本定位。
> 仅当算子名精确为 `aclnnGroupedMatmulV5` 时适用，并以当前版本文档的场景表复核。

##### H. 条件维数 / dimNum 门控（支持场景表，v4 增补）

> 来自 aclnnGroupedMatmulV5 闭环：文档有「groupType 支持场景表」，提取器只把表里
> `len()`/value 级约束提升，**未**把「离散参数组合 → weight 维数」逐行提升为跨参数
> 约束，weight 只留 `dimensions=[2,3]` 无条件并集，下游生成器自由产出
> `splitItem=1(separated)+weight 3D` 非法组合，ACL `161002` 拒绝。本节是条件 Shape 的
> 维数特例：**只门控 rank，不门控具体轴**。

**识别信号**（任一出现即触发，本算子恒命中）：

- 文档含「支持场景表 / 场景矩阵 / groupType 支持场景 / splitItem 支持场景」类
  表格，且单元格写 weight/out 的维数；
- 短语「separated → 2D」「单单单 → 3D」「单多多 → 2D」「dimNum=2」「二维 / 三维」
  「X 为 2D / 3D」配合 `groupType`/`splitItem`/单-多 tensor 取值出现；
- 某张量 `dimensions.value` 含 ≥2 个 rank（如 `[2,3]`）且文档同时按离散参数
  区分该张量用哪个 rank。

**门控参数形态**：函数签名里 enum/int 标量
（`groupType.range_value`、`splitItem.range_value`），`is_operator_param.value=true`。

**必须产出**：场景表**每一合法行**一格 `cross_param_constraint`，析取形式
`not(门控条件) or (len(目标.shape) == N)`，门控条件合取该行全部离散键值与 tensor 数；
`relation_params` 同时含门控参数与目标张量（含 tensor 数时一并含相关 TensorList）。
rank 一律写 `len(param.shape)`，**禁止**写 `param.dimNum`（项目约束语言无此字段）。

```text
# R1 separated → weight 2D（不含 len()，生成器 length=null 未修时也能挡）
expr_type: cross_param_constraint
expr: not(splitItem.range_value == 0 or splitItem.range_value == 1) or (len(weight.shape) == 2)
relation_params: ["splitItem", "weight"]
src_text: "weight separated（splitItem=0/1）时 weight 各 tensor 仅支持 2D；groupType 支持场景表"

# R2 groupType 0 单单单 → weight 3D（含 len(weight)，须生成器先写具体 length）
expr_type: cross_param_constraint
expr: not(groupType.range_value == 0 and len(x) == 1 and len(weight) == 1) or (len(weight.shape) == 3)
relation_params: ["groupType", "x", "weight"]
src_text: "groupType=0 单单单 splitItem=2/3 时 weight 为 3D"
```

**规则要点**：

1. **`dimensions` 留全集、门控单独落库**：`weight.dimensions.value=[2,3]` 作为
   「该 tensor rank 宇宙」可保留，但 `constraints_in_parameters` **必须**同时有
   跨参数门控 expr 把 rank 收窄到当前场景；只留并集、无门控 = 漏抽。
2. **析取必须覆盖场景表全部合法行**：漏一行该组合下 rank 无约束，生成器随机赋值；
   多 rank 映射（某场景同时允许 2D/3D）在该行用 `or` 表达。
3. **含 `len(weight)`/`len(x)` 的行依赖 TensorList 具体长度**：若生成器
   `length=null` 未修，这些行不可求值；不含 tensor 数的行（如 R1）应优先落库，
   可立即阻断非法组合。
4. **`expr_type` 选 `cross_param_constraint`**；`derived_value` 不适用
   （非派生值，是 rank 门控）。
5. **反例（禁止）**：`weight.dimensions.value=[2,3]` 且 `constraints_in_parameters`
   无 `len(weight.shape)` 门控 expr → 漏抽；`weight.dimNum == 2` → 字段不存在；
   把 R1/R2 拆成两条互不引用门控参数的无条件 `shape_equality` → 丢失门控。

## 规则要点

- `relation_params` 必须包含目标 tensor 及实际门控参数；逐平台落库，`src_text` 摘录
  当前文档对应场景行。
- 优先使用 `cross_param_constraint`（若当前下游已支持）或等价的可消费关系类型；
  不得因知识中的示例数值覆盖当前文档。

##### K. aclnnGroupedMatmulV5 取值互斥与 weight 布局专项（v2 增补，精确算子）

> 仅当算子名精确为 `aclnnGroupedMatmulV5` 时适用。本节补充 §4.6.3 H（rank 门控）
> 之外的**取值互斥**与**weight 布局确定性**规则，闭环 cases 1/3/4/8（groupListType
> 冲突）与 cases 0/2/7/9（x.k 与 weight 维不匹配）。

**K.1 groupType ↔ groupListType 取值互斥**

文档「约束说明」写明 `groupListType=2：仅全量化且groupType=0场景下支持`（Ascend 950
约束，A3/A2 kernel 同样校验 `When groupListType is 2 only support groupType 0`）。
**必须**产出：

```text
expr_type: cross_param_constraint
expr: (groupType.range_value == 0) if (groupListType.range_value == 2) else True
relation_params: ["groupListType", "groupType"]
src_text: "groupListType=2：仅全量化且groupType=0场景下支持"
```

当 scene_directive 固定 `groupType=-1`（非量化）时，此约束使 `groupListType` 的
合法取值被收窄到 `{0,1}`（`2` 被 `groupType==-1` 排除）。不得只在
`groupListType.description` 备注「不影响计算」。

**K.2 groupType=-1 非量化场景下 weight 布局与 x.k 维关系**

文档 groupType=-1 行：`weight 可转置（统一），shape 为 (n_i,k_i) 或 (k_i,n_i)`，
且「weight 转置时对应 Tensor 必须非连续」。API 签名无 `transposeWeight` 形参，
cases 层使用 shape 顺序表达逻辑布局：非转置为 `(n,k)`，转置为 `(k,n)`。executor
随后把两种 cases 表示物化为 ACLNN 调用所需的 `(k,n)` Tensor；该执行层转换不得反向
改写 cases 层的轴约束。

按 §4.6.3 J，**禁止**写成丢失布局条件的无前提 OR。`groupType=-1 + 非量化` 场景必须
由可物化的 `weight_transposed` 隐式 bool 门控两种 cases shape：

```text
expr_type: shape_value_dependency
expr: (x.shape[1] == weight.shape[1] and weight.shape[0] == out.shape[1] and out.shape[0] == x.shape[0]) if (weight_transposed.range_value == False) else (x.shape[1] == weight.shape[0] and weight.shape[1] == out.shape[1] and out.shape[0] == x.shape[0]) if (weight_transposed.range_value == True) else True
relation_params: ["x", "weight", "out", "weight_transposed"]
src_text: "groupType=-1: x不转置 shape=(m,k); weight 可转置 shape=(n,k)或(k,n);
           weight 转置时对应 Tensor 必须非连续；out shape=(m_i,n_i)。cases 层
           False=(n,k)、True=(k,n)，executor 再按转置状态物化 ACLNN 调用布局。"
```

> **M 轴绑定（v5 增补，R5 验证）**：文档 groupType=-1 行明定「非量化 x，out 中 tensor
> 需为 2 维，shape 分别为（m_i, k_i）和（m_i, n_i）」，即 `x.shape=(m_i,k_i)`、
> `out.shape=(m_i,n_i)`，故 `out.shape[0]=m_i=x.shape[0]`。R4 缺此绑定致 7/10 用例
> EZ1001（`out.shape[0]≠x.shape[0]` 时 GetWorkspaceSize 返回 161002 拒绝），R5 补
> `and out.shape[0] == x.shape[0]` 后 10/0/10 全过。groupType=-1 的场景表另行规定
> splitItem=0/1；K.1 只负责 groupType/groupListType 互斥，不承担 splitItem 门控。

当前 executor 已通过 `weight_transposed=False/True` 的二维 FP32 ND 硬件用例验证：
False 将 cases `(n,k)` 转为连续 `(k,n)`；True 保持 cases `(k,n)` shape/数值并构造
非连续转置 view。NZ、三维 weight、量化以及 `x_transposed=True` 仍需完整回归，不得由
本次二维 ND 结果直接宣称全部布局已验证。

**K.2.1 DIAGNOSE_INFERRED_GUARD：Atlas A3/A2 非量化 FP16/BF16 的 2D NZ N 轴对齐**

Atlas A3/A2 文档规定 `weight` 为 `FRACTAL_NZ` 时 shape 须满足 NZ 格式要求。本项目的
ATK cases 仍以二维逻辑 shape 表示 weight，仅把 `format` 设为 `NZ`。运行
`aclnnGroupedMatmulV5-20260813-075223-209105` 的 R3（88 passed / 12 failed）表明：12 个
失败样本的逻辑 N 轴均不能被 16 整除；成功样本 id 6/8/61 的 K 轴分别为 769/1/65，
虽不能被 16 整除仍执行成功。因此禁止照抄错误消息中的“K/N 双轴整除”，当前经样本
支持的保守生成护栏是逻辑 N 轴对齐：

```text
expr_type: shape_value_dependency
expr: not(weight.format == "NZ") or not(weight.dtype == "FLOAT16" or weight.dtype == "BFLOAT16") or (weight_transposed.range_value == False and weight.shape[0] % 16 == 0) or (weight_transposed.range_value == True and weight.shape[1] % 16 == 0)
relation_params: ["weight", "weight_transposed"]
src_text: "Atlas A3/A2：weight 为 FRACTAL_NZ 格式时 shape 须满足 NZ 格式要求；运行 aclnnGroupedMatmulV5-20260813-075223-209105 R3 的成功/失败真值方向校验支持逻辑 N 轴按 16 对齐，K 轴非 16 倍数仍可成功"
```

适用边界：仅 Atlas A3/A2、非量化、二维逻辑 weight、FP16/BF16、format=NZ。FLOAT32
在本场景仅支持 ND，不由本条处理；ND 不受本条约束。非转置 `(N,K)` 的 N 轴为
`shape[0]`，转置 `(K,N)` 的 N 轴为 `shape[1]`，必须由 `weight_transposed` 门控。

**证据等级与待补测项**：这是 `origin=diagnose_inferred` 的运行经验护栏，不是文档明确
给出的精确公式。现有样本尚不能区分“仅 N 轴对齐”和“K/N 至少一轴对齐”，因为没有
“K 可被 16 整除但 N 不可整除”的判别样本。后续必须补该样本：若失败，确认仅 N 轴
规则；若成功，则应放宽为至少一轴规则。R4 因 executor 在进入算子前统一 KeyError，且
没有生成 NZ 用例，不能视为本约束的二次验证。在判别样本完成前不得把本 guard 描述为
API 文档硬约束或推广到其他算子。

**K.3 bias 1D shape 绑定 out 的 N 轴**

文档 groupType=-1 行：`bias 中 tensor 需为 1 维，shape 为 (n_i)`，out 为
`(m_i,n_i)`。优先使用不依赖 weight 转置状态的直接关系 `bias.shape[0]==out.shape[1]`；
只有 scene 已固定 K.2 的连续 `(k,n)` 布局时，才可额外绑定到 `weight.shape[1]`：

```text
expr_type: shape_equality
expr: biasOptional is None or biasOptional.shape[0] == out.shape[1]
relation_params: ["biasOptional", "out"]
src_text: "groupType=-1: bias 中 tensor shape=(n_i)，out 中 tensor shape=(m_i,n_i)"
```

与既有 `biasOptional is None or len(biasOptional) == len(weight)` 并存（一个约束
TensorList 长度，一个约束每 tensor 的 shape[0]）。同时强化 bias dtype 一致性
（`type_dependency` 第 2 条已正确表达，本节不重复，仅提醒：bias 的 dtype 不得违反
`x.dtype → biasOptional.dtype` 派生关系，case 5 的次要 generator_bug 即源于此）。

##### L. aclnnGroupedMatmulV5 可选参数与临时能力护栏（精确算子）

> 仅当算子名精确为 `aclnnGroupedMatmulV5` 时适用。API 文档语义与当前生成能力必须
> 分开记录：`biasOptional`、`tuningConfigOptional` 都保持可选；临时执行投影不得伪装成
> 永久 API 约束。

**L.1 biasOptional 保持真正可选**

非量化 dtype 表明确允许 bias 为对应 dtype 或 `null`。不得因某轮 cases 全部构造了 bias
而增加强制 present。bias 存在时应用以下条件约束，缺席时保留合法分支：

```text
# bias 与 out 共享 N 轴，不依赖 weight 是否转置
expr_type: shape_equality
expr: biasOptional is None or biasOptional.shape[0] == out.shape[1]
relation_params: ["biasOptional", "out"]

# bias dtype 跟随 x（析取表达）
expr_type: type_dependency
expr: biasOptional is None or (x.dtype == "FLOAT32" and biasOptional.dtype == "FLOAT32") or (x.dtype == "FLOAT16" and biasOptional.dtype == "FLOAT16") or (x.dtype == "BFLOAT16" and (biasOptional.dtype == "BFLOAT16" or biasOptional.dtype == "FLOAT32"))
relation_params: ["biasOptional", "x"]
```

**L.2 TEMP_CAPABILITY_GUARD: ACLINTARRAY_MULTI_VALUE_UNSUPPORTED**

> **临时能力限制，不是 API 文档约束。** 当前生成链路不能可靠表达和物化
> `aclIntArray` 多元素、分索引取值语义；在功能基础验证中若生成非空
> `tuningConfigOptional`，可能产生长度或元素语义不正确的数组。文档仍保持该参数
> `is_optional=true`，并明确“不使用时可传 nullptr”。Atlas A3/A2 文档实际支持非空
> 数组的 `[0]`、`[1]`、`[2]` 三种索引语义；本 guard 是对该合法域的临时执行投影，
> 不是“不支持 tuningConfigOptional”的平台结论。

- **状态**：active（临时）
- **归属**：用例生成/展开能力，不归属于算子 API 语义
- **当前行为**：仅自动生成的功能基础用例固定传 `nullptr`
- **删除触发器**：完成下述解除条件并通过回归后，与 `[null]` 投影一并删除

在当前 guard 生效期间，`aclnnGroupedMatmulV5` 的自动生成执行副本默认不启用调优功能，
将 `tuningConfigOptional` 投影为 nullptr：

```text
{
  "value": [null],
  "type": "enum",
  "src_text": "TEMP_CAPABILITY_GUARD: ACLINTARRAY_MULTI_VALUE_UNSUPPORTED；API 参数可选，当前自动生成暂不支持 aclIntArray 多元素分索引语义，功能基础用例默认不启用调优并传 nullptr"
}
```

**作用范围**：仅自动生成的功能基础用例；不表示手写用例、未来生成器或 API 本身不支持
非空 tuningConfig。不得把 guard 的 `[null]` 解释为文档永久合法域，也不得推广到其他
`aclIntArray` 参数或算子。

**解除条件（全部满足后删除本 guard）**：

1. 生成器能为 `aclIntArray` 表达固定长度及每个索引独立取值/约束；
2. cases JSON 到 executor 的展开能保持数组元素顺序和完整值；
3. `aclIntArray` typed nullptr 与非空数组两条 C 边界路径均有回归测试；
4. 至少覆盖 `tuningConfigOptional=nullptr`、合法 1/2/3 元素数组及非法越界数组，并在
   真实 ATK 上验证预期结果；
5. 删除本段和 `allowed_range_value=[null]` 投影，恢复文档允许的 present/absent 组合。

检索标识固定为 `TEMP_CAPABILITY_GUARD: ACLINTARRAY_MULTI_VALUE_UNSUPPORTED`；能力修复
任务必须全仓搜索该标识，逐项复核并移除，不得静默保留。

##### M. A2/A8W8、groupType=0 单 TensorList 场景补充（精确场景）

> 仅用于 scene 已同时固定为 Atlas A2/A8W8、`groupType=0`，且 `x`、`weight`、
> `out` 均为单 TensorList 的场景；覆盖文档允许的 `groupListType=0/1/2`。不得推广到
> 非量化、伪量化、`groupType=2` 或多 TensorList 场景。

**M.0 3-D A8W8 weight cases 布局（不得套用 K.2 的 2-D 规则）**

文档数学布局及运行 `aclnnGroupedMatmulV5-20260814-090720-714289` case 5 的 K 维错误
共同确认：`weight_transposed=False` 时 cases 已为 `[E,K,N]`，executor 应保持连续且
不得再次转置；`weight_transposed=True` 时 cases 为 `[E,N,K]`，executor 必须执行一次
`transpose(-1,-2)`，向 ACLNN 传入数学布局 `[E,K,N]`。不得用“双重转置”保持原 shape，
否则 K 维仍按 N 解释。K.2 的 false `(N,K)->(K,N)` 仅适用于其明确限定的
`groupType=-1 + 2-D FP32 ND` 场景。

**M.1 biasOptional 与 perTokenScaleOptional 的完整 rank/shape**

该场景中 weight 的归一化数学布局为 `[E,K,N]`，`out` 的单个 tensor 为 `[M,N]`。
`biasOptional` 存在时不是一维 `[N]`，而是二维 `[E,N]`；
`perTokenScaleOptional` 存在时必须是一维 `[M]`。可选参数缺席仍是合法分支，不得把
本轮 present 样本沉淀成强制必传：

```text
expr_type: rank_constraint
expr: biasOptional is None or len(biasOptional.shape) == 2
relation_params: ["biasOptional"]
src_text: "A2/A8W8, groupType=0, single TensorList: biasOptional shape=[E,N]"

expr_type: shape_equality
expr: biasOptional is None or biasOptional.shape[1] == out.shape[1]
relation_params: ["biasOptional", "out"]

expr_type: rank_constraint
expr: perTokenScaleOptional is None or len(perTokenScaleOptional.shape) == 1
relation_params: ["perTokenScaleOptional"]
src_text: "A2/A8W8, groupType=0: perTokenScaleOptional shape=[M]"

expr_type: shape_equality
expr: perTokenScaleOptional is None or perTokenScaleOptional.shape[0] == x.shape[0]
relation_params: ["perTokenScaleOptional", "x"]

expr_type: rank_constraint
expr: scaleOptional is None or len(scaleOptional.shape) == 2
relation_params: ["scaleOptional"]
src_text: "A2/A8W8, groupType=0, single TensorList: scaleOptional 必须为二维"
```

**M.1a biasOptional / scaleOptional 的 E 轴等式（v2 增补，必须落库）**

> 原 `TEMP_CAPABILITY_GUARD: TENSOR_LIST_ELEMENT_SHAPE_INDEX_UNSUPPORTED` 已**解除**（证伪
> 依据见下）。iter_001 闭环 44/50 用例因 `biasOptional/scaleOptional/groupListOptional`
> 的 `shape[0]` 未与分组数 E 绑定而失败（生成器自由产出 `(65534,1)` 等）。同一
> `constraints.json` 已成功使用 `weight.shape[1]`/`weight.shape[2]`（见 K.2 的
> `weight.shape[0]`/`weight.shape[1]` 等式），证明单 TensorList 场景下
> `weight.shape[i]` 指首元素张量第 i 维、可解，故 `weight.shape[0]`（= E = 分组数 g）
> 同样可解。**禁止**用 `len(weight)` 作为 E（会把 E 绑成 TensorList 长度 1）；**禁止**
> 只约束 ndim 不约束 shape[0]。

guard 解除后，`biasOptional.shape[0] == E` 与 `scaleOptional.shape[0] == E` 必须绑定到
`weight.shape[0]`（首元素张量第 0 维 = E = 分组数 g），与上方 M.1 的
`shape[1]==out.shape[1]`、`ndim==2` 并存（一个管 shape[0]=E，一个管 shape[1]=N）。
以下两条 `shape_equality` **必须**逐平台落库：

```text
expr_type: shape_equality
expr: biasOptional is None or biasOptional.shape[0] == weight.shape[0]
relation_params: ["biasOptional", "weight"]
src_text: "A2/A8W8, groupType=0, single TensorList: biasOptional shape=[E,N]，E=weight.shape[0]（分组数g）"

expr_type: shape_equality
expr: scaleOptional is None or scaleOptional.shape[0] == weight.shape[0]
relation_params: ["scaleOptional", "weight"]
src_text: "A2/A8W8, groupType=0, single TensorList: scaleOptional shape=[E,N]或[E,1]，E=weight.shape[0]（分组数g）"
```

E=1（`weight.shape[0]==1`）时 bias/scale 首维被钉死为 1，消除 `(65534,1)` 等非法 shape。
适用边界：仅 A2/A8W8、groupType=0、单 TensorList（weight 3D (E,K,N)）。多 TensorList
（weight 2D）E 由 `len(weight)` 决定；groupType=2 groupNum 取自 `dim(out[0],0)`，不套用
`weight.shape[0]`。

**M.2 groupListType 0/1/2 的 shape 与内容职责分离**

三种类型均包含 tensor **内容序列关系**，不能用当前生成器的单个离散
`range_values` 候选表达，也不得提炼成“每个元素从若干标量 enum 中任选”的 Z3
约束：

- type0：长度 E 的非负、单调非递减累计边界，末项不大于 M；
- type1：长度 E 的非负分组大小，各元素之和不大于 M；
- type2：shape `[E,2]`，每行为 `[groupIdx,groupSize]`，非零组前置，第二列之和不大于 M。

shape/rank 是约束层职责，必须完整表达，尤其不能只约束 type2 为“二维”而漏掉固定的
第二维 `2`：

```text
expr_type: presence_dependency
expr: groupListOptional is not None
relation_params: ["groupListOptional"]
src_text: "A2/A8W8, groupType=0, single TensorList: groupListOptional 为必传输入"

expr_type: cross_param_constraint
expr: groupListOptional is None or groupListType.range_value == 2 or len(groupListOptional.shape) == 1
relation_params: ["groupListOptional", "groupListType"]
src_text: "groupListType=0/1: groupListOptional shape=[E]"

expr_type: cross_param_constraint
expr: groupListOptional is None or not(groupListType.range_value == 2) or (len(groupListOptional.shape) == 2 and groupListOptional.shape[1] == 2)
relation_params: ["groupListOptional", "groupListType"]
src_text: "groupListType=2: groupListOptional shape=[E,2]，每行为[组索引,组大小]"

```

**M.2a groupListOptional 的 E 轴等式（v2 增补，必须落库）**

guard 已解除（见 M.1a 上方），`groupListOptional.shape[0] == E` 必须绑定到
`weight.shape[0]`（首元素张量第 0 维 = E = 分组数 g），与上方 M.2 的 ndim/`shape[1]==2`
约束并存。以下两条 `shape_equality` **必须**逐平台落库：

```text
expr_type: shape_equality
expr: groupListOptional is None or groupListType.range_value == 2 or groupListOptional.shape[0] == weight.shape[0]
relation_params: ["groupListOptional", "weight", "groupListType"]
src_text: "groupListType=0/1: groupListOptional shape=[E]，E=weight.shape[0]（分组数g）；groupList 必传且末值/总和≤x第一维、最大1024组"

expr_type: shape_equality
expr: groupListOptional is None or not(groupListType.range_value == 2) or (groupListOptional.shape[0] == weight.shape[0] and groupListOptional.shape[1] == 2)
relation_params: ["groupListOptional", "weight", "groupListType"]
src_text: "groupListType=2: groupListOptional shape=[E,2]，每行为[组索引,组大小]；E=weight.shape[0]（分组数g）"
```

E=1（`weight.shape[0]==1`）时 groupList 首维被钉死为 1，消除 `(127,)`/`(65,)` 等非法
shape。适用边界同 M.1a：仅 A2/A8W8、groupType=0、单 TensorList。多 TensorList / groupType=2
不套用 `weight.shape[0]`（见 M.1a 边界说明）。

执行器只负责在 shape 已合法时物化内容：将 M 尽量均匀分配到 E 组，type0 生成累计和，
type1 生成每组大小，type2 生成 `[groupIdx,groupSize]`。该策略令覆盖范围末端/总和等于
M，是合法、确定性的测试构造，不是把文档的“不大于 M”收紧为永久 API 约束。
当 E 大于 M 时，非零组排在前面、零大小组排在末尾。executor 遇到错误 shape 必须明确
报错，不得通过 reshape、截断或补齐掩盖约束提取问题。

> **约束层职责边界（内容语义，v2 增补明确）**：`groupListType` 0/1/2 的元素取值序列
> 语义（cumsum/sum/shape(E,2) 第二列总和 ≤ M）由 executor 在 shape 已合法时物化，
> **约束层不产出元素取值 `constraints_in_parameters`，只负责 shape/rank 完整表达**。
> 该策略令覆盖范围末端/总和等于 M，是合法确定性测试构造，不是把文档「不大于 M」
> 收紧为永久 API 约束。

**M.2b 分组数 E 的 ≤1024 上界不等式（v3 新增，必须落库）**

文档「最大 1024 组」（groupType=0 单单单行）与「非量化场景支持最多 1024 个 Tensor」
是同一上界 1024 在不同参数上的体现。`weight.shape[0] <= 1024` 是 E 本身的**数值上界**，
不是 shape 之间的相等/依赖关系，故 `expr_type` **必须**为 `shape_inequality`，不得用
`shape_equality`。这两条与 §M.2a 的 shape_equality 并存：等式管「E 在 weight 和
groupListOptional 间传递一致」，不等式管「E 本身不超过 1024」。iter_002 闭环 10/50
用例因 E=weight.shape[0]=65534（8 例）/1587301（2 例）> 1024 失败，NPU 报
`AclNN_Parameter_Error(EZ1001): size of groupList <E> should be less than or equal to
1024`；extractor 仅把「最大1024组」抄进 src_text 却未在 expr 编码上界（文本记录 ≠
形式化约束）。以下两条 `shape_inequality` **必须**逐平台落库：

```text
expr_type: shape_inequality
expr: weight.shape[0] <= 1024
relation_params: ["weight"]
src_text: "A2/A8W8, groupType=0, single TensorList: 分组数 E=weight.shape[0]，
           文档「最大1024组」（aclnnGroupedMatmulV5.md line 332）；
           iter_002 失败证伪：E=65534/1587301 > 1024 触发 EZ1001"

expr_type: shape_inequality
expr: groupListOptional is None or groupListType.range_value == 2 or groupListOptional.shape[0] <= 1024
relation_params: ["groupListOptional", "groupListType"]
src_text: "groupListType=0/1: groupListOptional shape=[E]，
           dim(groupListOptional,0)≤1024（source_analysis §2.5）；文档「最大1024组」"
```

`weight` 参数 `allowed_range_value.value` 仍为 `[]`（上界由 shape_inequality 承载，
不入 allowed_range_value，符合 nz_matmul §D 的语义修正规则）。适用边界同 §M.1a/§M.2a：
仅 A2/A8W8、groupType=0、单 TensorList。多 TensorList 场景 E 由 `len(weight)` 决定，
上界仍为 1024 但表达式不同（`len(weight) <= 1024`，见 source_analysis §2.9），不在本节
范围；groupType=2 groupNum 取自 `dim(out[0],0)`，不套用 `weight.shape[0]`。

### N. splitItem 与输出 TensorList 长度

`splitItem` 直接决定输出是否分离。文档 return_info 161002 明确拒绝：`splitItem=2/3`
但 `out` 长度不为 1，以及 `splitItem=0/1` 但 `out` 与 `weight` 长度不同。该关系必须
形式化，不能只写进参数说明或错误码描述。优先使用条件表达式保持正向语义清晰：

```text
expr_type: shape_equality
expr: (len(out) == len(weight)) if (splitItem.range_value in [0, 1]) else (len(out) == 1) if (splitItem.range_value in [2, 3]) else True
relation_params: ["splitItem", "out", "weight"]
src_text: "splitItem=0/1 时 out 长度等于 weight 长度；splitItem=2/3 时 out 长度为 1（文档 return_info 161002）"
```

不得仅约束 `len(x) == len(weight)` 后在 `src_text` 中声称三者相等；表达式本身必须覆盖
`out`。该规则与 groupType 场景表一致：groupType=-1 使用 splitItem=0/1，输出多 Tensor；
groupType=0 单单单使用 splitItem=2/3，输出单 Tensor。

### O. A2/A8W4 offset 空场景的执行护栏

仅在 Atlas A2、A8W4、`offsetOptional is None`、weight 为不转置的三维
`[E,K,N]` 场景应用：

```text
expr_type: cross_param_constraint
expr: offsetOptional is not None or groupListType.range_value == 1
relation_params: ["offsetOptional", "groupListType"]
src_text: "A8W4 offset为空时仅支持groupListType=1；算子不检查属性值并按1处理"

expr_type: shape_inequality
expr: weight.shape[2] <= 65535
relation_params: ["weight"]
src_text: "Atlas A2 SPLIT_M 非转置 weight 的逻辑 N 轴不大于65535；运行 aclnnGroupedMatmulV5-20260821-023252-894602 中全部 N>65535 用例均被 N dim mismatch 拒绝"
```

A8W4 offset 空场景已有 `weight.shape[2] % 8 == 0`，与第二条共同作用时生成器实际最大
合法 N 为 65528。INT4 转 INT32 仅压缩 TTK 物理载体的最后一维为 `N/8`，上述表达式
仍约束转换前的逻辑 N，不得误写为物理 INT32 宽度。

`groupListOptional` 的元素内容继续由执行层物化：TTK ACLNN 模式由同一个 `--plugin`
文件中的 `__input__` hook 生成总和等于 M 的合法 count 序列；约束层只固定 type/shape，
不尝试用单一 `range_values` 表达跨元素求和关系。
