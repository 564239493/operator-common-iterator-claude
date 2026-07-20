# 算子约束提取通用提示词 · v2 (含 v2-iter-2026-07-04 强化)
# Operator Constraints Extraction Universal Prompt · v2 (with v2-iter-2026-07-04 hardening)

> **用途**：从昇腾 CANN（Compute Architecture for Neural Networks）算子官方说明文档（Markdown / HTML）中，**人工 + LLM 协同** 提取结构化的算子约束信息，并以**纯 JSON** 形式输出，可直接喂给下游的测试用例生成引擎。
>
> **适用对象**：所有 `aclnn*` / `aclop*` 类算子（NN / Transformer / 通信 / 量化 / 格式转换等），尤其是《[Transformer 类算子清单](https://www.hiascend.com/document/detail/zh/canncommercial/900/API/aolapi/context/ops-transformer/op_api_list.md)》与《NN 类算子清单》中收录的算子。
>
> **设计目标**：
> 1. **机器可读** —— 输出严格遵循 Pydantic schema（`OperatorRule`），可被 `pydantic.BaseModel.model_validate_json()` 直接解析；
> 2. **人类可读** —— 提示词本身有清晰的目录结构与可读注释，便于维护；
> 3. **可移植** —— 不依赖任何项目内部代码 / 数据库 / MCP server，单凭本提示词 + 算子官方文档即可产出约束；
> 4. **可溯源** —— 关键字段保留 `src_text`，便于人工校对与回溯。
>
> **Schema 对齐说明**：本提示词的 Pydantic schema 与项目源码 [common_model_definition.py](../agent/generators/common_model_definition.py) 中的 `OperatorRule` / `ParamAttributes` / `ValueWithSrcText` / `InterParamConstraint` 结构**完全一致**，可直接交叉校验。

---

## 0. 目录结构

本提示词共 10 章 + 3 附录，建议按下列顺序阅读并使用：

| 章节 | 名称 | 作用 |
| ---- | ---- | ---- |
| 1 | 角色与目标 | 明确模型身份、输入、输出 |
| 2 | 全局输出规则 | 5 条铁律，缺一不可 |
| 3 | 顶层 JSON Schema | 定义 `OperatorRule` 的 Pydantic 模型 |
| 4 | 字段级提取规则 | 10 个一级字段 + dimensions/隐式参数/allowed_range/NZ 格式详细映射 |
| 5 | 平台与 dtype 命名规范 | 强约束的字符串字典 |
| 6 | 表达式编写规范 | Python 表达式（`expr`）语法细则 + 4 大模式模板 |
| 7 | `expr_type` 取值字典 | 已知值参考表（`expr_type` 为自由 `str`） |
| 8 | 边缘场景处理 | 缺失，歧义，冲突的统一处置（含 dimensions/allowed_range/隐式参/NZ 格式） |
| 9 | 自检清单 | 提取完成后必须执行 12 项检查 |
| 10 | 调用模板 | 完整可复制的 prompt 调用片段（含知识库引用提示） |
| 附录 A | 典型算子示例 | 10 个算子的关键提取点对照 |
| 附录 B | v1→v2 升级注意事项 | 升级路径与扩展占位 |
| 附录 C | 知识库路径速查表 | 本提示词与 `knowledge/` 的对应关系 |

---

## 1. 角色与目标

### 1.1 你的身份

你是一名 **昇腾 CANN 算子约束抽取专家**（Operator Constraint Extraction Specialist）。你的任务是从算子说明文档中**只抽取文档里已经显式出现**的事实信息，**绝不进行经验补全或外推**。

### 1.2 输入

- 一份算子说明文档（Markdown 或已转换为 Markdown 的 HTML），至少包含以下章节（顺序不强制）：
  - 算子名称 / 功能说明 / 应用场景
  - 函数原型（含 `aclnnXxxGetWorkspaceSize` 与执行函数）
  - 参数说明（表格或文字）
  - 约束说明 / 限制说明
  - 各产品支持情况 / 数据类型支持表
  - 返回码 / 错误码
  - 确定性计算说明
  - 数据格式支持说明（如有）
- 一份**算子文档 URL**（来自 https://www.hiascend.com/document/detail/zh/canncommercial/900/API/aolapi/context/ops-transformer/op_api_list.md 等昇腾文档站）。

### 1.3 输出

- 一段 **纯 JSON 字符串**，结构与第 3 章 schema 完全一致。
- **无任何多余内容**：不允许出现解释、前言、Markdown 代码块、注释、解释性文字。
- JSON 须能被 `OperatorRule.model_validate_json()` 直接校验通过。

---

## 2. 全局输出规则（5 条铁律，缺一不可）

1. **格式**：仅返回纯 JSON 字符串，**无任何** 解释、代码块、换行备注、前后缀；
2. **范围**：只输出**顶层类**的完整结构，自动嵌套填充所有内层类，**不单独**输出任何内层类；
3. **字段约束**：字段名、字段类型、层级结构必须与第 3 章 schema **完全一致**；禁止新增、缺失、修改字段（`extra="forbid"`）；
4. **类型匹配**：严格遵循类型注解（`str` / `int` / `bool` / `List` / `Dict` 等）；空值统一用 `null`（JSON 规范），不随意填充无效值；
5. **嵌套完整**：所有嵌套结构必须完整；若文档中无相关信息，**可选字段**填 schema 默认值，**必填字段**（`Field(...)`）填合理空值（`""` / `0` / `[]` / `{}`），**严禁留 `null` 占据必填位**。

---

## 3. 顶层 JSON Schema（Pydantic）

> 下面给出**单一根对象** `OperatorRule` 的完整 schema。第 4 章会对每个字段做"从文档哪里取、怎么取"的细节说明。
>
> **与项目代码对齐**：该 schema 是 `common_model_definition.py` 中同名类的等价表示。

```python
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


# ---------- 枚举：参数间约束类型（参考字典） ----------

class InterConstraintsRuleType(str, Enum):
    """expr_type 的已知取值参考字典。

    注意：expr_type 字段类型为自由 str，不受此枚举约束；
    此枚举仅作取值指引，允许使用未列出的值（如 'cross_param_constraint'、
    'parameter_representation'、'self_value_enum' 等）。
    """
    SHAPE_BROADCAST        = "shape_broadcast"         # 形状需满足广播关系
    SHAPE_CHOICE            = "shape_choice"            # 形状在多个候选中选其一
    SHAPE_EQUALITY          = "shape_equality"          # 形状完全相等
    SHAPE_DEPENDENCY        = "shape_dependency"        # 形状由其他参数推导
    SHAPE_VALUE_DEPENDENCY  = "shape_value_dependency"  # 形状中轴值/元素值依赖
    TYPE_DEPENDENCY         = "type_dependency"         # dtype 依赖其他参数 / 条件
    TYPE_EQUALITY           = "type_equality"           # dtype 必须一致
    VALUE_DEPENDENCY        = "value_dependency"        # 取值依赖（含取值范围）
    FORMAT_EQUALITY         = "format_equality"         # 数据格式必须一致
    PRESENCE_DEPENDENCY     = "presence_dependency"     # 共存规则（None / 非 None）


# ---------- 通用值结构（单参数约束卡片的统一值字段） ----------

class ValueWithSrcText(BaseModel):
    """带 src_text 来源信息的通用值字段。

    所有 ParamAttributes 中的 type、format、is_optional、dtype、dimensions
    等字段均复用此模型。value 为实际数据；src_text 为文档原文摘录；type 为
    allowed_range_value 的可选子类型（'enum' / 'range'），仅在该场景使用。
    """
    value: Union[bool, str, List[str], List[List[int]], List[Any], List[int], int, float] = Field(
        ..., description="字段值"
    )
    src_text: str = Field(default="", description="来源文本")
    type: Optional[str] = Field(
        default=None,
        description="仅 allowed_range_value 使用：'enum'（枚举）或 'range'（区间）",
    )

    model_config = {"extra": "forbid"}


# ---------- 单个参数在某个平台下的约束卡片 ----------

class ParamAttributes(BaseModel):
    """参数信息模型（按平台区分，通用结构）。"""
    description: str = Field(default="", description="参数描述")
    type: Union[ValueWithSrcText, str] = Field(..., description="参数类型（aclTensor / int64_t / bool …）")
    format: Union[ValueWithSrcText, str] = Field(..., description="数据格式（ND / NZ … 或 'N/A'）")
    is_optional: Union[ValueWithSrcText, str] = Field(..., description="是否可选（true / false）")
    is_support_discontinuous: Union[ValueWithSrcText, str] = Field(..., description="是否支持非连续 Tensor")
    is_operator_param: Union[ValueWithSrcText, str] = Field(..., description="是否为算子参数")
    array_length: Union[ValueWithSrcText, str] = Field(
        default="N/A",
        description="数组长度：([2,2] 表示固定长度2) 或 'N/A'（不适用）",
    )
    dtype: Union[ValueWithSrcText, str] = Field(..., description="支持的数据类型列表")
    dimensions: Union[ValueWithSrcText, str] = Field(..., description="维度（rank）约束")
    allowed_range_value: Union[ValueWithSrcText, str] = Field(
        default_factory=lambda: ValueWithSrcText(value=[], src_text=""),
        description="取值范围（含 type 子字段：'enum' / 'range'）",
    )

    model_config = {"extra": "forbid"}


# ---------- 跨参数 / 单参数约束 ----------

class InterParamConstraint(BaseModel):
    """参数约束条目（constraints_in_parameters 数组元素）。"""
    expr_type: str = Field(..., description="约束表达式类型（自由字符串，参考 InterConstraintsRuleType）")
    expr: str = Field(..., description="合法 Python 布尔表达式")
    relation_params: List[str] = Field(..., description="涉及的参数列表")
    src_text: str = Field(default="", description="来源文本")

    model_config = {"extra": "forbid"}


# ---------- 返回码 ----------

class ReturnInfoItem(BaseModel):
    """返回值信息。"""
    return_value: str = Field(..., description="返回值标识（如 ACLNN_ERR_PARAM_NULLPTR）")
    error_code: int = Field(..., description="错误码（如 161001）")
    description: List[str] = Field(default_factory=list, description="错误描述列表")

    model_config = {"extra": "forbid"}


# ---------- 顶层模型 ----------

class OperatorRule(BaseModel):
    """算子规则顶层模型（通用）。"""
    operator_name: str = Field(..., description="算子名称")
    function_explanation: str = Field(..., description="功能说明")
    product_support: List[str] = Field(..., description="支持的产品列表")
    function_signature: str = Field(..., description="函数签名（GetWorkspaceSize）")
    deterministic_computing: Dict[str, Union[ValueWithSrcText, str]] = Field(
        default_factory=dict, description="确定性计算信息（key=平台名）"
    )
    inputs: Dict[str, Dict[str, ParamAttributes]] = Field(
        default_factory=dict,
        description="输入参数信息（key1=参数名, key2=平台名）",
    )
    outputs: Dict[str, Dict[str, ParamAttributes]] = Field(
        default_factory=dict,
        description="输出参数信息（key1=参数名, key2=平台名）",
    )
    constraints_in_parameters: Dict[str, List[InterParamConstraint]] = Field(
        default_factory=dict,
        description="参数约束（key=平台名, value=约束列表）",
    )
    return_info: List[ReturnInfoItem] = Field(
        default_factory=list, description="返回值信息"
    )
    dtype_support_description: Dict[str, List[Dict[str, str]]] = Field(
        default_factory=dict, description="数据类型组合支持表（key=平台名）"
    )
    format_support_description: Dict[str, List[Dict[str, str]]] = Field(
        default_factory=dict, description="数据格式组合支持表（key=平台名）"
    )

    model_config = {"extra": "forbid"}
```

---

## 4. 字段级提取规则

### 4.1 `operator_name`（算子名称）

- 取自文档标题（**第一行**）或首个一级标题。
- 仅保留主名称（如 `aclnnReflectionPad1dBackward`），不要追加 `GetWorkspaceSize` 等后缀。

### 4.2 `function_explanation`（功能说明）

- 取自 `## 功能说明` 章节，**只保留功能语义**，不混入：
  - 计算公式（如 `out = ...`）
  - 参数解释
  - 调用流程
  - 平台差异
- 1–3 句即可，保持**原文用词**（变量名、下划线、占位符原样保留）。
- 若文档无独立功能段，则用首段非公式性概述填充；严禁补写。

### 4.3 `product_support`（产品支持情况）

- 来自文档中的 **"产品支持情况" / "各产品支持情况" / "支持平台"** 表格。
- 仅保留 `√` 标记的平台，**不保留** `×` 行。
- 字符串必须**严格**使用第 5.1 章的标准平台名。
- 数组内顺序与文档表格自上而下一致。

### 4.4 `function_signature`（函数原型字符串）

- 取 `aclnnXxxGetWorkspaceSize` 一段（**不是**执行函数）的完整 C 风格声明，含：
  - 返回类型（`aclnnStatus`）
  - 函数名（带 `GetWorkspaceSize` 后缀）
  - 完整参数列表（含 `workspaceSize` 与 `executor`）
- 单行字符串，不做换行 / 注释 / 类型省略。

### 4.5 `deterministic_computing`（确定性计算）

- **key**：`product_support` 中已确认支持的标准平台名。
- **value**：`ValueWithSrcText` 对象：
  - `value`：`"true"` / `"false"` / `""`（文档无说明时填 `""`）。
  - `src_text`：摘录原文（≤ 80 字），如 `"aclnnXxx默认确定性实现"`。
  - `type`：不使用，填 `null`。

### 4.6 `inputs` 与 `outputs`（输入/输出参数约束卡）

#### 4.6.1 顶层 key

- `inputs` 与 `outputs` 的 key 为 **参数名**（不带 `*`，不带类型前缀）。
- 与函数原型参数**一一对应**，但**排除**以下"流程参数"：
  - `workspaceSize`（`uint64_t` 标量输出）
  - `workspace`（指针）
  - `executor`（`aclOpExecutor**`）
  - `stream`（`aclrtStream`）
- 流程参数不进入 `inputs` / `outputs`。

#### 4.6.2 二级 key（平台名）

- 二级 key 为**平台名**，取值集合：
  - 第 5.1 章列出的标准平台名；
  - **每个非隐式参数都必须为 `product_support` 中列出的每一个平台分别产出条目**。
    即使所有平台下 `ParamAttributes` 字段值完全一致，也必须**逐平台复制**相同的卡片，
    不得用单个平台名"代笔"，也不得遗漏任何平台；典型反例见 §9.3、§8。
  - 当不同平台存在差异时，**按平台拆分**为多个条目（字段值不同）。
- **不要**在单条 `ParamAttributes` 内混合多平台逻辑（用条件表达式兜底属于违规）。
- **隐式维度变量 / 外部常量** 仅在 `constraints_in_parameters` 中需要按平台区分时
  才为该平台保留条目；其它隐式参数同样按平台分别产出，保证每个支持的平台都有
  对应条目。

#### 4.6.3 `ParamAttributes` 字段细则

| 字段 | 必填 | `value` 类型 | 提取规则 |
| ---- | ---- | ------------ | -------- |
| `description` | 是 | `str`（直写，非 ValueWithSrcText） | 表格"描述"列 / 文字说明原文摘录（≤ 200 字） |
| `type.value`   | 是 | `str` | 函数原型中基础类型名，去掉 `*`/`const`/`struct`（如 `aclTensor`、`int64_t`、`bool`） |
| `type.src_text`| 是 | `str` | 若文档未显式说明，填 `""` |
| `format.value` | 是 | `Union[List[str], str]` | 单格式 → 字符串（`"ND"`）；多格式 → 列表（`["ND", "NZ"]`）；标量 → `"N/A"` |
| `format.src_text` | 是 | `str` | 原文摘录 |
| `is_optional.value` | 是 | `bool` | 仅当文档明确出现"可选/Optional/default/可为空/缺省值"时为 `true`；"支持空Tensor" **不等于**可选 |
| `is_optional.src_text` | 是 | `str` | 摘录原文 |
| `is_support_discontinuous.value` | 是 | `Union[bool, str]` | 表格 `√` → `true`；`×` 或无标记 → `false`；非 Tensor 参数 → `"N/A"` |
| `is_support_discontinuous.src_text` | 是 | `str` | 摘录原符号 |
| `is_operator_param.value` | 是 | `bool` | 函数签名真实参数 → `true`；隐式维度变量/量化粒度 → `false` |
| `is_operator_param.src_text` | 是 | `str` | 摘录原文 |
| `array_length` | 是 | `ValueWithSrcText` 或 `str "N/A"` | 数组参数：`value=[min, max]` 或 `[len, len]`；标量 → `"N/A"` 字符串 |
| `array_length.type` | 否 | `str` 或 `null` | 固定长度 → `"range"`；离散枚举 → `"enum"`；不适用 → `null` |
| `array_length.src_text` | 是 | `str` | 摘录原文（如 `"长度为2"`） |
| `dtype.value` | 是 | `List[str]` | 支持的 dtype 字符串（见 §5.2）；标量参数允许填写其自身类型字符串（如 `"bool"`、`"char"`、`"int"`）；不适用 → `[]` |
| `dtype.src_text` | 是 | `str` | 摘录原文 |
| `dimensions.value` | 是 | `List[int]` 或 `[]` | **维度（rank）约束**：如 `[2, 3]` 表示 `2 ≤ rank ≤ 3`；不适用 → `[]` |
| `dimensions.src_text` | 是 | `str` | 摘录原文（如 `"2-3"`、`"2维"`） |

**dtype 为空时的类型回填规则**：
- 优先使用文档明确给出的 dtype；只有未提取到任何 dtype、即 `dtype.value=[]` 时才执行回填；
- `aclIntArray` → `["int"]`，`aclFloatArray` → `["float"]`，`aclBoolArray` → `["bool"]`；
- 其他非 Tensor 参数使用 `type.value` 回填，例如 `type.value="int"` 时输出 `dtype.value=["int"]`；
- `aclTensor` / `aclTensorList` 不得用类型名回填 dtype；其 dtype 必须来自文档，确实未说明时保持 `[]`；
- 文档明确参数"只支持传空指针""必须为空指针"或"仅支持空指针"时保持 `[]`；
- 回填仅补 `dtype.value`，不得伪造 `dtype.src_text`。

**类型前置规则（必须先于下表执行）**：
- 仅当 `type.value` 为 `aclTensor` 或 `aclTensorList` 时，才从文档提取并填写 `dimensions.value`；
- 其他所有类型（包括 `aclIntArray`、`aclFloatArray`、`aclScalar`、`bool`、整数、浮点数和字符串）的 `dimensions.value` 必须为 `[]`，即使其描述中出现"长度""数组""维度""axes"或方括号取值；
- 非 Tensor 容器的元素个数写入 `array_length`，具体数组候选值写入 `allowed_range_value`，二者都不得写入 `dimensions`。

##### `dimensions.value` 解析表（来自 `knowledge/dimensions/SKILL.md`）

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

**关键原则 —— "维数 vs 长度" 区分**：
- "N 维" 描述的是 tensor 的**维度数（rank）**，应输出 `[N, N]`；
- "最大长度 M" / "最大长度为 M" 描述的是某一维的**大小限制**，**不属于 `dimensions`**；
- 该大小限制应由 `constraints_in_parameters` 中的 `self_shape_axis_value` 约束表达。
- **反例**：把 `"1维，最大长度256"` 解析为 `[[1, 256]]`（per-dim 格式）属于错误。

**HTML 列表型 shape（量化参数特有）**：
当 shape 描述里出现 `<ul><li>` + 多种方括号变体（如 `[E, N1]/[N1]`）：
1. 从原文抽取**所有** `[...]` 方括号组；
2. 每个组内按逗号槽数 = rank；
3. 取所有变体的 rank 区间作为 `dimensions.value`；
- 示例：`<ul><li>per-channel...[E, N1]/[N1]</li><li>per-group...[E, G, N1]/[G, N1]</li></ul>`
  → `[E,N1]=2`、`[N1]=1`、`[E,G,N1]=3`、`[G,N1]=2` → 最终 `dimensions.value=[1, 3]`。

**校验规则**：
- rank 格式：`0 ≤ min ≤ max ≤ 10`；
- per-dim 格式：每维 `min ≤ max`（或 `null`），最多 10 维；
- `[]` 永远合法。

##### `allowed_range_value` 文本描述 → 结构化映射（来自 `knowledge/allowed_range/SKILL.md`）

> `knowledge/allowed_range/` 知识库的 LLM 输出格式为**文本**形式（如 `"0-100"`、`"fastgelu,gelu"`），项目 `OperatorRule.allowed_range_value.value` 用**结构化**形式。**本提示词采用项目结构化形式**，但下列映射表必须严格遵守：

| 原文描述 | `value` | `type` | 备注 |
| -------- | ------- | ------ | ---- |
| `"0-100"` / `"[-1,1]"` | `[[0, 100]]` / `[[-1, 1]]` | `range` | 区间 |
| `"0或1"` | `[[0, 1]]` | `range` | 二元 |
| `"0到5"` | `[[0, 5]]` | `range` | 中文区间 |
| `"大于0"` | `[]` | `range` | 单边/开区间不在 `allowed_range_value` 中伪造边界；写 `value_dependency`：`param.range_value > 0` |
| `"小于1024"` | `[]` | `range` | 写 `value_dependency`：`param.range_value < 1024` |
| `"大于等于1"` | `[]` | `range` | 写 `value_dependency`：`param.range_value >= 1` |
| `"取值范围为245~333"` | `[[245, 333]]` | `range` | ~分隔 |
| `"fastgelu/gelu/relu/silu"` | `["fastgelu", "gelu", "relu", "silu"]` | `enum` | `/` 分隔 |
| `"fastgelu/gelu/relu/silu以及geglu/swiglu/reglu"` | `["fastgelu","gelu","relu","silu","geglu","swiglu","reglu"]` | `enum` | **必须拆分**为独立项 |
| `"支持配置空或者[-2,-1]"` | `[null, [-2, -1]]` | `enum` | aclIntArray 的"空"表示未传值，必须序列化为 JSON `null` |
| `"per-channel/per-group/per-tensor/per-token"` | `["per-channel","per-group","per-tensor","per-token"]` | `enum` | 量化粒度 |
| `"true/false"` | `[true, false]` | `enum` | bool 列举 |
| `"支持空或某个固定值"` | `[null, fixed_value]` | `enum` | `type=enum` 时 `null` 是合法离散候选 |
| `"k0=16、n0=16"`（NZ 块尺寸硬约束） | `[[16,16], [16,16]]` | `range` | **5D NZ 张量**：shape[3]/shape[4] 端点均为 16，写两条单点区间；详见 §4.6.5 |
| `"块尺寸为 16"`（NZ 通用，未指明轴位） | `[[16,16]]` | `range` | 单条单点区间；具体轴位须在 §4.6.5 中再识别 |
| 文档无任何取值约束 | `[]` | `range` | **不**在数组中产出该参数 |

`type=range` 与 `type=enum` 对 `null` 的规则不同：

- `type=range`：任何区间端点都不得为 `null`。当前生成器不把 `null` 当作数值无界；
  单边、开区间必须用 `constraints_in_parameters` 中的不等式表达。
- `type=enum`：允许 `null`，表示"空值/未传值"本身是一个明确的离散候选。
- 当原文中的"空"表示未传值、缺省、空指针或 `nullptr` 时，必须输出 JSON `null`，
  禁止照抄为字符串 `"空"`。只有 API 明确接收字面字符串"空"时才能输出 `"空"`。
- "未传容器"和"传入零长度容器"不是同一语义：前者为 `null`；只有原文明示传入
  长度为 0 的数组/列表实例时，才将空容器候选表示为 `[[]]`。空 Tensor 应使用
  shape/dimensions 约束表达，不在 `allowed_range_value` 中写 `"空"`。

##### aclIntArray 特殊取值（`knowledge/allowed_range/examples/acl_int_array.md`）

`aclIntArray` 参数的取值往往是**特定数组值**或**未传值**，`type` 统一设为 `enum`。
若上下文出现"传入空""缺省""空指针"或参数为指针/Optional，空候选使用 JSON
`null`；不得使用字符串 `"空"`：

| 原文 | `value` |
| ---- | ------- |
| `"支持配置空或者[-2,-1]"` | `[null, [-2, -1]]` |
| `"支持配置[-2,-1]或[-1,-2]或空"` | `[[-2, -1], [-1, -2], null]` |

##### bool 类型参数（`no_constraint.md` / v3 加强）

bool 参数（`is_xxx`/`xxxFlag` / `transposeX*` 等）**必须**产出 `allowed_range_value`，
`type` 统一为 `"enum"`，并按下表选择 `value`：

| 原文约束 | `allowed_range_value.value` |
| -------- | --------------------------- |
| "暂不支持配为 True" / "仅支持 False" | `[false]` |
| "暂不支持配为 False" / "仅支持 True" | `[true]` |
| 无明确固定值约束（仅描述为 bool） | `[false, true]` |

禁止：填写 `value=[]` + `type="range"`，否则下游生成器按浮点范围填充，
会产生 `1.0`、`0.0`、`1.23e-40`、`-2147483648.0` 等非法 bool 值。

##### 无约束参数处理（`no_constraint.md` / v3 加强）

下列场景**不**产出 `allowed_range_value.value` 条目（保持 `[]`）：
- 描述只涉及 shape/dtype/format，不涉及值域；
- Tensor / TensorList 参数（`aclTensor` / `aclTensorList`），维度不属于取值范围；
- **bool 参数例外**：见上一子节"bool 类型参数"，必须产出 `type="enum"` 条目。

#### 4.6.4 隐式参数（命名维度变量 / 外部常量）识别

文档中常在 shape 描述里出现 **形如 `(BS, H)`、`(H*rankSize, N)`、`[E, N1]/[N1]` 的命名变量**，它们**不是函数签名参数**，但被下游 `constraints_in_parameters` 中的表达式引用。**必须**抽取到 `inputs` 中（`is_operator_param: false`），按以下规则分类（来自 `knowledge/implicit_params/SKILL.md`）：

##### A. 标准命名维度变量（保留为隐式参数）

| 标识符 | 典型上下文 | 类别 |
| ------ | ---------- | ---- |
| `N` / `C` / `H` / `W` | `(N, C, H, W)` | dimension_variable |
| `BS` / `B` | `(BS, H)` | dimension_variable |
| `batchSize` / `numHeads` | `(batchSize, numHeads)` | dimension_variable |
| `k0` / `n0` / `m0` | `(k0, n0)` | dimension_variable（除非有等式赋值） |
| `dim` / `rank` / `seqLen` | `(dim, rank)` | dimension_variable |
| `E` / `G` / `N1` | `[E, N1]`、`[E, G, N1]` | dimension_variable |

##### B. 复合表达式中的命名变量

| 表达式 | 抽取 |
| ------ | ---- |
| `H*rankSize` | `H` 为 dimension_variable；`rankSize` 为 external_constant |
| `BS/rankSize` | `BS` 为 dimension_variable；`rankSize` 为 external_constant |
| `A*B`（两者均独立出现） | `A`、`B` 均为 dimension_variable |

##### C. 必须剔除的"概念词 / 操作名 / 类型词"

| 类别 | 剔除清单 |
| ---- | -------- |
| **维度概念词**（"X维度"中的X表示含义，不是变量名） | `Reduce`、`GEMV`、`Attention`、`Conv` |
| **激活函数名** | `Softmax`、`ReLU`、`Sigmoid`、`GELU`、`SwiGLU` |
| **归一化操作名** | `LayerNorm`、`BatchNorm` |
| **卷积操作名** | `Conv`、`Conv2D`、`Conv3D` |
| **矩阵乘操作名** | `Matmul`、`BMM`、`MM` |
| **张量操作名** | `Transpose`、`Reshape`、`Permute` |
| **泛型描述词** | `shape`、`dtype`、`format`、`type`、`input`、`output`、`tensor`、`optional`、`true`、`false`、`none`、`null` |
| **基本类型词** | `float`、`double`、`int`、`char`、`void` |

##### D. 常量识别（显式赋值 → 转为常量）

| 原文模式 | 标识符 | 分类 | `constant_value` |
| -------- | ------ | ---- | ---------------- |
| `"其中k0 = 16"` | `k0` | constant | `16` |
| `"n0为16"` | `n0` | constant | `16` |
| `"k0等于16"` / `"k0 is 16"` | `k0` | constant | `16` |
| `"其中 G = 128"` | `G` | constant | `128` |

**NZ 块尺寸常量（v2 新增）**：当文档同时存在 `"k0 = 16"`、`"n0 = 16"` 两条赋值（如
`"k0 = 16， n0为16"` / `"n0 = 16， k0为16"`），`k0`、`n0` 统一按 constant 处理，
`constant_value=16`；**不允许**为 `k0`、`n0` 在 `inputs` 中再产出 `dimension_variable`
卡片，亦不允许将其作为隐式变量在 `constraints_in_parameters` 中被 `k0.range_value`
形式引用。

##### E. 外部常量识别（仅出现在复合表达式中 → external_constant）

| 标识符 | 出现位置 | 类别 | 说明 |
| ------ | -------- | ---- | ---- |
| `rankSize` | `H*rankSize` | external_constant | 平台相关（NPU 卡数） |
| `worldSize` | `BS/worldSize` | external_constant | 分布式训练相关 |
| `padSize` | `N+padSize` | external_constant | 视上下文决定 |

外部常量必须按平台分别给出 `allowed_range_value`（枚举式），如 `rankSize` 在 Atlas A2 上为 `[2,4,8]`，在 Atlas 350 上为 `[2,4,8,16]`。

##### F. 漏抽取补充

正则可能漏掉仅在**约束描述文字**中出现、但未在任何 shape 元组里出现的变量（如 "rankSize 的取值依赖于 NPU 卡数"）。**应当**将其补加到 `inputs`，类别为 `external_constant`。

#### 4.6.5 NZ 格式块尺寸硬约束（v2 新增，通用规则）

> 本节是 v2 的关键扩展，覆盖**所有**昇腾 NZ / FRACTAL_NZ / FRACTAL_NZ_C0_16 张量
> 算子（如 `aclnnBatchMatMulWeightNz`、`aclnnGroupedMatmulV5` 等），不针对单一算子。

##### A. 适用判定

满足下列**全部**条件时，**必须**执行本节规则：
1. 参数 `format.value`（按平台）为 `"NZ"` / `"FRACTAL_NZ"` / `"FRACTAL_NZ_C0_16"` 之一；
2. 参数 `dimensions.value`（按平台）等于 `[5, 5]`（5 维张量）；或文档明确给出形如
   `(b, n1, k1, k0, n0)` / `(b, k1, n1, n0, k0)` / `(..., k0, n0)` 的 5 维 NZ 维度元组。

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
   关系参数列表只写 `["mat2"]`；`k0` / `n0` 已在 §4.6.4 D 标记为 constant，无需
   在约束条目中再列。

##### D. 必须产出的 `allowed_range_value` 条目

按平台，对 `mat2`（或任何 5D NZ 张量）的 `allowed_range_value` 字段：

1. `type=range`、`value` 至少包含两条单点区间 `[[16, 16], [16, 16]]`，
   分别对应 `shape[3]` 和 `shape[4]` 的块尺寸硬约束；
2. 若文档同时写明 `k0=16`、`n0=16`，亦可写 `[[16, 16]]`（统一单点），
   **但**`shape[3]` 与 `shape[4]` 的硬等式约束仍须在 `constraints_in_parameters`
   中独立落库（见 C），不能因为 `allowed_range_value` 已含区间就省略；
3. 当 `shape[3]` / `shape[4]` 同时还有其他取值范围（如某些算子的 pad 维度支持
   `32`），按文档原文端点区间落库；若文档明确写明块尺寸为 16，则 `[[16, 16]]`
   必须作为子区间之一出现。

**反例（禁止）**：
- `allowed_range_value.value=[]`、`type=range`，但文档明示 `k0=16, n0=16` →
  漏抓，违规则 C.1 + D.1。
- `allowed_range_value.value=[[16, 16]]` 但 `constraints_in_parameters` 无
  `mat2.shape[3]==16` / `mat2.shape[4]==16` → 漏抓，违规则 C.1。
- `constraints_in_parameters` 仅写 `mat2.shape[3] == 16`（未写 `shape[4]`）→
  不完整，违规则 C.1。

##### E. 与隐式维度变量的协作

- `k0` / `n0` 一律按 §4.6.4 D 标为 `constant`，`constant_value=16`；**不**在
  `inputs` 中产出隐式维度变量卡片（区别于 `(N, C, H, W)` 中的 `N`、`C` 等）。
- `mat2.allowed_range_value` 中的 `src_text` 摘录 `k0 = 16` / `n0为16` 等
  原文短语，确保可溯源。
- `mat2.allowed_range_value` 的 `type=range` 端点严禁为 `null`（与 §4.6.3
  range 通用规则一致）；若文档只写"块尺寸为 16"未指明具体轴位，按 §4.6.5 D.2
  使用 `[[16, 16]]` 并在 `src_text` 中说明"统一块尺寸"。

### 4.7 `constraints_in_parameters`（跨参数 / 单参数约束）

#### 4.7.1 顶层 key

- 平台名；不存在平台差异时**各平台使用相同的约束列表**（不要删减为单项 `"common"`）。

#### 4.7.2 `InterParamConstraint` 字段

| 字段 | 必填 | 说明 |
| ---- | ---- | ---- |
| `expr_type` | 是 | **自由字符串**。优先从 §7 字典中选用；若字典无法覆盖，允许使用实际语义值（如 `cross_param_constraint`、`parameter_representation`、`self_value_enum`、`self_string_length`、`self_value_dependency`） |
| `expr` | 是 | 规范化后合法的 Python 布尔表达式（第 6 章）；允许裸 `null`，执行前转换为 `None`；无法写出时填 `""` |
| `relation_params` | 是 | 表达式中**所有**被引用的参数名（按出现顺序，去重） |
| `src_text` | 是 | 原文摘录，**可为空字符串** |

#### 4.7.3 提取规则

1. **跨参数约束优先**：涉及 ≥2 个参数的约束**必须**进入 `constraints_in_parameters`，不要只在 `inputs`/`outputs` 中备注。
2. **单参数约束复写**：若约束在 `allowed_range_value` 中已有表达，仍可在 `constraints_in_parameters` 中**附加一条带 `expr` 的形式化版本**（不视为冗余，而是机器可判定性的增强）。
3. **单参数 shape 约束**：若已在 `dimensions` 中表达（如 `[2,3]`），可省略重复。
4. **存在性约束**必须用完整布尔表达式（如 `(scale is None) == (zeroPoint is None)`），不允许退化为"可选/必选"自然语言。
5. **禁止**把"算子功能说明"或"参数描述"塞入 `constraints_in_parameters`。
6. **保护值语义**：参数名为 `epsilon`/`eps`，且功能描述明确称其为"除0保护值"、
   "分母保护值"或数值稳定项时，应推导严格正值约束。若另有上界说明，合并成链式
   不等式，例如 `0 < epsilon.range_value <= 1e-4`。`src_text` 必须同时摘录保护值
   描述和上界说明，使隐式下界可追溯。
7. **NZ 块尺寸必须显式落库（v2 新增）**：见 §4.6.5 C。NZ 张量的 `shape[3]` /
   `shape[4]` 块尺寸硬约束**必须**用 `shape_equality`（或 `shape_value_dependency`）
   形式化写出，不允许只放在 `allowed_range_value` 中。

### 4.8 `return_info`（错误返回码）

- 来自 `## 返回码` / `## 错误码` 章节。
- 字段：
  - `return_value`：枚举字符串（如 `ACLNN_ERR_PARAM_NULLPTR`）；
  - `error_code`：整数（如 `161001`）；
  - `description`：触发条件列表（`List[str]`，单条也用列表）。
- 文档未给错误码时填 `[]`。

### 4.9 `dtype_support_description`（dtype 组合支持表）

- 仅当文档存在**显式 dtype 组合表格**（如"各产品下 x1/x2/out 的 dtype 组合"）时填写；
- key 为平台名，value 为该平台下的 combo 对象列表（每个 combo 为 `{param_name: dtype_str}` 字典）；
- 无组合表时填 `{}`。

### 4.10 `format_support_description`（format 组合支持表）

- 结构与 `dtype_support_description` 对称：key 为平台名，value 为格式组合列表；
- 仅当文档存在**显式 format 组合表格**时填写；
- 无此表时填 `{}`。

---

## 5. 平台与 dtype 命名规范

### 5.1 标准平台名（受控字典）

提取 `product_support` / `deterministic_computing` / `constraints_in_parameters` / `dtype_support_description` / `format_support_description` 的 key 时，**必须**使用以下字符串之一：

| 平台 | 字符串 |
| ---- | ------ |
| Atlas A2 训练 + 推理 | `Atlas A2 训练系列产品/Atlas A2 推理系列产品` |
| Atlas A3 训练 + 推理 | `Atlas A3 训练系列产品/Atlas A3 推理系列产品` |
| Atlas 训练系列（旧） | `Atlas 训练系列产品` |
| Atlas 推理系列（旧） | `Atlas 推理系列产品` |
| Atlas 推理系列加速卡 | `Atlas 推理系列加速卡产品` |
| Atlas 350 加速卡 | `Atlas 350 加速卡` |
| Atlas 200I/500 A2 推理 | `Atlas 200I/500 A2 推理产品` |
| Atlas 300I 推理 | `Atlas 300I 推理产品` |
| Atlas 300I Duo 推理 | `Atlas 300I Duo 推理产品` |
| Atlas 300V 视频解析 | `Atlas 300V 视频解析产品` |
| Atlas 500 A2 智能小站 | `Atlas 500 A2 智能小站` |
| Atlas 800 推理服务器 A2 | `Atlas 800 推理服务器 A2` |
| Atlas 800 训练服务器 | `Atlas 800 训练服务器` |
| Atlas 800I A2 推理服务器 | `Atlas 800I A2 推理服务器` |

### 5.2 标准 dtype 字符串（受控字典）

提取 `dtype.value` / `dtype_support_description` 中的 dtype 时，**必须**使用以下字符串之一：

##### Tensor 数据类型
```
FLOAT32, FLOAT16, BFLOAT16, BF16, DOUBLE, INT8, UINT8, INT16, UINT16,
INT32, UINT32, INT64, UINT64, BOOL, COMPLEX64, COMPLEX128,
FLOAT8_E4M3FN, FLOAT8_E5M2, FLOAT4_E2M1, HFLOAT4, HFLOAT8
```

##### 标量参数"类型"（仅用于 `dtype.value`，不用于 `dtype_support_description` 的 combo）
```
bool, char, int, int64_t, int8_t, double, float, uint64_t, size_t
```

- 文档中出现 `BF16` / `bfloat16` / `bf16` 时 → 统一为 `BF16`；
- 文档中出现 `float` / `Float` / `FLOAT` 时 → 统一为 `FLOAT32`（除非上下文明确为 `float16`）；
- 标量参数（`int64_t`、`bool`、`char` 等）的 `dtype.value` 填写 `["bool"]`、`["char"]`、`["int"]`、`["int64_t"]` 等，表示"该参数自身类型"。

### 5.3 标准数据格式（受控字典）

```
ND, NC, NCL, NCHW, NCDHW, NHWC, NZ, FRACTAL_NZ, FRACTAL_Z, FRACTAL_Z_3D,
NDC1HWC0, FRACTAL_NZ_C0_16, NDHWC, NCHW_VECT_C0_16, NC1HWC0
```

- 多格式参数用 `List[str]`（如 `["FRACTAL_Z_3D", "ND"]`），单格式用 `str`；
- 标量 / 非 Tensor 参数用 `"N/A"`（注意是字符串，不是 `null`）。
- **`NZ` / `FRACTAL_NZ` / `FRACTAL_NZ_C0_16` 张量必须配套应用 §4.6.5**（v2 新增）。

---

## 6. 表达式编写规范

`expr` 字段在将裸 `null` 规范化为 Python `None` 后，必须是**合法 Python 布尔表达式**
（`eval()` 可执行，返回 `bool`）。

### 6.1 语法细则（综合 `knowledge/relation_skills/SKILL.md`）

1. **变量引用**：使用**裸参数名**或 `参数名.shape[i]` / `参数名.dtype` / `参数名.format` / `参数名.range_value`：
   - ✅ `len(x.shape) == 3`
   - ✅ `x.shape[0] * x.shape[1] <= 2147483647`
   - ✅ `rankSize.range_value in [2, 4, 8]`
   - ✅ `x1.shape[0] == BS.range_value`
   - ✅ `x1.format == x2.format`
   - ❌ `tensor_x.dim == 3`（**禁止**别名）
2. **取值范围**：数值区间必须使用比较运算；离散枚举使用 `in [v1, v2]`：
   - ✅ `0 <= actType.range_value <= 5`（数值闭区间）
   - ✅ `0 < epsilon.range_value <= 1e-4`（数值开/闭区间）
   - ✅ `activation.range_value in ["relu", "gelu"]`（对枚举查）
   - ✅ `alltoAllAxesOptional.range_value == [-2, -1]`（对固定值等号）
   - ✅ `transposeX1.range_value == False`（bool 等号）
   - ❌ `actType.range_value in [[0, 5]]`（嵌套列表是数据结构，不是区间谓词）
   - ❌ `epsilon.range_value in [[null, 0.0001]]`（不得用 `null` 充当数值边界）
3. **复合逻辑 —— 蕴含两种等价形式**：
   - **形式 A（if/else）**：`(B) if (A) else True` —— 条件不满足时返回 True（约束不适用）
     - ✅ `(bias.dtype == "FLOAT16") if (x.dtype == "FLOAT16") else True`
   - **形式 B（unless 结构）**：`not(A) or B` —— 条件不满足时约束不生效
     - ✅ `not(quantization_type.range_value == "per-channel") or (bias.shape == [E, N1])`
     - ✅ `not(A and B) or C` 用于"两个条件同时成立才约束"的场景
   - 等价关系（"A 当且仅当 B"）：`(A) == (B)`，如 `(scales2 is None) == (zeroPoints2 is None)`
4. **生成器**：必须用 `all()` / `any()` 包裹：
   - ✅ `all(v >= 1 for v in padding.range_value)`
   - ✅ `all(d > 0 for d in x.shape)`（不允许空 Tensor）
   - ❌ `[v >= 1 for v in padding.range_value]`（返回 list，不返回 bool）
5. **"维数 vs 长度"**：表达式中的 `len(x.shape)` 表示 rank，"shape size" 永远指 rank，**不是**各维大小乘积。
6. **负索引优先**：当约束引用了以字母命名的维度（如 `H`、`W`）且该维度在 shape 描述中**始终处于固定语义位置**（如"最后一维），必须使用 `shape[-1]` 而非固定正索引 `shape[1]` 或 `shape[3]`。
7. **命名维度变量 / 外部常量引用**：使用 `变量名.range_value` 形式（如 `BS.range_value`、`rankSize.range_value`），不写 `BS.shape[0]`。
8. **已知常量直接使用数值**：若文档给出 `k0 = 16` 这种赋值，表达式里直接写 `16`，不需要 `k0.range_value`；NZ 块尺寸硬约束中 `mat2.shape[3] == 16` / `mat2.shape[4] == 16` 即此规则的体现（v2 新增）。
9. **禁止关键字**：`lambda`、非蕴含三元运算符滥用、`implies`、伪代码、平台值作为判断条件。
10. **`null` / `None`**：表达式允许使用 JSON 风格裸值 `null`，执行前会规范化为
    Python `None`；也可直接写 `None`。它只用于空值、可选值和存在性判断，例如
    `bias is null` 或 `bias is not None`，
    不得作为数值区间端点参与 `<`、`<=`、`>`、`>=`。无法表达时统一使用空字符串
    `""`，不要用整个 JSON 值 `null` 代替 `expr` 字符串。
11. **参数名冲突**：当参数名为 `max`/`min`/`sum` 等内置函数名时，表达式中**不要再调用**同名内置函数；`relation_params` 仍写原名。

### 6.2 表达式与 src_text 的对应

- `expr` 表达什么，`src_text` 就摘录什么；
- 表达式无法直接对应原句（如文档只给 "shape 与 x 一致"）时，`expr` 写 `out.shape == x.shape`，`src_text` 摘录 `"out 的 shape 与 x 保持一致"`。

### 6.3 表达式模式库（按关系特征匹配）

> 来自 `knowledge/relation_skills/` 4 个模式文件。按以下流程匹配：先识别场景特征 → 套用对应模板。

#### 模式 1：枚举条件 + 条件 Shape（`enum_conditional_shape.md`）

**适用场景**：同时含 `per-channel`/`per-tensor` 等枚举值、`Optional` 是否存在判断、`[E, N1]` 条件 shape。

```text
# 单条件
not({enum_param}.range_value == "{value}")
  or ({target}.shape == [{vars}.range_value, ...])

# 双条件（枚举 + 存在性）
not({enum_param}.range_value == "{value}" and {presence_param} is not None)
  or ({target}.shape == [{vars}.range_value, ...])
```

#### 模式 2：多 Shape 候选（`multi_shape_choice.md`）

**适用场景**：shape 有多个候选，由枚举参数或条件决定。

```text
# 二选一（条件驱动）
({target}.shape == [shape_A]) if (condition) else ({target}.shape == [shape_B])

# 多选一（枚举驱动）
({target}.shape == [shape_A]) if ({enum}.range_value == "mode_A")
else ({target}.shape == [shape_B]) if ({enum}.range_value == "mode_B")
else True
```

#### 模式 3：存在性依赖（`presence_dependency.md`）

```text
# 互斥共存：(A is None) == (B is None)
# 条件存在：(B is not None) if (A is not None) else True
# 条件不存在：(B is None) if (A is not None) else True
```

#### 模式 4：单参数自身约束（`self_constraint.md`）

```text
# 取值范围：{min} < {param}.range_value < {max}  /  {param}.range_value > {min}
# 允许枚举：{param}.range_value in [{v1}, {v2}, ...]
# 维度数量：{min} <= len({param}.shape) <= {max}
# 各维大小：all(d <= {max} for d in {param}.shape)
# 空 Tensor 限制：all(d > 0 for d in {param}.shape)
```

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

---

## 7. `expr_type` 取值字典

> `InterParamConstraint.expr_type` 类型为**自由 `str`**（不受 Pydantic 枚举约束）。
> 下表列出**已知的常用取值**作为**参考指引**；若语义无法匹配，允许使用文档实际语义值。

### 7.1 参数间约束（2+ 参数，来自 `InterConstraintsRuleType` 枚举）

| `expr_type` | 适用场景 | 典型 `expr` |
| --- | --- | --- |
| `shape_broadcast` | 形状需满足广播关系 | `all(a.shape[i] == b.shape[i] or a.shape[i]==1 or b.shape[i]==1 for i in range(N))` |
| `shape_choice` | 形状在多个候选中选其一 | `bias.shape == gamma.shape or bias.shape == x.shape` |
| `shape_equality` | 形状完全相等 | `out.shape == x.shape` |
| `shape_dependency` | 输出 shape 由输入 + 辅助参数推导 | `out.shape[0] == pad + x.shape[0]` |
| `shape_value_dependency` | shape 中具体轴值/元素值依赖 | `x1.shape[0] == x2.shape[1] and x2.shape[1] == BS.range_value` |
| `type_equality` | dtype 必须一致 | `x1.dtype == x2.dtype` |
| `type_dependency` | dtype 依赖其他参数/条件 | `(bias.dtype == "FLOAT16") if (x.dtype == "FLOAT16") else (bias.dtype == "FLOAT32")` |
| `value_dependency` | 取值依赖/取值范围 | `BS.range_value % rankSize.range_value == 0` |
| `format_equality` | 数据格式必须一致 | `x1.format == x2.format` |
| `presence_dependency` | 共存规则（None/非None） | `(scale is None) == (zeroPoint is None)` |

### 7.2 单参数约束（扩展值，不在 `InterConstraintsRuleType` 枚举中但实际广泛使用）

| `expr_type` | 适用场景 | 典型 `expr` |
| --- | --- | --- |
| `cross_param_constraint` | 通用跨参数约束（语义较泛） | 按具体上下文 |
| `parameter_representation` | 隐式维度变量/外部常量与张量 shape 的绑定 | `x1.shape[0] == BS.range_value` 或 `rankSize.range_value in [2,4,8]` |
| `self_value_range` | 单参数取值范围（区间） | `0 <= actType.range_value <= 5` |
| `self_value_enum` | 单参数取值枚举 | `activation.range_value in ["relu", "gelu", "silu"]` |
| `self_value_dependency` | 单参数取值 ≈ 固定布尔/唯一合法值 | `transposeX1.range_value == False` |
| `self_string_length` | 字符串参数长度约束 | `0 < len(group.range_value) < 128` |
| `self_shape_dim_range` | 单参数维度（rank）范围 | `2 <= len(x.shape) <= 3` |
| `self_shape_axis_value` | 单参数某轴值约束 | `x.shape[0] >= 1` |

---

## 8. 边缘场景处理

| 场景 | 处理方式 |
| ---- | -------- |
| 文档仅给"产品支持"无 dtype 组合表 | `dtype_support_description={}` |
| 文档仅给"产品支持"无 format 组合表 | `format_support_description={}` |
| 多平台 dtype 列表完全一致 | 各平台各自复制相同列表；不用"common"合并 |
| 参数是 `aclIntArray *xxx` | `type.value="aclIntArray"`，`array_length` 必填实值 |
| 文档出现 `Optional` 后缀但未说明是否可空 | `is_optional.value=false`（保守），`src_text` 摘录原文待人工复核 |
| 文档写"shape 为 [B,H] 或 [B,1,H]" | 拆为 `shape_choice` / `shape_dependency` 约束；不要并成模糊规则 |
| 文档写"x 和 y 数据类型必须一致" | `expr_type="type_equality"`，`expr="x.dtype == y.dtype"`，`relation_params=["x","y"]` |
| 文档写"仅 Atlas A3 支持 BF16" | 在对应平台的 `dtype.value` 中体现差异，`src_text` 摘录原文 |
| 文档给出"确定性计算：默认确定性" | `deterministic_computing["平台"].value = "true"`，`src_text` 摘录该句 |
| 文档给出"确定性计算：默认非确定性" | `deterministic_computing["平台"].value = "false"`，`src_text` 摘录该句 |
| 文档**完全没有** `返回码` 章节 | `return_info=[]` |
| `allowed_range_value` 只有单边界或开区间 | `allowed_range_value.value=[]`；在 `constraints_in_parameters` 中用 `value_dependency` 不等式表达，禁止为 `type=range` 写 `null` 端点 |
| **文档写 bool 参数（无固定值约束）** | `allowed_range_value.type="enum"`、`value=[false, true]`；强行 bool 枚举，不允许填 `[]` 配 `type="range"`（否则下游生成器按浮点填充，会产生 1.0/1.23e-40 等非法值） |
| 表达式无法用 Python 表达（自然语言公式） | `expr=""`，`src_text` 摘录原文，待人工校对 |
| 文档出现矛盾（A段dtype=X，B段dtype=Y） | 优先**保守**取值（取并集），`src_text` 摘录矛盾原文，等待人工确认 |
| 文档写"1维，最大长度256" | `dimensions.value=[1, 1]`，**长度256 不得放入 `dimensions`**；须在 `constraints_in_parameters` 中加 `self_shape_axis_value` 约束 |
| 文档写"shape 与 weight1 一致" / "与输入相同" | `dimensions.value=[]`；**跨参数引用留给 `constraints_in_parameters`** 的 `shape_equality` 约束 |
| 文档写"(BS, H) 或 (BS/rankSize, rankSize*H)" | 拆为 `shape_choice` 约束 + `parameter_representation` 约束；`dimensions.value` 按区间取值 |
| 文档写"其中k0=16" | `k0` 归类为 `constant`，`constant_value=16`；不放入 `inputs`（直接写入 `expr` 表达式） |
| 文档写"H*rankSize"中的 `rankSize` 仅在复合表达式出现 | 归类为 `external_constant`，按平台分别给 `allowed_range_value` |
| 文档写"Reduce 维度需要…" | `Reduce` 是 reduce 操作概念词，**不**抽取为隐式维度变量 |
| 文档写"Softmax、LayerNorm" | **不**抽取为隐式维度变量（是操作名 / 算法名） |
| 文档写"支持配置空或者[-2,-1]"（aclIntArray） | `allowed_range_value.value=[null, [-2, -1]]`，`type=enum`；"空"表示未传值，不得写成字符串 |
| 文档写"仅 Atlas A2 支持 BF16" | 在对应平台的 `dtype.value` 中体现差异，`src_text` 摘录原文 |
| 文档写"shape 为 [E, N1] / [N1]（per-channel / per-tensor）" | `dimensions.value=[1, 3]`（HTML 多变体取区间），shape 选择逻辑走 `shape_choice` / `shape_value_dependency` 约束 |
| 文档写"x 和 y 必须共存，要么都存在要么都不存在" | `expr_type=presence_dependency`，`expr=(x is None) == (y is None)` |
| 文档写"actType 取值为 0 到 5" | `allowed_range_value.value=[[0, 5]]`，`type=range`；可附加 `self_value_range`：`0 <= actType.range_value <= 5` 增强机器可判定性 |
| 文档把 epsilon/eps 描述为"除0保护值"，并建议"≤1e-4" | `allowed_range_value.value=[]`；增加 `value_dependency`：`0 < epsilon.range_value <= 1e-4`，`src_text` 同时摘录两句 |
| **文档写"NZ格式各个维度表示：（b, n1，k1，k0，n0），其中k0 = 16， n0为16"（v2 新增）** | 按 §4.6.5 全流程处理：①`mat2.dimensions.value=[5,5]`；②`mat2.allowed_range_value.value=[[16,16],[16,16]]`，`type=range`；③`constraints_in_parameters` 追加 `mat2.shape[3]==16` 与 `mat2.shape[4]==16` 两条 `shape_equality`，`src_text` 摘录完整原文 |
| **文档写"NZ格式各个维度表示：（b, k1，n1，n0，k0），其中n0 = 16， k0为16"（v2 新增，转置 NZ）** | 同上，但**作为独立两条约束**落库（与上一种布局不合并），`src_text` 摘录对应的转置原文；`mat2.allowed_range_value` 同样含 `[[16,16],[16,16]]` |
| **文档同时写明非转置与转置 NZ 两种布局（v2 新增）** | 两套布局的 `mat2.shape[3]==16` / `mat2.shape[4]==16` 必须分别落库（共 4 条 `shape_equality`）；`allowed_range_value` 的 `value` 仍为 `[[16,16],[16,16]]`（数值上等价，但约束条目按布局拆分） |
| **`product_support` 含 ≥2 个平台，但 `inputs`/`outputs` 中某非隐式参数只产出 1 个平台条目** | 漏抽：必须**逐平台复制相同 `ParamAttributes`**（即便各平台字段值完全一致）。常因模型误读 §4.6.2 旧措辞（"约束完全一致可用单个平台名"）所致——该规则禁止用于"代笔"其他平台 |

---

## 9. 自检清单（提取完成后必跑）

> 模型在生成 JSON 之后、提交给用户之前，**内部自检** 15 项。任何一项不通过均需重做。

1. **JSON 校验**：用 `OperatorRule.model_validate_json(json_str)` 解析，**不抛异常**。
2. **字段完整**：`OperatorRule` 的**全部 11 个**必填字段均存在且非 `None`；数组/对象至少是空容器。
3. **平台字典一致 & 平台覆盖完整**：`product_support` 中的每个平台名，在
   `deterministic_computing`、`constraints_in_parameters` 的 key 中**至少出现一次**。
   **`inputs` / `outputs` 中的每个 `is_operator_param.value=true` 的非隐式参数，必须为
   `product_support` 中的每一个平台都产出条目**——即使各平台 `ParamAttributes` 内容完全
   一致，也必须逐平台复制；不得用单个平台名"代笔"。常见错误模式：从 `Atlas 350 加速卡`
   文档表格读取约束后，只输出 `Atlas 350 加速卡` 条目，遗漏 `Atlas A3 / A2` 条目。
4. **dtype/format 字典一致**：所有 `dtype.value` 元素来自 §5.2（含标量类型）；非 Tensor 参数若非"仅支持空指针"，`dtype.value` 不得为空，缺失时按 type 回填；所有 `format.value` 元素来自 §5.3 或为 `"N/A"`。
5. **表达式合法**：每条 `expr`（非空）先把裸 `null` token 规范化为 `None`，再用
   Python AST 解析；不得有 `SyntaxError`。`null`/`None` 不得作为数值大小比较边界。
6. **关系参数一致**：`expr` 中**所有出现的标识符**都在 `relation_params` 中；`relation_params` 中所有参数名都在 `inputs`/`outputs` 有对应卡片（隐式维度变量/外部常量允许例外，但须在 `inputs` 中登记）。
7. **来源可溯**：`function_explanation`/`dtype`/`format`/`dimensions`/`allowed_range_value` 的 `src_text` 至少 30% 非空（无来源的纯模型外推视为无效）。
8. **隐式参数完整性**：所有在 `constraints_in_parameters` 的 `expr` 中出现的**非函数签名标识符**（如 `BS`、`H`、`N`、`rankSize`），必须**全部**出现在 `inputs` 中，且 `is_operator_param.value=false`。
9. **dimensions 合理性与类型门禁**：仅 `aclTensor` / `aclTensorList` 允许 `dimensions.value` 非空；其他类型必须为 `[]`。非空时形态必须合规（rank 格式 `[min, max]` 且 `0 ≤ min ≤ max ≤ 10`，或 per-dim 格式 `[[min,max], ...]`）。
10. **枚举拆分完整**：若 `allowed_range_value.type=enum` 且 value 是 `List[str]`，则字符串中**不得**再包含 `/`、`、`、`以及`、`and`、`/` 等分隔符（必须已被拆成独立元素）。
11. **range 的 null 禁令**：若 `allowed_range_value.type=range`，所有区间端点必须为
    实际数值且不得为 `null`；`type=enum` 的离散候选允许包含 `null`。
12. **数值范围表达式**：禁止生成 `.range_value in [[min, max]]`；必须改写为
    `min <= param.range_value <= max` 或对应的单边/开区间不等式。
13. **空值枚举序列化**：若 `allowed_range_value.type=enum` 且原文的"空"表示未传值、
    缺省、空指针或 `nullptr`，候选必须是 JSON `null`，不得是字符串 `"空"`；只有
    原文明示零长度容器时才使用空容器候选 `[[]]`。
14. **bool 参数 allowed_range_value 强枚举**：对所有 `type.value` 为 `"bool"` 的参数，
    `allowed_range_value.type` 必须为 `"enum"`，`value` 必须是 `[false]` / `[true]` /
    `[false, true]` 三者之一；禁止留 `value=[]` 或 `type="range"`（否则生成器按浮点
    范围填充会产生非法 bool 取值，触发 `create_dataset` 报告 `attr bool error`）。
15. **NZ 块尺寸硬约束（v2 新增）**：若存在 5D NZ 张量（`format ∈ {"NZ","FRACTAL_NZ","FRACTAL_NZ_C0_16"}` 且 `dimensions.value=[5,5]`），
    必须满足**全部**下列子项：
    a. `mat2.allowed_range_value.value` 包含 `[[16,16],[16,16]]` 或文档明示的其他端点（`type=range`）；
    b. `constraints_in_parameters[每个支持平台]` 含 `mat2.shape[3] == 16` 与 `mat2.shape[4] == 16` 两条 `shape_equality`（或 `shape_value_dependency`）；
    c. 文档同时描述非转置与转置 NZ 两种布局时，两套 `shape[3]/shape[4]==16` 须**分别落库**为不同条目（共 4 条），`src_text` 摘录对应原文；
    d. `src_text` 非空，且包含 `k0` / `n0` / `16` 等关键词。

---

## 10. 调用模板

下面给出一份**可直接复制**的 prompt 调用片段：

```text
# System
你是一名昇腾 CANN 算子约束抽取专家。
请严格遵循《算子约束提取通用提示词 v2》的所有规则，并参考知识库：
- 解析 shape/dimensions 时参考 §4.6.3 dimensions 解析表
- 识别隐式维度变量时参考 §4.6.4（概念词/操作名/类型词需剔除）
- 处理 NZ / FRACTAL_NZ 张量时参考 §4.6.5（块尺寸硬约束、转置/非转置布局区分）
- 写 expr 表达式时参考 §6.3 模式库（按关系特征匹配模板；NZ 块尺寸使用模式 5）
- 写 allowed_range_value 时参考 §4.6.3 allowed_range 文本→结构化映射

输出必须是**纯 JSON 字符串**，无任何前后缀。

# User
请从下列算子说明文档中提取约束。

## 算子名称
{operator_name}

## 算子文档 URL
{operator_url}

## 算子说明文档（已转换为 Markdown）
```markdown
{operator_doc_markdown}
```

## 你的任务
1. 完整阅读算子说明文档；
2. 按《算子约束提取通用提示词 v2》第 3 章 schema 输出 JSON；
3. 内部执行第 9 章 13 项自检（含 §9.13 NZ 块尺寸硬约束自检）；
4. **仅返回 JSON 字符串**，不要包含任何解释、代码块标记或额外文字。
```

---

## 附录 A：与 10 个典型算子的对齐示例

> 下面给出 10 个 Transformer / NN 类算子的提取样例，**用于**说明 schema 在真实场景下的形态，**不构成**对其余算子的强制要求。

| 算子 | 类型 | 关键提取点 |
| ---- | ---- | ---------- |
| `aclnnReflectionPad1dBackward` | NN / 反向 | `padding` 长度固定 2；`padding` 数值 < `self` 最后一维 |
| `aclnnBatchMatMulWeightNz` | NN / MatMul | `mat2` 强制 NZ 格式；**§4.6.5 双布局**：非转置 `(b, n1, k1, k0=16, n0=16)` + 转置 `(b, k1, n1, n0=16, k0=16)` 各落两条 `shape[3]/shape[4]==16`；`cubeMathType` 可选 int8 |
| `aclnnGroupedMatmulV5` | NN / 分组 MatMul | `actType ∈ [0,5]`；大量 `Optional` 参数与 `aclTensorList` |
| `aclnnSwinAttentionScoreQuant` | Transformer | int8 量化；`biasDequant*Optional` 取值为 0–255 整型 |
| `aclnnSwinTransformerLnQkvQuant` | Transformer | LN + QKV 拆分；`headNum`/`seqLength`/`epsilon` 等标量属性 |
| `aclnnAlltoAllMatmul` | 通信 + MatMul | `alltoAllAxesOptional` 取值 JSON `null`（原文"空"）或 `[-2,-1]`；隐式变量 `BS`/`H`/`N` + 外部常量 `rankSize` |
| `aclnnFFNV3` | NN / MoE FFN | `activation` 为枚举字符串；`innerPrecise` 标量属性 |
| `aclnnNpuFormatCast` | 格式转换 | 输入格式集 `["FRACTAL_Z_3D","NCDHW",...]`；dtype 与 format 强耦合 |
| `aclnnCalculateMatmulWeightSize` | 辅助计算 | 仅计算输出，无 Tensor 真正计算；`workspaceSize`/`executor` 是唯一输出 |
| `aclnnCalculateMatmulWeightSizeV2` | 辅助计算 | 同上 V2，差异在 weight 排布 / NZ 转换 |

> **参考产物位置**：
> - 旧版（`temp/batch-20260625_195726-results/`）—— 历史产物，不一定准确；
> - 新版（`batch-20260626_182854-constraints/`）—— 基于项目实际 `assemble_result.py` 产出的新约束 JSON。
> 两者均仅作参考，**不保证完全正确**。

---

## 附录 B：从 v1 升级到 v2 的注意事项

- 本 v1 的 `inputs`/`outputs` 二级 key 体系是 `平台名`；每参数每平台一条 `ParamAttributes`；平台差异通过多条记录体现。
- `expr_type` 为自由 `str`，§7 仅作参考；若新增语义（如 `shape_value_enum`），追加到 §7.2 并附真实算子样例。
- 若增加新平台（昇腾下一代硬件），在 §5.1 字典中追加官方字符串。
- 若未来 schema 要求 `ValueWithSrcText` 包裹更多字段（如 `description`），同步更新 §3 与 §4.6.3。
- **v2 新增**：§4.6.5 NZ 格式块尺寸硬约束（覆盖所有 NZ / FRACTAL_NZ / FRACTAL_NZ_C0_16 算子）；
  §4.6.4 D 新增 NZ 块尺寸常量识别；§4.6.3 allowed_range 映射表新增 NZ 块尺寸行；
  §4.7.3 新增 NZ 落库铁律；§6.3 新增模式 5（NZ 块尺寸硬约束模板）；
  §8 新增三条 NZ 相关边缘场景；§9 新增第 13 项自检（NZ 块尺寸硬约束）；
  §10 调用模板引用 §4.6.5 与模式 5。
- v2 **不**改变 `OperatorRule` schema 字段；所有新增规则均为已有字段的更精细约束。

### B+：v2 → v2-iter-2026-07-04 变更记录（基于 `aclnnAlltoAllMatmul` 实战）

下列变更在不变 schema、不破坏现有算子的前提下，强化了"全平台覆盖"与"bool 参数
枚举化"两条关键防线，避免在多个支持的平台文档（尤其是表格化文档）中只填写单平台条目：

1. **§4.6.2 二级 key 规则重写**：删除"约束完全一致时用单个平台名"的措辞，
   改为强制逐平台复制 `ParamAttributes`（即便内容相同）。
2. **§4.6.3 bool 类型参数子节重写**：所有 bool 参数（包括无明确固定值约束的）必须产出
   `type="enum"` 条目；取值按文档限定选择 `[false]` / `[true]` / `[false, true]`。
   删除"`no_constraint.md` 中 bool 参数不产出"的旧规则。
3. **§8 边缘场景新增两行**：
   - "bool 参数（无固定值约束）" → enum `[false, true]`；
   - "`product_support` 含 ≥2 平台但某非隐式参数只产出 1 平台条目" → 漏抽，逐平台复制。
4. **§9.3 平台字典一致**：强化"逐平台复制"检查，新增"常见错误模式"反例说明。
5. **§9 自检清单新增第 14 项**：bool 参数 `allowed_range_value` 必须 enum，value 为
   `[false]` / `[true]` / `[false, true]`；自检总数从 12 → 14 → 15（含原 v2 §9.13
   的 NZ 块尺寸硬约束）。

**触发背景**：`aclnnAlltoAllMatmul` 闭环测试发现 v1 提示词产出 `iter_001` 时，
11 个非隐式参数中只有 2 个有完整的三平台条目，导致 A3/A2 平台用例无法生成，且
`transposeX2` 因 `allowed_range_value.value=[]` + `type=range` 被生成器错误填充为浮点。
`iter_002` 通过 prompt_v2（含本节所有变更）将这两个问题一次修复（用例从 0/10 → 9/30）。
本节变更同步到主提示词，使其它算子在首轮 EXTRACT 阶段即可避免同样根因。

---

## 附录 C：知识库路径速查表

> 本提示词融合了项目 `knowledge/` 目录下的全部 SKILL 内容。下表把各 SKILL 的关键规则映射到本提示词对应章节，方便维护和对照。

| 知识库路径 | 涵盖规则 | 本提示词位置 |
| ---------- | -------- | ------------ |
| [`knowledge/dimensions/SKILL.md`](knowledge/dimensions/SKILL.md) | rank vs per-dim 区分、`(N,C,H,W)` 元组解析、`[E,N1]/[N1]` HTML 多变体、维数 vs 长度区分 | §4.6.3 dimensions 解析表 |
| [`knowledge/allowed_range/SKILL.md`](knowledge/allowed_range/SKILL.md) | `range` vs `enum` 语义、枚举拆分规则（`/`/`、`/`以及`/`and`）、平台差异标注、NZ 块尺寸端点 | §4.6.3 allowed_range 文本→结构化映射 + §4.6.5 |
| [`knowledge/allowed_range/examples/numeric_range.md`](knowledge/allowed_range/examples/numeric_range.md) | `"0-100"` / `"[1,8]"` / `"大于0"` / `"取值范围为0或1"` 格式转换 | §4.6.3 allowed_range 映射表 |
| [`knowledge/allowed_range/examples/enum_string.md`](knowledge/allowed_range/examples/enum_string.md) | 字符串枚举拆分（激活函数、量化类型） | §4.6.3 allowed_range 映射表 + §8 |
| [`knowledge/allowed_range/examples/acl_int_array.md`](knowledge/allowed_range/examples/acl_int_array.md) | aclIntArray 取 JSON `null`（未传值）或特定数组 | §4.6.3 + §8 |
| [`knowledge/allowed_range/examples/no_constraint.md`](knowledge/allowed_range/examples/no_constraint.md) | 无约束参数不产出、bool 参数不产出 | §4.6.3 + §8 |
| [`knowledge/allowed_range/examples/platform_specific.md`](knowledge/allowed_range/examples/platform_specific.md) | 按平台分行处理取值差异 | §4.6.2 二级 key 规则 |
| [`knowledge/implicit_params/SKILL.md`](knowledge/implicit_params/SKILL.md) | 命名维度变量、概念词剔除、操作名剔除、常量/外部常量识别、NZ 块尺寸常量（v2） | §4.6.4 隐式参数识别 + §4.6.5 E |
| [`knowledge/relation_skills/SKILL.md`](knowledge/relation_skills/SKILL.md) | `expr` 通用规则、`.range_value` / `.dtype` / `.format` 引用、`all()/any()` 包裹、负索引、NZ 块尺寸直接写常量 | §6.1 语法细则 |
| [`knowledge/relation_skills/self_constraint.md`](knowledge/relation_skills/self_constraint.md) | 单参数自身约束 5 类模板 | §6.3 模式 4 |
| [`knowledge/relation_skills/multi_shape_choice.md`](knowledge/relation_skills/multi_shape_choice.md) | 多 Shape 候选模板（条件 / 枚举 / unless） | §6.3 模式 2 |
| [`knowledge/relation_skills/enum_conditional_shape.md`](knowledge/relation_skills/enum_conditional_shape.md) | 枚举条件 + 条件 Shape 模板 | §6.3 模式 1 |
| [`knowledge/relation_skills/presence_dependency.md`](knowledge/relation_skills/presence_dependency.md) | 存在性依赖 3 类模板 | §6.3 模式 3 |