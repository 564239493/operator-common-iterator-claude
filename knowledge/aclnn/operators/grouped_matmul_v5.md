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
