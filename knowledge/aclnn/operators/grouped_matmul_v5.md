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
expr: not(groupListType.range_value == 2) or (groupType.range_value == 0)
relation_params: ["groupListType", "groupType"]
src_text: "groupListType=2：仅全量化且groupType=0场景下支持"
```

当 scene_directive 固定 `groupType=-1`（非量化）时，此约束使 `groupListType` 的
合法取值被收窄到 `{-2?}` 之外即 `{0,1}`（`2` 被 `groupType==-1` 排除）。不得只在
`groupListType.description` 备注「不影响计算」。

**K.2 groupType=-1 非量化场景下 weight 布局与 x.k 维关系**

文档 groupType=-1 行：`weight 可转置（统一），shape 为 (n_i,k_i) 或 (k_i,n_i)`，
且「weight 转置时对应 Tensor 必须非连续」。API 签名无 `transposeWeight` 形参，
`cases_executor.py` 默认构造**连续** weight，kernel 按连续 weight 解释为 `(k,n)`
布局，要求 `weight.shape[0] == x.shape[1]`（K 轴）。

按 §4.6.3 J（v2 增补），**禁止**写成无前提 OR。当前执行路径固定连续 weight 即
`(k,n)` 布局，**必须**产出确定性等式：

```text
expr_type: shape_value_dependency
expr: x.shape[1] == weight.shape[0] and weight.shape[1] == out.shape[1] and out.shape[0] == x.shape[0]
relation_params: ["x", "weight", "out"]
src_text: "groupType=-1: x不转置 shape=(m,k); weight 可转置 shape=(n,k)或(k,n);
           weight 转置时对应 Tensor 必须非连续。当前执行路径下 weight 连续即 (k,n)
           布局，K=weight.shape[0]、N=weight.shape[1]。文档明定 out 中 tensor 需为
           2 维 shape=(m_i,n_i)，故 out.shape[0]=m_i=x.shape[0]（M 轴绑定）。"
```

> **M 轴绑定（v5 增补，R5 验证）**：文档 groupType=-1 行明定「非量化 x，out 中 tensor
> 需为 2 维，shape 分别为（m_i, k_i）和（m_i, n_i）」，即 `x.shape=(m_i,k_i)`、
> `out.shape=(m_i,n_i)`，故 `out.shape[0]=m_i=x.shape[0]`。R4 缺此绑定致 7/10 用例
> EZ1001（`out.shape[0]≠x.shape[0]` 时 GetWorkspaceSize 返回 161002 拒绝），R5 补
> `and out.shape[0] == x.shape[0]` 后 10/0/10 全过。splitItem 在 groupType=-1 下仅
> 0/1（§K.1 已门控），每个 out 与对应 x 的 M 轴一一对应，无需按 splitItem 门控。

若后续 scene 选定转置分支且执行路径支持非连续 weight，则按
`knowledge/aclnn/features/transpose_shape.md` 引入 `weight_transposed` 隐式 bool
+ if/else 门控 K 轴位置（False→`weight.shape[0]`，True→`weight.shape[1]`），并落
`value_dependency`：`not(weight_transposed.range_value == True) or (weight 非连续)`
（非连续性若无法在约束层形式化，记入 `weight.description`，不产出空 `expr`）。
`weight_transposed.allowed_range_value.value` 在非量化场景为 `[false, true]`；当
执行路径不支持非连续时收窄到 `[false]`。

**K.3 bias 1D shape 绑定 weight 的 N 轴**

文档 groupType=-1 行：`bias 中 tensor 需为 1 维，shape 为 (n_i)`。`n_i` 即 weight
的 N 轴。在 K.2 的 `(k,n)` 布局下，`N = weight.shape[1]`。**必须**把 bias shape
绑定到 weight 的 N 轴（而非仅 `out.shape[1]`，`out.shape[1]` 是派生量）：

```text
expr_type: shape_equality
expr: biasOptional is None or biasOptional.shape[0] == weight.shape[1]
relation_params: ["biasOptional", "weight"]
src_text: "groupType=-1: bias 中 tensor 需为 1 维 shape 为 (n_i)；weight (k,n) 布局下 n_i=weight.shape[1]"
```

与既有 `biasOptional is None or len(biasOptional) == len(weight)` 并存（一个约束
TensorList 长度，一个约束每 tensor 的 shape[0]）。同时强化 bias dtype 一致性
（`type_dependency` 第 2 条已正确表达，本节不重复，仅提醒：bias 的 dtype 不得违反
`x.dtype → biasOptional.dtype` 派生关系，case 5 的次要 generator_bug 即源于此）。

##### L. aclnnGroupedMatmulV5 可选参数 presence 与缺席专项（v3/v4 增补，精确算子）

> 仅当算子名精确为 `aclnnGroupedMatmulV5` 时适用。本节按 §4.6.3 L（通用 presence
> 强制与缺席表达规则）落本算子的精确条目。R2 bias shape/dtype 失活、R3
> tuningConfigOptional 缺席未生效均经本系列条目修复并在 R5 全过验证。

**L.1 非量化场景 biasOptional presence 强制（present）**

非量化场景（`groupType=-1`，由 scene_directive `fix` 选定）下 `biasOptional` 恒
present（`cases_executor` 按 present 构造 bias TensorList）。**必须**产出：

```text
expr_type: presence_dependency
expr: not(groupType.range_value == -1) or (biasOptional is not None)
relation_params: ["groupType", "biasOptional"]
src_text: "groupType=-1 非量化: bias 可选但 cases_executor 恒按 present 构造；强制 is_present=True 以激活 bias shape/dtype 约束"
```

使其与既有 bias dtype（`type_dependency`）与 bias shape（`shape_equality`）协同：
`is_present` 被锁 True 后，`biasOptional is None or ...` 守卫归约到后续约束，Z3 必须绑定
`biasOptional.shape[0] == weight.shape[1]` 与 `biasOptional.dtype == x.dtype`。

**L.2 bias shape 与 dtype 约束的协同确认**

既有约束（保持不变，仅确认 presence 强制后它们才生效）：

```text
# bias shape 绑定 weight N 轴（(k,n) 布局下 N=weight.shape[1]）
expr_type: shape_equality
expr: biasOptional is None or biasOptional.shape[0] == weight.shape[1]
relation_params: ["biasOptional", "weight"]

# bias dtype 跟随 x（析取表达）
expr_type: type_dependency
expr: biasOptional is None or (x.dtype == "FLOAT32" and biasOptional.dtype == "FLOAT32") or (x.dtype == "FLOAT16" and biasOptional.dtype == "FLOAT16") or (x.dtype == "BFLOAT16" and (biasOptional.dtype == "BFLOAT16" or biasOptional.dtype == "FLOAT32"))
relation_params: ["biasOptional", "x"]
```

L.1 的 `presence_dependency` 使上述两条守卫的 `biasOptional is None` 析取支归约为
False（`is_present==True` ⇒ `biasOptional is not None`），Z3 必须满足后续 shape/dtype
等式，bias shape/dtype 不再自由。

**L.3.1 非量化场景 tuningConfigOptional 缺席（v4 纠正）**

`tuningConfigOptional` 是可选 `aclIntArray`（`is_optional.value=true`），文档
return_info 记载「tuningConfigOptional 的元素为负数，或者大于 x 的行数 m」触发 161002；
Atlas A2 平台约束记载 tuningConfigOptional 各元素适用场景为 A8W4/A8W8/A4W4（量化场景）。
非量化场景（`groupType=-1`）下该参数无适用语义，应传空（nullptr）。按 §4.6.3 L 缺席
表达规则，**必须**在 `inputs.tuningConfigOptional.<platform>.allowed_range_value` 写：

```text
{
  "value": [null],
  "type": "enum",
  "src_text": "非量化场景 tuningConfigOptional 须传空（nullptr）；数组型调优参数当前生成机制不支持逐元素取值约束，按不传处理"
}
```

（与 `scaleOptional`/`offsetOptional`/`antiquantScaleOptional`/`groupListOptional` 等
非量化缺席参数写法一致。）

**必须删除**原 v3 `presence_dependency: not(groupType.range_value == -1) or
(tuningConfigOptional is None)` 条目——该写法仅设 Z3 `is_present`、不进
`force_false_params` 集合，下游 attrs 仍按 present 生成非空取值（已证伪）。
`analysis_param_is_present` 按参数名遍历、不区分 aclTensorList/aclIntArray，故
`[null]` enum 对 aclIntArray 同样适用。

**L.3.2 量化场景 tuningConfigOptional 取值上界守卫（保留 + 机制限制注解）**

> 为量化分支预留（当前 groupType=-1 不生效）。补注：量化分支若启用，
> `tuningConfigOptional` 的逐元素取值约束受生成机制限制（当前机制不支持数组不同
> 索引不同数值），建议同样按不传（`allowed_range_value=[null]` enum）或单值
> （`allowed_range_value={"value":[v],"type":"enum"}`）处理，避免逐元素 ForAll
> 约束在下游不可执行。若需约束全部元素：
> `tuningConfigOptional is None or ForAll(i, 0 <= i < len(tuningConfigOptional) implies tuningConfigOptional[i] <= x.shape[0])`。
