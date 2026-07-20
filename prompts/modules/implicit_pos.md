---
module: implicit_pos
description: 大小/数量语义参数的隐式 >0 约束
triggers:
  - kind: doc_contains
    value: "长度为[0-9]|数量为|个数|必须大于0|大于0的"
depends_on: []
---

# 模块 implicit_pos（按需加载）

> 本模块原为 `operator_constraints_extract_v4.md` §4.6.9，按算子特征由 `scripts/select_prompt.py` 装配到活跃提示词末尾。原 § 编号保留，便于交叉引用按标题文本定位。

#### 4.6.9 隐式 >0 约束（大小/数量语义参数，v3 增补，通用规则）

> 本节来自大小/数量语义参数的闭环：输出参数（如 `uint64_t*` 标量指针）的
> description 含"元素的数据量""空间大小""元素个数"等短语，语义上必然 > 0，
> 但文档未显式写"大于0"。v3 提示词无规则要求提取这种隐式 >0 约束，导致
> `constraints_in_parameters` 漏掉该约束，下游生成器可能产出 size=0 的非法用例。
> 该规则按 description 中的语义短语触发，**不**按算子名硬编码。

##### A. 适用判定

满足下列**全部**条件时，**必须**执行本节规则：

1. 某**标量取值参数**（输入或输出，常见于一段式算子的标量指针输出如 `uint64_t*` /
   `int64_t*`，但不限于输出）的 `description` 中出现以下语义短语之一（含同义表达）：
   - "空间大小" / "占用空间大小" / "所占用的空间大小"
   - "的数据量" / "元素个数" / "元素的数量"
   - "的数量" / "个数" / "数据量"
   - 其他明确表示该参数的取值是一个"大小 / 数量 / 个数"的短语；
2. 该参数的取值语义为**非负标量计数**（即该参数表示的是元素个数、字节数、空间大小
   等物理量，而非 shape、dtype、format、枚举标签或布尔标志）；
3. 文档**未**显式写明该参数的取值范围（如未出现"大于0"、"≥0"、"[0, ...]"等明确
   数值约束）——若文档已显式给出取值约束，以文档为准，不再追加隐式 >0。

##### B. 不适用场景（禁止套用）

以下场景**不得**套用本规则：

1. `aclTensor` / `aclTensorList` 参数的 shape / dimensions —— shape 各维大小约束
   由 §4.6.3 dimensions 规则与 §6.3 模式 4 `all(d > 0 for d in x.shape)` 处理；
2. `aclIntArray` / `aclFloatArray` / `aclBoolArray` 参数的元素值或长度 —— 数组长度
   约束由 `array_length` 字段和 `len(param)` 表达式处理；
3. `aclDataType`、`aclFormat` 等枚举型参数 —— 其取值是离散标签，不是数值计数；
4. bool 参数 —— 由 §4.6.3 bool 类型参数子节强制 enum 处理；
5. 描述中虽出现"大小""数量"等词，但上下文明确指代 shape 维度、dtype 位宽等非
   标量计数语义的参数。

##### C. 必须产出的 `constraints_in_parameters` 条目

对每个满足适用判定的参数 `P`，必须在 `constraints_in_parameters[每个支持平台]` 中
追加**一条**隐式 >0 约束：

```text
expr_type: value_dependency
expr: P.range_value > 0
relation_params: ["P"]
src_text: "<摘录 description 中空间大小/数据量/元素个数/数量等原文字句>；大小/数量语义隐含 >0"
```

**规则要点**：

1. **expr 模板**：`P.range_value > 0`（与 §4.6.3 allowed_range 映射表"大于0"行的
   `value_dependency: param.range_value > 0` 惯例一致）；`expr_type` 使用
   `value_dependency`（亦可使用 §7.2 的 `self_value_range`，二者均合规，以项目
   既有惯例为准）。
2. **禁止用 `allowed_range_value` 伪造 0 下界**：`allowed_range_value.value` 保持
   `[]`（与 §4.6.3 "大于0"行规则一致：单边/开区间不在 `allowed_range_value` 中
   伪造边界）；`allowed_range_value.type` 保持 `"range"`。不得写成 `[[0, ...]]`
   或 `[[0, null]]` 等。
3. **逐平台落库**：与其它约束一致，`product_support` 中每个平台都必须有对应条目，
   即使各平台 expr 完全相同。
4. **src_text 可溯源**：`src_text` 必须摘录 description 中表示"大小/数量/个数"的
   原文字句，并补注"大小/数量语义隐含 >0"，使隐式下界的推导依据可追溯。
5. **不重复落库**：若文档已显式写明 `P > 0` 或 `P >= 1` 等取值约束并已按 §4.6.3
   allowed_range 映射表"大于0"行落库了对应的 `value_dependency` 条目，则**不再**
   追加本节隐式 >0 条目（避免重复）。
6. **仅针对标量取值参数**：本规则只针对"表示大小/数量/个数"的标量取值参数（如
   `uint64_t*` / `int64_t*` / `size_t*` 等标量指针输出或标量输入），不针对
   shape/dtype/format/枚举值。

