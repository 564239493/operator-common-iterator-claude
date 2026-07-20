# 算子约束提取通用提示词 · v3 (含一段式算子支持 / 修正非 Tensor 数组类型 .shape 误用 / aclDataType 参数 dtype 固定为 string / aclIntArray 参数 dtype 固定为 int / 大小/数量语义参数的隐式 >0 约束 / 联合交叉 dtype/format 组合表用 OR-of-ANDs 析取表达)
# Operator Constraints Extraction Universal Prompt · v3 (with single-function operator support / fix non-tensor array .shape misuse / fix aclDataType param dtype to string / fix aclIntArray param dtype to int / implicit >0 constraint for size/count semantic parameters / joint cross dtype/format combo table expressed as OR-of-ANDs disjunction)

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
| 4 | 字段级提取规则 | 10 个一级字段 + dimensions/隐式参数/allowed_range/NZ 格式/条件 Shape/反向算子 Partial-Shape/大小数量语义隐式 >0 约束 详细映射 |
| 5 | 平台与 dtype 命名规范 | 强约束的字符串字典 |
| 6 | 表达式编写规范 | Python 表达式（`expr`）语法细则 + TensorList 长度/条件 Shape 等模式模板 |
| 7 | `expr_type` 取值字典 | 已知值参考表（`expr_type` 为自由 `str`） |
| 8 | 边缘场景处理 | 缺失、歧义、冲突的统一处置（含 dimensions/allowed_range/隐式参/NZ 格式/条件 Shape） |
| 9 | 自检清单 | 提取完成后必须执行 31 项检查（含条件 Shape、TensorList 长度、动态边界、Partial-Shape 自检、大小数量语义隐式 >0、公共互推导/broadcast 知识、derived_value 可求解性、格式转换 dtype 等式、联合交叉 dtype/format 组合表、FFNV3 模式型约束） |
| 10 | 调用模板 | 完整可复制的 prompt 调用片段（含知识库引用提示） |
| 附录 A | 典型算子示例 | 10 个算子的关键提取点对照 |
| 附录 B | v1→v2→v3 升级注意事项 | 升级路径与扩展占位 |
| 附录 C | 知识库路径速查表 | 本提示词与 `knowledge/` 的对应关系 |

---

## 1. 角色与目标

### 1.1 你的身份

你是一名 **昇腾 CANN 算子约束抽取专家**（Operator Constraint Extraction Specialist）。你的任务是从算子说明文档中**只抽取文档里已经显式出现**的事实信息，**绝不进行经验补全或外推**。

### 1.2 输入

- 一份算子说明文档（Markdown 或已转换为 Markdown 的 HTML），至少包含以下章节（顺序不强制）：
  - 算子名称 / 功能说明 / 应用场景
  - 函数原型（含 `aclnnXxxGetWorkspaceSize` 与执行函数）；**一段式算子**（如 `aclnnCalculateMatmulWeightSize`）只有 `aclnnXxx(...)` 单函数，无 `GetWorkspaceSize` 变体
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


class ArrayLengthWithSrcText(BaseModel):
    """数组长度字段；value 必须是非 null 的区间列表。"""
    value: Union[List[int], List[List[int]]] = Field(default_factory=list)
    src_text: str = Field(default="")
    type: Optional[str] = Field(default=None)

    model_config = {"extra": "forbid"}


# ---------- 单个参数在某个平台下的约束卡片 ----------

class ParamAttributes(BaseModel):
    """参数信息模型（按平台区分，通用结构）。"""
    description: str = Field(default="", description="参数描述")
    type: Union[ValueWithSrcText, str] = Field(..., description="参数类型（aclTensor / int64_t / bool …）")
    format: Union[ValueWithSrcText, str] = Field(..., description="数据格式（Tensor 始终使用字符串列表；非 Tensor 使用 'N/A'）")
    is_optional: Union[ValueWithSrcText, str] = Field(..., description="是否可选（true / false）")
    is_support_discontinuous: Union[ValueWithSrcText, str] = Field(..., description="是否支持非连续 Tensor")
    is_operator_param: Union[ValueWithSrcText, str] = Field(..., description="是否为算子参数")
    array_length: ArrayLengthWithSrcText = Field(
        default_factory=ArrayLengthWithSrcText,
        description=(
            "数组长度：[min,max] 表示单一区间；"
            "[[min1,max1],[min2,max2]] 表示多个可选区间；"
            "无明确长度约束使用 []，value 禁止为 null"
        ),
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
    function_signature: str = Field(..., description="函数签名（两段式取 GetWorkspaceSize 段；一段式取唯一函数）")
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

先判定模式：若文档"函数原型"章节出现 `aclnnXxxGetWorkspaceSize` → **两段式**；若只有 `aclnnXxx(...)` 单函数、无 `GetWorkspaceSize` 变体 → **一段式**。一段式判定仅为**内部**取段依据，**不得**在 JSON 中写入 `is_single_function_mode` 字段；下游由 `function_signature` 是否含 `GetWorkspaceSize` 隐式区分。

- **两段式**：取 `aclnnXxxGetWorkspaceSize` 那一段（**不是**执行函数）的完整 C 风格声明，含：
  - 返回类型（`aclnnStatus`）
  - 函数名（带 `GetWorkspaceSize` 后缀）
  - 完整参数列表（含 `workspaceSize` 与 `executor`）
- **一段式**（如 `aclnnCalculateMatmulWeightSize`）：取该唯一函数的完整 C 风格声明，含：
  - 返回类型（`aclnnStatus`）
  - 函数名（**无** `GetWorkspaceSize` 后缀，与算子同名）
  - 完整参数列表（一段式无 `workspaceSize` / `executor` / `stream`）
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
- **一段式算子例外（v3 新增）**：一段式算子没有 `workspaceSize`/`workspace`/`executor`/`stream`，其输出常为**标量指针**（如 `uint64_t *weightTensorSize`、`int64_t *xxx`）。标量指针输出**必须**进 `outputs`，**不得**因 `uint64_t*` 与 `workspaceSize` 同类型而误判为流程参数排除。其 `ParamAttributes`：`type.value` 去掉 `*`（如 `uint64_t`）、`format.value="N/A"`、`dimensions.value=[]`、`is_operator_param.value=true`、`dtype.value` 取文档"数据类型"列（空则按 type 回填，如 `["uint64_t"]`）、`is_support_discontinuous.value="N/A"`。

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
| `format.value` | 是 | `Union[List[str], str]` | Tensor 始终使用列表：单格式 → `["ND"]`，多格式 → `["ND", "NZ"]`，未提取到格式 → `[]`；标量 / 非 Tensor → `"N/A"` |
| `format.src_text` | 是 | `str` | 原文摘录 |
| `is_optional.value` | 是 | `bool` | 只能依据文档参数分类或正文显式可选语义判定：分类为"输入"/"输出"默认 `false`；分类为"可选输入"/"可选输出"或正文明确"可选/可不传/default/缺省值/可为空指针"时才为 `true`；"支持空Tensor" **不等于**可选；参数名中的 `Optional` 等字样**不得**作为可选证据 |
| `is_optional.src_text` | 是 | `str` | 摘录原文 |
| `is_support_discontinuous.value` | 是 | `Union[bool, str]` | 表格 `√` → `true`；`×` 或无标记 → `false`；非 Tensor 参数 → `"N/A"` |
| `is_support_discontinuous.src_text` | 是 | `str` | 摘录原符号 |
| `is_operator_param.value` | 是 | `bool` | 函数签名真实参数 → `true`；隐式维度变量/量化粒度 → `false` |
| `is_operator_param.src_text` | 是 | `str` | 摘录原文 |
| `array_length` | 是 | `ValueWithSrcText` 或 `str "N/A"` | 数组参数：单一区间用 `value=[min, max]` / `[len, len]`；多个可选区间用 `value=[[min1,max1],[min2,max2]]`；标量或无明确长度约束用 `value=[]` |
| `array_length.type` | 否 | `str` 或 `null` | 固定长度 → `"range"`；离散枚举 → `"enum"`；不适用 → `null` |
| `array_length.src_text` | 是 | `str` | 摘录原文（如 `"长度为2"`） |

`array_length.value` 的强制规则：

- `array_length` 必须始终为对象，禁止写 `"N/A"` 或 JSON `null`。
- `value` **禁止为 JSON `null`**；标量、不适用或没有明确长度约束时必须写 `[]`。
- 原文给出由"或 / 或者 / 或是"连接的多个闭区间时，必须逐区间保留，禁止合并成覆盖范围。
- 例如原文 `"tensorList长度支持[1, 128]或者[1, 1024]"` 必须提取为：
  `{"value": [[1, 128], [1, 1024]], "src_text": "tensorList长度支持[1, 128]或者[1, 1024]", "type": "range"}`；
  禁止错误合并为 `[1, 1024]`。
| `dtype.value` | 是 | `List[str]` | 支持的 dtype 字符串（见 §5.2）；标量参数允许填写其自身类型字符串（如 `"bool"`、`"char"`、`"int"`）；不适用 → `[]` |
| `dtype.src_text` | 是 | `str` | 摘录原文 |
| `dimensions.value` | 是 | `List[int]` 或 `[]` | **维度（rank）约束**：如 `[2, 3]` 表示 `2 ≤ rank ≤ 3`；不适用 → `[]` |
| `dimensions.src_text` | 是 | `str` | 摘录原文（如 `"2-3"`、`"2维"`） |

**`is_optional` 判定强制规则**：

- **优先依据参数表的"输入/输出/可选输入/可选输出"等分类列**：分类为"输入"或"输出"时，
  默认 `is_optional.value=false`；只有分类为"可选输入"、"可选输出"或等价明确分类时，
  才可置为 `true`。若同一参数在不同 API 表格或平台表格中分类不一致，必须按对应表格/平台
  拆分，并在 `src_text` 中摘录产生差异的原文。
- **其次依据正文中的显式可选语义**：只有出现"可选"、"可不传"、"不传即为 nullptr"、
  "缺省值/default"、"可为空指针"、"optional input" 等明确说明参数调用时可以省略/传空时，
  才可置为 `true`。
- **禁止依据参数名推断可选性**：参数名包含 `Optional`、`optional`、`Opt`、`Maybe`、
  `Nullable` 等字样时，不能据此把 `is_optional.value` 置为 `true`，也不能把这些名字片段
  作为 `is_optional.src_text`。参数名只是接口命名，不是文档约束证据。
- **"支持/仅支持输入 nullptr" 不等于参数可省略**：如果参数表分类仍为"输入"，但使用说明写
  "当前仅支持输入 nullptr"，应提取为 `is_optional.value=false`，并另行在
  `constraints_in_parameters` 中表达 `param is None` / `param == nullptr` 之类的取值约束。
  该参数仍是必填入参，只是必填值为 `nullptr`。
- **`is_optional.src_text` 必须引用真实分类或显式说明**：非可选参数推荐摘录 `"输入"`、`"输出"`
  或包含该分类的表格行原文；可选参数摘录 `"可选输入"` / `"可不传（即为nullptr）"` 等明确原文。
  禁止 `src_text` 仅写 `"Optional"`，除非这是文档分类/正文中的独立显式说明，而不是参数名的一部分。
- **反例**：`aclnnSwinAttentionScoreQuant` 的 `biasQuantOptional`、`biasDequant1Optional`、
  `biasDequant2Optional`、`paddingMask1Optional`、`paddingMask2Optional` 在参数表"输入/输出"列均为
  "输入"，因此 `is_optional.value=false`；不得因名称后缀 `Optional` 置为 `true`。其中
  `paddingMask2Optional` 的"当前仅支持输入nullptr"应作为取值约束提取，而不是作为可选性证据。

**TensorList 长度关系（强制规则）**：

- 对 `type.value="aclTensorList"`，文档中的"长度"表示 TensorList 包含的 Tensor
  个数，表达式必须写 `len(param)`；它既不是 Tensor 的 rank，也不是某个 Tensor 的
  shape，因此禁止写 `len(param.shape)`。
- `array_length` 是约束 JSON 的静态元数据字段，不是求解表达式支持的运行时属性；
  `constraints_in_parameters.expr` 中禁止出现 `param.array_length`。
- 文档明确写"P 长度与 Q 相同"时，必须生成
  `len(P) == len(Q)`；若 P 为 Optional，则写
  `(P is None) or (len(P) == len(Q))`。
- 必须逐参数、逐平台提取。多行参数描述重复出现"长度与 weight 相同"时，每个参数
  都要各自生成约束，不得按相同文案去重。
- "一般情况下/通常情况下长度相同"属于带条件关系，须继续读取综合约束确定适用条件；
  不得在条件未知时擅自生成无条件长度等式。

**dtype 为空时的类型回填规则**：
- 优先使用文档明确给出的 dtype；只有未提取到任何 dtype、即 `dtype.value=[]` 时才执行回填；
- `aclIntArray` → `["int"]`，`aclFloatArray` → `["float"]`，`aclBoolArray` → `["bool"]`；
- 其他非 Tensor 参数使用 `type.value` 回填，例如 `type.value="int"` 时输出 `dtype.value=["int"]`；
- `aclTensor` / `aclTensorList` 不得用类型名回填 dtype；其 dtype 必须来自文档，确实未说明时保持 `[]`；
- 文档明确参数"只支持传空指针""必须为空指针"或"仅支持空指针"时保持 `[]`；
- 回填仅补 `dtype.value`，不得伪造 `dtype.src_text`。
- 注：`aclIntArray` 的 dtype 不走"文档张量 dtype 列回填"——见下方「aclIntArray 参数的固定 dtype 规则」（`dtype.value` 固定 `["int"]`）。

**aclDataType 参数的固定 dtype 规则**：
- 当 `type.value == "aclDataType"` 时，`dtype.value` **固定**为 `["string"]`。`aclDataType` 是表示数据类型的标量枚举，参数本身取值为 dtype 名称字符串，故其"自身 dtype"恒为 `string`。此规则**优先级高于**上面的"类型回填规则"——无论文档"数据类型"列是否给出候选都强制执行，**不**因列值非空而改写，也**不**走"其他非 Tensor 参数使用 `type.value` 回填"分支（否则会错误地产出 `["aclDataType"]`）。
- 文档"数据类型"列里的候选（如 `FLOAT16`/`BFLOAT16`/`INT8`）是参数的**取值域**，必须写入 `allowed_range_value`：`type="enum"`、`value=["FLOAT16","BFLOAT16","INT8"]`；若文档允许"空/缺省/不传"则追加 `null` 候选。**禁止**把这些候选写进 `dtype.value`，也**禁止**给 `dtype.type` 填 `"enum"`（`ValueWithSrcText.type` 仅 `allowed_range_value` 使用，`dtype.type` 恒为 `null`）。
- 其余字段按标量非 Tensor 处理：`format.value="N/A"`、`dimensions.value=[]`（非 `aclTensor`/`aclTensorList`，按下方"类型前置规则"恒为空）、`is_support_discontinuous.value="N/A"`、`array_length="N/A"`。
- 典型场景：`aclnnCalculateMatmulWeightSizeV2` 的 `dataType`（函数原型 `aclDataType dataType`，文档"数据类型"列 `FLOAT16、BFLOAT16、INT8`）→ `type.value="aclDataType"`、`dtype.value=["string"]`、`allowed_range_value.value=["FLOAT16","BFLOAT16","INT8"]`（`type="enum"`）。错误反例：把候选抄进 `dtype.value=["FLOAT16","BFLOAT16","INT8"]`（与 `allowed_range_value` 重复，且使 `dtype` 语义从"参数自身类型"退化为"取值候选"）——`dtype` 应只表达参数自身类型，取值候选由 `allowed_range_value` 承载。注意：本规则只修正 `dtype` 字段的语义错误，**不**改变 `allowed_range_value.type=enum` 这一合规表达；若下游生成器对字符串枚举 `allowed_range_value` 仍有 Z3 求解缺陷，属生成器侧 bug，不在此规则范围内。

**aclIntArray 参数的固定 dtype 规则**：
- 当 `type.value == "aclIntArray"` 时，`dtype.value` **固定**为 `["int"]`。`aclIntArray` 是 int 元素的数组，其"自身元素 dtype"恒为 `int`，与数组元素的语义含义无关。此规则**优先级高于**上面的"类型回填规则"——无论文档"数据类型"列是否给出候选都强制执行，**不**因列值非空而改写为张量 dtype。
- 文档"数据类型"列若给 `aclIntArray` 参数列出张量 dtype（如 `FLOAT16`/`BFLOAT16`），这些列值描述的是**关联张量**的 dtype（应由独立的 `aclDataType` 参数承载，如 `aclnnCalculateMatmulWeightSizeV2.dataType`，见上方 aclDataType 规则），**不**是该数组参数的元素类型；**禁止**把它们写进 `dtype.value`（`dtype.value=["FLOAT16","BFLOAT16"]` 是把关联张量 dtype 错当成数组元素 dtype 的错误表达），也**不**写进 `allowed_range_value`（`aclIntArray` 的取值域是数组值本身，如 `[-2,-1]`，见 §4.6.3 aclIntArray 特殊取值）。
- 典型场景：`aclnnCalculateMatmulWeightSize` 的 `tensorShape`（`aclIntArray`，文档"数据类型"列 `FLOAT16`/`BFLOAT16`）→ `type.value="aclIntArray"`、`dtype.value=["int"]`；列里的 `FLOAT16`/`BFLOAT16` 描述该 shape 所属权重张量的 dtype，不是数组元素类型，不写入 `tensorShape` 的 `dtype` 或 `allowed_range_value`。错误反例：`dtype.value=["FLOAT16","BFLOAT16"]`（把关联张量 dtype 错当成数组元素 dtype）。注意：本规则只修正 `dtype` 字段的语义错误，**不**改变 `allowed_range_value` 的合规表达。

**类型前置规则（必须先于下表执行）**：
- 仅当 `type.value` 为 `aclTensor` 或 `aclTensorList` 时，才从文档提取并填写 `dimensions.value`；
- 其他所有类型（包括 `aclIntArray`、`aclFloatArray`、`aclScalar`、`bool`、整数、浮点数和字符串）的 `dimensions.value` 必须为 `[]`，即使其描述中出现"长度""数组""维度""axes"或方括号取值；
- 非 Tensor 容器的元素个数写入 `array_length`，具体数组候选值写入 `allowed_range_value`，二者都不得写入 `dimensions`。

#### 4.6.3 `ParamAttributes` 字段细则

| 字段 | 必填 | `value` 类型 | 提取规则 |
| ---- | ---- | ------------ | -------- |
| `description` | 是 | `str`（直写，非 ValueWithSrcText） | 表格"描述"列 / 文字说明原文摘录（≤ 200 字） |
| `type.value`   | 是 | `str` | 函数原型中基础类型名，去掉 `*`/`const`/`struct`（如 `aclTensor`、`int64_t`、`bool`） |
| `type.src_text`| 是 | `str` | 若文档未显式说明，填 `""` |
| `format.value` | 是 | `Union[List[str], str]` | Tensor 始终使用列表：单格式 → `["ND"]`，多格式 → `["ND", "NZ"]`，未提取到格式 → `[]`；标量 / 非 Tensor → `"N/A"` |
| `format.src_text` | 是 | `str` | 原文摘录 |
| `is_optional.value` | 是 | `bool` | 只能依据文档参数分类或正文显式可选语义判定：分类为"输入"/"输出"默认 `false`；分类为"可选输入"/"可选输出"或正文明确"可选/可不传/default/缺省值/可为空指针"时才为 `true`；"支持空Tensor" **不等于**可选；参数名中的 `Optional` 等字样**不得**作为可选证据 |
| `is_optional.src_text` | 是 | `str` | 摘录原文 |
| `is_support_discontinuous.value` | 是 | `Union[bool, str]` | 表格 `√` → `true`；`×` 或无标记 → `false`；非 Tensor 参数 → `"N/A"` |
| `is_support_discontinuous.src_text` | 是 | `str` | 摘录原符号 |
| `is_operator_param.value` | 是 | `bool` | 函数签名真实参数 → `true`；隐式维度变量/量化粒度 → `false` |
| `is_operator_param.src_text` | 是 | `str` | 摘录原文 |
| `array_length` | 是 | `ValueWithSrcText` 或 `str "N/A"` | 数组参数：单一区间用 `value=[min, max]` / `[len, len]`；多个可选区间用 `value=[[min1,max1],[min2,max2]]`；标量或无明确长度约束用 `value=[]` |
| `array_length.type` | 否 | `str` 或 `null` | 固定长度 → `"range"`；离散枚举 → `"enum"`；不适用 → `null` |
| `array_length.src_text` | 是 | `str` | 摘录原文（如 `"长度为2"`） |

`array_length.value` 的强制规则：

- `array_length` 必须始终为对象，禁止写 `"N/A"` 或 JSON `null`。
- `value` **禁止为 JSON `null`**；标量、不适用或没有明确长度约束时必须写 `[]`。
- 原文给出由"或 / 或者 / 或是"连接的多个闭区间时，必须逐区间保留，禁止合并成覆盖范围。
- 例如原文 `"tensorList长度支持[1, 128]或者[1, 1024]"` 必须提取为：
  `{"value": [[1, 128], [1, 1024]], "src_text": "tensorList长度支持[1, 128]或者[1, 1024]", "type": "range"}`；
  禁止错误合并为 `[1, 1024]`。
| `dtype.value` | 是 | `List[str]` | 支持的 dtype 字符串（见 §5.2）；标量参数允许填写其自身类型字符串（如 `"bool"`、`"char"`、`"int"`）；不适用 → `[]` |
| `dtype.src_text` | 是 | `str` | 摘录原文 |
| `dimensions.value` | 是 | `List[int]` 或 `[]` | **维度（rank）约束**：如 `[2, 3]` 表示 `2 ≤ rank ≤ 3`；不适用 → `[]` |
| `dimensions.src_text` | 是 | `str` | 摘录原文（如 `"2-3"`、`"2维"`） |

**`is_optional` 判定强制规则**：

- **优先依据参数表的"输入/输出/可选输入/可选输出"等分类列**：分类为"输入"或"输出"时，
  默认 `is_optional.value=false`；只有分类为"可选输入"、"可选输出"或等价明确分类时，
  才可置为 `true`。若同一参数在不同 API 表格或平台表格中分类不一致，必须按对应表格/平台
  拆分，并在 `src_text` 中摘录产生差异的原文。
- **其次依据正文中的显式可选语义**：只有出现"可选"、"可不传"、"不传即为 nullptr"、
  "缺省值/default"、"可为空指针"、"optional input" 等明确说明参数调用时可以省略/传空时，
  才可置为 `true`。
- **禁止依据参数名推断可选性**：参数名包含 `Optional`、`optional`、`Opt`、`Maybe`、
  `Nullable` 等字样时，不能据此把 `is_optional.value` 置为 `true`，也不能把这些名字片段
  作为 `is_optional.src_text`。参数名只是接口命名，不是文档约束证据。
- **"支持/仅支持输入 nullptr" 不等于参数可省略**：如果参数表分类仍为"输入"，但使用说明写
  "当前仅支持输入 nullptr"，应提取为 `is_optional.value=false`，并另行在
  `constraints_in_parameters` 中表达 `param is None` / `param == nullptr` 之类的取值约束。
  该参数仍是必填入参，只是必填值为 `nullptr`。
- **`is_optional.src_text` 必须引用真实分类或显式说明**：非可选参数推荐摘录 `"输入"`、`"输出"`
  或包含该分类的表格行原文；可选参数摘录 `"可选输入"` / `"可不传（即为nullptr）"` 等明确原文。
  禁止 `src_text` 仅写 `"Optional"`，除非这是文档分类/正文中的独立显式说明，而不是参数名的一部分。
- **反例**：`aclnnSwinAttentionScoreQuant` 的 `biasQuantOptional`、`biasDequant1Optional`、
  `biasDequant2Optional`、`paddingMask1Optional`、`paddingMask2Optional` 在参数表"输入/输出"列均为
  "输入"，因此 `is_optional.value=false`；不得因名称后缀 `Optional` 置为 `true`。其中
  `paddingMask2Optional` 的"当前仅支持输入nullptr"应作为取值约束提取，而不是作为可选性证据。

**TensorList 长度关系（强制规则）**：

- 对 `type.value="aclTensorList"`，文档中的"长度"表示 TensorList 包含的 Tensor
  个数，表达式必须写 `len(param)`；它既不是 Tensor 的 rank，也不是某个 Tensor 的
  shape，因此禁止写 `len(param.shape)`。
- `array_length` 是约束 JSON 的静态元数据字段，不是求解表达式支持的运行时属性；
  `constraints_in_parameters.expr` 中禁止出现 `param.array_length`。
- 文档明确写"P 长度与 Q 相同"时，必须生成
  `len(P) == len(Q)`；若 P 为 Optional，则写
  `(P is None) or (len(P) == len(Q))`。
- 必须逐参数、逐平台提取。多行参数描述重复出现"长度与 weight 相同"时，每个参数
  都要各自生成约束，不得按相同文案去重。
- "一般情况下/通常情况下长度相同"属于带条件关系，须继续读取综合约束确定适用条件；
  不得在条件未知时擅自生成无条件长度等式。

**dtype 为空时的类型回填规则**：
- 优先使用文档明确给出的 dtype；只有未提取到任何 dtype、即 `dtype.value=[]` 时才执行回填；
- `aclIntArray` → `["int"]`，`aclFloatArray` → `["float"]`，`aclBoolArray` → `["bool"]`；
- 其他非 Tensor 参数使用 `type.value` 回填，例如 `type.value="int"` 时输出 `dtype.value=["int"]`；
- `aclTensor` / `aclTensorList` 不得用类型名回填 dtype；其 dtype 必须来自文档，确实未说明时保持 `[]`；
- 文档明确参数"只支持传空指针""必须为空指针"或"仅支持空指针"时保持 `[]`；
- 回填仅补 `dtype.value`，不得伪造 `dtype.src_text`。
- 注：`aclIntArray` 的 dtype 不走"文档张量 dtype 列回填"——见下方「aclIntArray 参数的固定 dtype 规则」（`dtype.value` 固定 `["int"]`）。

**aclDataType 参数的固定 dtype 规则**：
- 当 `type.value == "aclDataType"` 时，`dtype.value` **固定**为 `["string"]`。`aclDataType` 是表示数据类型的标量枚举，参数本身取值为 dtype 名称字符串，故其"自身 dtype"恒为 `string`。此规则**优先级高于**上面的"类型回填规则"——无论文档"数据类型"列是否给出候选都强制执行，**不**因列值非空而改写，也**不**走"其他非 Tensor 参数使用 `type.value` 回填"分支（否则会错误地产出 `["aclDataType"]`）。
- 文档"数据类型"列里的候选（如 `FLOAT16`/`BFLOAT16`/`INT8`）是参数的**取值域**，必须写入 `allowed_range_value`：`type="enum"`、`value=["FLOAT16","BFLOAT16","INT8"]`；若文档允许"空/缺省/不传"则追加 `null` 候选。**禁止**把这些候选写进 `dtype.value`，也**禁止**给 `dtype.type` 填 `"enum"`（`ValueWithSrcText.type` 仅 `allowed_range_value` 使用，`dtype.type` 恒为 `null`）。
- 其余字段按标量非 Tensor 处理：`format.value="N/A"`、`dimensions.value=[]`（非 `aclTensor`/`aclTensorList`，按下方"类型前置规则"恒为空）、`is_support_discontinuous.value="N/A"`、`array_length="N/A"`。
- 典型场景：`aclnnCalculateMatmulWeightSizeV2` 的 `dataType`（函数原型 `aclDataType dataType`，文档"数据类型"列 `FLOAT16`/`BFLOAT16`/`INT8`）→ `type.value="aclDataType"`、`dtype.value=["string"]`、`allowed_range_value.value=["FLOAT16","BFLOAT16","INT8"]`（`type="enum"`）。错误反例：把候选抄进 `dtype.value=["FLOAT16","BFLOAT16","INT8"]`（与 `allowed_range_value` 重复，且使 `dtype` 语义从"参数自身类型"退化为"取值候选"）——`dtype` 应只表达参数自身类型，取值候选由 `allowed_range_value` 承载。注意：本规则只修正 `dtype` 字段的语义错误，**不**改变 `allowed_range_value.type=enum` 这一合规表达；若下游生成器对字符串枚举 `allowed_range_value` 仍有 Z3 求解缺陷，属生成器侧 bug，不在此规则范围内。

**aclIntArray 参数的固定 dtype 规则**：
- 当 `type.value == "aclIntArray"` 时，`dtype.value` **固定**为 `["int"]`。`aclIntArray` 是 int 元素的数组，其"自身元素 dtype"恒为 `int`，与数组元素的语义含义无关。此规则**优先级高于**上面的"类型回填规则"——无论文档"数据类型"列是否给出候选都强制执行，**不**因列值非空而改写为张量 dtype。
- 文档"数据类型"列若给 `aclIntArray` 参数列出张量 dtype（如 `FLOAT16`/`BFLOAT16`），这些列值描述的是**关联张量**的 dtype（应由独立的 `aclDataType` 参数承载，如 `aclnnCalculateMatmulWeightSizeV2.dataType`，见上方 aclDataType 规则），**不**是该数组参数的元素类型；**禁止**把它们写进 `dtype.value`（`dtype.value=["FLOAT16","BFLOAT16"]` 是把关联张量 dtype 错当成数组元素 dtype 的错误表达），也**不**写进 `allowed_range_value`（`aclIntArray` 的取值域是数组值本身，如 `[-2,-1]`，见 §4.6.3 aclIntArray 特殊取值）。
- 典型场景：`aclnnCalculateMatmulWeightSize` 的 `tensorShape`（`aclIntArray`，文档"数据类型"列 `FLOAT16`/`BFLOAT16`）→ `type.value="aclIntArray"`、`dtype.value=["int"]`；列里的 `FLOAT16`/`BFLOAT16` 描述该 shape 所属权重张量的 dtype，不是数组元素类型，不写入 `tensorShape` 的 `dtype` 或 `allowed_range_value`。错误反例：`dtype.value=["FLOAT16","BFLOAT16"]`（把关联张量 dtype 错当成数组元素 dtype）。注意：本规则只修正 `dtype` 字段的语义错误，**不**改变 `allowed_range_value` 的合规表达。

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

**重要：rank 区间只是弱域，不得替代具体 shape 依赖。** 当方括号变体中出现命名维度
（如 `[E,N1]`、`[N1]`、`[G,N2]`、`[K1,N1]`）时，必须同时在
`constraints_in_parameters` 中落库具体 shape 约束，把每个命名维度绑定到对应轴值。
若同一参数的变体由专家/量化粒度/模式决定，表达式必须带相应门控或使用文档可判定的
rank 分支，不能只写 `dimensions.value=[1,2]`。例如：

```text
(bias1Optional is None) or (
  (bias1Optional.shape == [N1.range_value])
  if (len(weight1.shape) == 2)
  else (bias1Optional.shape == [E.range_value, N1.range_value])
)
```

如果文档只给出"有专家/无专家"而没有显式 bool 参数，则可使用 `len(weight.shape)` 作为
专家模式门控：二维权重表示无专家，三维权重表示有专家。`src_text` 必须摘录包含
`有专家[...]；无专家[...]` 或等价短语的原文。

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
| `"padding的两个数值都需小于self最后一维度"` | `[]` | `range` | 动态边界依赖 `self.shape[-1]`，必须写入 `constraints_in_parameters`，禁止枚举几个样例值 |
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
- 参数为必选（`is_optional.value=false`）且原文未明确允许未传值/空指针时，
  `allowed_range_value` 禁止包含 `null`；C/C++ 签名是指针不等于参数可以为空。
- "未传容器"和"传入零长度容器"不是同一语义：前者为 `null`；只有原文明示传入
  长度为 0 的数组/列表实例时，才将空容器候选表示为 `[[]]`。空 Tensor 应使用
  shape/dimensions 约束表达，不在 `allowed_range_value` 中写 `"空"`。

##### aclIntArray 特殊取值（`knowledge/allowed_range/examples/acl_int_array.md`）

`aclIntArray` 参数的取值往往是**特定数组值**或**未传值**，`type` 统一设为 `enum`。
仅当上下文明确出现"传入空""缺省""空指针"，或参数确为 Optional 时，空候选使用
JSON `null`；不得仅因 C/C++ 类型是指针就添加 `null`，也不得使用字符串 `"空"`：

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

**算子特例优先级**：`aclnnBatchMatMulWeightNz` 的隐式布尔变量
`self_transposed`、`mat2_transposed` 必须按 §4.6.5 B.1 使用
`value=[true, false]`（顺序也必须一致），不得套用本表的默认 `[false, true]`。

禁止：填写 `value=[]` + `type="range"`，否则下游生成器按浮点范围填充，
会产生 `1.0`、`0.0`、`1.23e-40`、`-2147483648.0` 等非法 bool 值。

##### 无约束参数处理（`no_constraint.md` / v3 加强）

下列场景**不**产出 `allowed_range_value.value` 条目（保持 `[]`）：
- 描述只涉及 shape/dtype/format，不涉及值域；
- Tensor / TensorList 参数（`aclTensor` / `aclTensorList`），维度不属于取值范围；
- 取值上下界依赖其他参数的 shape、长度或取值；这种动态关系必须完整写入
  `constraints_in_parameters`，禁止用少量"代表性样例"伪造枚举；
- 动态表达式只编码原文明示的比较方向和边界；原文仅写"小于"时禁止自行补充
  `>= 0`、非空等额外条件；
- **bool 参数例外**：见上一子节"bool 类型参数"，必须产出 `type="enum"` 条目。

##### G. 条件 Shape 描述识别（门控维度，v3 新增，通用规则）

文档中常出现 "X 的 shape 为 (A, B)；当 Y 配置为 True 时 shape 为 (C, D)" 或者
"若 Y 为 True 则 X shape 为 (C, D)，否则为 (A, B)" 的描述。此时 shape **不是无条件
的，而是门控于某 enum/boolean 参数 Y**。必须把这条规则识别为**单一条件约束**而
非两条独立 shape 描述，否则下游生成器会把 (C, D) 与 (A, B) 当作两个独立候选而
不区分 Y，从而在生成用例时把门控后的 shape 配给非门控的 Y 取值（典型反例见
`iter_001/analysis.json` 15/21 failures：把 `transposeX2=True` 的 `x2.shape=(N, H*rankSize)`
与 `transposeX2=False` 的 `x2.shape=(H*rankSize, N)` 当成独立候选，结果 5 个
transposeX2=True 用例仍按 (H*rankSize, N) 生成）。

**识别信号词**（任一出现即触发本节规则）：

| 信号词 | 示例 |
| ------ | ---- |
| "配置为True时…为…" | "配置为True时右矩阵Shape为(N, rankSize*H)" |
| "为True时…为…" / "为False时…为…" | "transposeX2为True时x2 shape为(N, H*rankSize)" |
| "若 Y 为 X 则 Z 为 W，否则…" | "若 transposeX2 为 False 则 x2 为 (H*rankSize, N)" |
| "Y=1时…" / "Y=0时…" | "axis=1时 shape 为 …" |
| "Y=数值 时…为…" | "group=tp 时 shape 为 …" |
| "默认值…，支持修改为…" | 隐含门控 |

**门控参数 Y 的形态判定**：

- 必须是**函数签名中显式存在**的 enum/boolean 参数；
- 已被抽取到 `inputs` 且 `is_operator_param.value=true`；
- 通常其 `allowed_range_value.type="enum"`、`value` 至少含 2 个离散候选；
- 不要把 Y 当成普通标量处理；Y 的取值应被 `src_text` 摘录原文。

**输出形式**：见 §6.3 模式 6（gate_conditional_shape），必须使用 if/elif/else
分支 expr 或 `not(or)/or` 等价形式；**不允许**多条独立无条件 expr 表达同一参数
的不同 shape。

**强制提取步骤（先登记、后生成）**：

1. 扫描每个参数的完整描述，先建立"条件 Shape 登记表"，记录目标参数 X、门控参数
   Y、Y 的门控值、默认 shape、门控后 shape 和完整原文；不得在逐字段生成 JSON 时
   边读边丢弃上下文。
2. 每条登记记录必须且只能生成一组门控 shape 约束；`relation_params` 必须同时包含
   X、Y 以及表达式使用的全部隐式变量。
3. 生成结束后反向核对登记表：每条记录都必须能找到包含 `Y.range_value` 和
   `X.shape` 的表达式。若只找到 X 的无条件 shape 表达式，判定为漏抽并重写。
4. `src_text` 必须合并摘录默认 shape 与"配置为/等于某值时"的 shape 原文，禁止只
   保留默认 shape 句子。

##### D+. `shape_value_dependency` 必须按 §4.6.5 B.1 隐式 bool 门控分支（v3 合并 v4 增补）

当算子含 §4.6.5 B.1 强制新增的隐式 bool 变量
（如 `aclnnBatchMatMulWeightNz` 的 `self_transposed` / `mat2_transposed`），
其 `constraints_in_parameters` 中的**任何** `shape_value_dependency` 表达式，
只要涉及：

- `mat2.shape[j]`（j ∈ [1, 2, 3]，对应非转置 `(b, n1, k1, 16, 16)` 与转置
  `(b, k1, n1, 16, 16)` 的不同轴位）；
- `self.shape[i]`（i ∈ [1, 2]，对应非转置 `(b, m, k)` 与转置 `(b, k, m)` 的不同轴位）；

**必须**按对应隐式 bool 变量分支。

| 引用 | 隐式 bool | False 分支（默认） | True 分支（门控后） |
| ---- | --------- | ------------------- | ------------------- |
| `mat2.shape[1]` | `mat2_transposed` | n1（列轴） | k1（行轴） |
| `mat2.shape[2]` | `mat2_transposed` | k1（行轴） | n1（列轴） |
| `mat2.shape[3]` | `mat2_transposed` | k0 = 16 | n0 = 16 |
| `mat2.shape[4]` | `mat2_transposed` | n0 = 16 | k0 = 16 |
| `self.shape[1]` | `self_transposed` | m（行） | k（归约轴） |
| `self.shape[2]` | `self_transposed` | k（归约轴） | m（行） |
| `out.shape[2]` | `mat2_transposed` | n = n1 × n0 | n = k1 × k0（一般化为 n1' × n0'） |

规则要点：

1. **典型错误反例**（必须避免）：`((self.shape[2] + 15) // 16 == mat2.shape[2])`
   写成无条件，会在 `mat2_transposed=True` 时把 `self.k` 等同于 `mat2.shape[2] = n1`。
2. **正确写法**：单条 `if/elif/else` 表达式（§6.3 模式 6）或多条
   `not(...) or ...` 等价形式（§6.3 模式 6.1），必须覆盖 False/True 分支，并以
   `else True` 兜底。
3. **`relation_params` 必须包含对应隐式 bool 变量**以及张量本身，例如
   `["self", "mat2", "mat2_transposed", "self_transposed"]`。
4. **`src_text` 必须同时摘录非转置布局与转置布局原文**，不能只摘默认布局。
5. **`out.shape` 引用按 `mat2_transposed` 门控**：out 的 N 由 mat2 的最后一根归约轴与
   最后一根输出轴决定，与 `mat2_transposed` 直接相关。
6. **同一 expr 不得交叉门控**：单一 expr 只对一个隐式 bool 门控；如果同时涉及
   `self_transposed` 与 `mat2_transposed`，拆为两条独立 expr（每条对应一个 bool）。

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

##### B.1 `aclnnBatchMatMulWeightNz` 转置隐式变量（算子特例，强制）

当且仅当 `operator_name == "aclnnBatchMatMulWeightNz"` 时，即使函数签名和参数表中
没有转置标志，也必须主动向 `inputs` 新增以下两个**隐式控制变量**：

- `self_transposed`：标识 `self` 是否按转置布局解释；
- `mat2_transposed`：标识 `mat2` 是否按转置布局解释。

两个变量都不是 API 的真实入参，**不得**写入 `function_signature`，但必须为
`product_support` 中的每个平台分别生成完整 `ParamAttributes` 卡片。字段要求如下：

```json
{
  "description": "隐式变量，标识 self 是否需要转置",
  "type": {"value": "bool", "src_text": ""},
  "format": {"value": "N/A", "src_text": ""},
  "is_optional": {"value": false, "src_text": ""},
  "is_support_discontinuous": {"value": "N/A", "src_text": ""},
  "is_operator_param": {"value": false, "src_text": ""},
  "array_length": {"value": [], "src_text": "", "type": null},
  "dtype": {"value": ["bool"], "src_text": ""},
  "dimensions": {"value": [], "src_text": ""},
  "allowed_range_value": {
    "value": [true, false],
    "src_text": "由 self 的转置与非转置布局描述抽象出的隐式控制变量",
    "type": "enum"
  }
}
```

`mat2_transposed` 使用相同字段结构，仅将 `description` 和
`allowed_range_value.src_text` 中的 `self` 替换为 `mat2`。以下规则均为强制：

1. 名称必须精确为 `self_transposed`、`mat2_transposed`，不得改成
   `transposeSelf`、`transposeMat2` 或其他别名；
2. `type.value="bool"`、`dtype.value=["bool"]`、
   `allowed_range_value.type="enum"`，且
   `allowed_range_value.value=[true, false]`；不得反转顺序、不得写成字符串；
3. `is_operator_param.value=false`，因为二者是生成器求解使用的隐式变量，不是函数
   签名参数；
4. 当 `self` 或 `mat2` 的 shape、NZ 轴位、K/N 对应关系因是否转置而变化时，对应
   `constraints_in_parameters` 表达式必须引用
   `self_transposed.range_value` 或 `mat2_transposed.range_value` 作为门控条件，
   `relation_params` 同时包含实际张量和对应隐式变量；禁止把转置与非转置布局写成
   两条互不受门控的无条件约束；
5. `src_text` 优先摘录文档中转置/非转置布局的原文；变量名和布尔值是为生成器补充
   的结构化控制信息，不得伪造成函数签名原文。
6. 当 `mat2` 引用 `mat2.shape[j]`、`self` 引用 `self.shape[i]`
   （j ∈ [1, 2, 3]，i ∈ [1, 2]）时，对应的 `shape_value_dependency` **必须**按本节
   隐式 bool 变量分支。触发条件为：`operator_name == "aclnnBatchMatMulWeightNz"`、
   `constraints_in_parameters[平台]` 含 `expr_type == "shape_value_dependency"`，且
   expr 包含 `mat2.shape[j]` 或 `self.shape[i]`。三条同时成立时强制执行；具体模板见
   §6.3 模式 6.1，典型反例见 §8 边缘场景表。

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

#### 4.6.6 Forward-Output Partial-Shape 跟随约束（两轮实测闭环）

> 本规则来自 `aclnnReflectionPad1dBackward` 两轮迭代：首轮只提取末维派生关系，
> 遗漏 `gradOutput` 与 `self` 的前缀维度和 rank 关系，执行结果为 44/80；第二轮
> 补齐以下约束后为 80/80。该规则按语义触发，不按算子名硬编码。

##### A. 适用判定

满足下列条件时必须执行本节：

1. 算子属于 backward / grad / 反向传播场景；
2. 文档明确说明 `gradOutput` / `dout` 的维度与 `self` / `input` 一致，或说明其
   shape 与正向算子的 output 一致；
3. 文档又给出最后若干维由 `padding`、`kernel_size`、`stride`、`output_size`
   等参数派生，因而不能简单写成两个完整 shape 相等。

##### B. 必须拆分落库

前缀跟随与派生轴关系是彼此独立的约束，不能只保留其中一类：

```text
# 1. 非派生轴（此例为除最后一维外）必须跟随
expr_type: shape_equality
expr: gradOutput.shape[:-1] == self.shape[:-1]
relation_params: ["gradOutput", "self"]

# 2. rank 必须显式一致
expr_type: shape_equality
expr: len(gradOutput.shape) == len(self.shape)
relation_params: ["gradOutput", "self"]

# 3. 派生轴按文档公式单独表达；以下仅为 reflection_pad1d 示例
expr_type: shape_value_dependency
expr: gradOutput.shape[-1] == self.shape[-1] + padding.range_value[0] + padding.range_value[1]
relation_params: ["gradOutput", "self", "padding"]
```

参数名和切片边界必须按文档确定。上例使用 `[:-1]`，是因为 ReflectionPad1d 文档
把 padding 明确绑定到 `self` 最后一维，且末维公式单独发生变化，所以切片表示
"除最后一维外的其余维度"。不得仅凭算子名中的 2d/3d 就外推为 `[:-2]` /
`[:-3]`；只有文档明确给出多个尾部派生轴时，才能采用对应切片并逐轴表达公式。

##### C. 防漏规则

1. `gradInput.shape == self.shape` 不能替代 `gradOutput` 与 `self` 的跟随关系；
2. 末维公式成立不能推出前缀维度或 rank 一致，三类约束必须分别检查；
3. `dimensions.value` 只记录静态 rank 范围，跨参数跟随必须进入
   `constraints_in_parameters`；
4. 每条 `src_text` 摘录对应的维度一致或派生公式原文；同一句覆盖多条约束时可复用；
5. 正向算子、MatMul broadcast、卷积反向等不满足上述语义的场景不得套用此模板。

#### 4.6.7 格式-秩（format↔rank）硬对应表（v7 新增，通用规则）

> 本节来自 `aclnnNpuFormatCast` 闭环：iter_001 把 `dstTensor.dimensions=[4,8]`
> 与 `srcTensor.dimensions=[2,6]` 当成**扁平 rank 区间**提取，但漏掉 format 与
> rank 的一一对应关系，生成器把 `format` 与 `dimensions` 当独立字段采样，产出
> `NCDHW + 8D`、`NDC1HWC0 + 2D`、`FRACTAL_Z_3D + 6D` 这类非法组合。NPU 真机校验
> 直接拒绝（`AclNN_Parameter_Error(EZ1001): Input Tensor format not match it's
> shape`），CPU golden 只做 reshape 不校验 format↔rank 故全部漏网。该规则按
> `format.value` 的形态触发，**不**按算子名硬编码。
#### 4.6.3 `ParamAttributes` 字段细则

| 字段 | 必填 | `value` 类型 | 提取规则 |
| ---- | ---- | ------------ | -------- |
| `description` | 是 | `str`（直写，非 ValueWithSrcText） | 表格"描述"列 / 文字说明原文摘录（≤ 200 字） |
| `type.value`   | 是 | `str` | 函数原型中基础类型名，去掉 `*`/`const`/`struct`（如 `aclTensor`、`int64_t`、`bool`） |
| `type.src_text`| 是 | `str` | 若文档未显式说明，填 `""` |
| `format.value` | 是 | `Union[List[str], str]` | Tensor 始终使用列表：单格式 → `["ND"]`，多格式 → `["ND", "NZ"]`，未提取到格式 → `[]`；标量 / 非 Tensor → `"N/A"` |
| `format.src_text` | 是 | `str` | 原文摘录 |
| `is_optional.value` | 是 | `bool` | 只能依据文档参数分类或正文显式可选语义判定：分类为"输入"/"输出"默认 `false`；分类为"可选输入"/"可选输出"或正文明确"可选/可不传/default/缺省值/可为空指针"时才为 `true`；"支持空Tensor" **不等于**可选；参数名中的 `Optional` 等字样**不得**作为可选证据 |
| `is_optional.src_text` | 是 | `str` | 摘录原文 |
| `is_support_discontinuous.value` | 是 | `Union[bool, str]` | 表格 `√` → `true`；`×` 或无标记 → `false`；非 Tensor 参数 → `"N/A"` |
| `is_support_discontinuous.src_text` | 是 | `str` | 摘录原符号 |
| `is_operator_param.value` | 是 | `bool` | 函数签名真实参数 → `true`；隐式维度变量/量化粒度 → `false` |
| `is_operator_param.src_text` | 是 | `str` | 摘录原文 |
| `array_length` | 是 | `ValueWithSrcText` 或 `str "N/A"` | 数组参数：单一区间用 `value=[min, max]` / `[len, len]`；多个可选区间用 `value=[[min1,max1],[min2,max2]]`；标量或无明确长度约束用 `value=[]` |
| `array_length.type` | 否 | `str` 或 `null` | 固定长度 → `"range"`；离散枚举 → `"enum"`；不适用 → `null` |
| `array_length.src_text` | 是 | `str` | 摘录原文（如 `"长度为2"`） |

`array_length.value` 的强制规则：

- `array_length` 必须始终为对象，禁止写 `"N/A"` 或 JSON `null`。
- `value` **禁止为 JSON `null`**；标量、不适用或没有明确长度约束时必须写 `[]`。
- 原文给出由"或 / 或者 / 或是"连接的多个闭区间时，必须逐区间保留，禁止合并成覆盖范围。
- 例如原文 `"tensorList长度支持[1, 128]或者[1, 1024]"` 必须提取为：
  `{"value": [[1, 128], [1, 1024]], "src_text": "tensorList长度支持[1, 128]或者[1, 1024]", "type": "range"}`；
  禁止错误合并为 `[1, 1024]`。
| `dtype.value` | 是 | `List[str]` | 支持的 dtype 字符串（见 §5.2）；标量参数允许填写其自身类型字符串（如 `"bool"`、`"char"`、`"int"`）；不适用 → `[]` |
| `dtype.src_text` | 是 | `str` | 摘录原文 |
| `dimensions.value` | 是 | `List[int]` 或 `[]` | **维度（rank）约束**：如 `[2, 3]` 表示 `2 ≤ rank ≤ 3`；不适用 → `[]` |
| `dimensions.src_text` | 是 | `str` | 摘录原文（如 `"2-3"`、`"2维"`） |

**`is_optional` 判定强制规则**：

- **优先依据参数表的"输入/输出/可选输入/可选输出"等分类列**：分类为"输入"或"输出"时，
  默认 `is_optional.value=false`；只有分类为"可选输入"、"可选输出"或等价明确分类时，
  才可置为 `true`。若同一参数在不同 API 表格或平台表格中分类不一致，必须按对应表格/平台
  拆分，并在 `src_text` 中摘录产生差异的原文。
- **其次依据正文中的显式可选语义**：只有出现"可选"、"可不传"、"不传即为 nullptr"、
  "缺省值/default"、"可为空指针"、"optional input" 等明确说明参数调用时可以省略/传空时，
  才可置为 `true`。
- **禁止依据参数名推断可选性**：参数名包含 `Optional`、`optional`、`Opt`、`Maybe`、
  `Nullable` 等字样时，不能据此把 `is_optional.value` 置为 `true`，也不能把这些名字片段
  作为 `is_optional.src_text`。参数名只是接口命名，不是文档约束证据。
- **"支持/仅支持输入 nullptr" 不等于参数可省略**：如果参数表分类仍为"输入"，但使用说明写
  "当前仅支持输入 nullptr"，应提取为 `is_optional.value=false`，并另行在
  `constraints_in_parameters` 中表达 `param is None` / `param == nullptr` 之类的取值约束。
  该参数仍是必填入参，只是必填值为 `nullptr`。
- **`is_optional.src_text` 必须引用真实分类或显式说明**：非可选参数推荐摘录 `"输入"`、`"输出"`
  或包含该分类的表格行原文；可选参数摘录 `"可选输入"` / `"可不传（即为nullptr）"` 等明确原文。
  禁止 `src_text` 仅写 `"Optional"`，除非这是文档分类/正文中的独立显式说明，而不是参数名的一部分。
- **反例**：`aclnnSwinAttentionScoreQuant` 的 `biasQuantOptional`、`biasDequant1Optional`、
  `biasDequant2Optional`、`paddingMask1Optional`、`paddingMask2Optional` 在参数表"输入/输出"列均为
  "输入"，因此 `is_optional.value=false`；不得因名称后缀 `Optional` 置为 `true`。其中
  `paddingMask2Optional` 的"当前仅支持输入nullptr"应作为取值约束提取，而不是作为可选性证据。

**TensorList 长度关系（强制规则）**：

- 对 `type.value="aclTensorList"`，文档中的"长度"表示 TensorList 包含的 Tensor
  个数，表达式必须写 `len(param)`；它既不是 Tensor 的 rank，也不是某个 Tensor 的
  shape，因此禁止写 `len(param.shape)`。
- `array_length` 是约束 JSON 的静态元数据字段，不是求解表达式支持的运行时属性；
  `constraints_in_parameters.expr` 中禁止出现 `param.array_length`。
- 文档明确写"P 长度与 Q 相同"时，必须生成
  `len(P) == len(Q)`；若 P 为 Optional，则写
  `(P is None) or (len(P) == len(Q))`。
- 必须逐参数、逐平台提取。多行参数描述重复出现"长度与 weight 相同"时，每个参数
  都要各自生成约束，不得按相同文案去重。
- "一般情况下/通常情况下长度相同"属于带条件关系，须继续读取综合约束确定适用条件；
  不得在条件未知时擅自生成无条件长度等式。

**dtype 为空时的类型回填规则**：
- 优先使用文档明确给出的 dtype；只有未提取到任何 dtype、即 `dtype.value=[]` 时才执行回填；
- `aclIntArray` → `["int"]`，`aclFloatArray` → `["float"]`，`aclBoolArray` → `["bool"]`；
- 其他非 Tensor 参数使用 `type.value` 回填，例如 `type.value="int"` 时输出 `dtype.value=["int"]`；
- `aclTensor` / `aclTensorList` 不得用类型名回填 dtype；其 dtype 必须来自文档，确实未说明时保持 `[]`；
- 文档明确参数"只支持传空指针""必须为空指针"或"仅支持空指针"时保持 `[]`；
- 回填仅补 `dtype.value`，不得伪造 `dtype.src_text`。
- 注：`aclIntArray` 的 dtype 不走"文档张量 dtype 列回填"——见下方「aclIntArray 参数的固定 dtype 规则」（`dtype.value` 固定 `["int"]`）。

**aclDataType 参数的固定 dtype 规则**：
- 当 `type.value == "aclDataType"` 时，`dtype.value` **固定**为 `["string"]`。`aclDataType` 是表示数据类型的标量枚举，参数本身取值为 dtype 名称字符串，故其"自身 dtype"恒为 `string`。此规则**优先级高于**上面的"类型回填规则"——无论文档"数据类型"列是否给出候选都强制执行，**不**因列值非空而改写，也**不**走"其他非 Tensor 参数使用 `type.value` 回填"分支（否则会错误地产出 `["aclDataType"]`）。
- 文档"数据类型"列里的候选（如 `FLOAT16`/`BFLOAT16`/`INT8`）是参数的**取值域**，必须写入 `allowed_range_value`：`type="enum"`、`value=["FLOAT16","BFLOAT16","INT8"]`；若文档允许"空/缺省/不传"则追加 `null` 候选。**禁止**把这些候选写进 `dtype.value`，也**禁止**给 `dtype.type` 填 `"enum"`（`ValueWithSrcText.type` 仅 `allowed_range_value` 使用，`dtype.type` 恒为 `null`）。
- 其余字段按标量非 Tensor 处理：`format.value="N/A"`、`dimensions.value=[]`（非 `aclTensor`/`aclTensorList`，按下方"类型前置规则"恒为空）、`is_support_discontinuous.value="N/A"`、`array_length="N/A"`。
- 典型场景：`aclnnCalculateMatmulWeightSizeV2` 的 `dataType`（函数原型 `aclDataType dataType`，文档"数据类型"列 `FLOAT16、BFLOAT16、INT8`）→ `type.value="aclDataType"`、`dtype.value=["string"]`、`allowed_range_value.value=["FLOAT16","BFLOAT16","INT8"]`（`type="enum"`）。错误反例：把候选抄进 `dtype.value=["FLOAT16","BFLOAT16","INT8"]`（与 `allowed_range_value` 重复，且使 `dtype` 语义从"参数自身类型"退化为"取值候选"）——`dtype` 应只表达参数自身类型，取值候选由 `allowed_range_value` 承载。注意：本规则只修正 `dtype` 字段的语义错误，**不**改变 `allowed_range_value.type=enum` 这一合规表达；若下游生成器对字符串枚举 `allowed_range_value` 仍有 Z3 求解缺陷，属生成器侧 bug，不在此规则范围内。

**aclIntArray 参数的固定 dtype 规则**：
- 当 `type.value == "aclIntArray"` 时，`dtype.value` **固定**为 `["int"]`。`aclIntArray` 是 int 元素的数组，其"自身元素 dtype"恒为 `int`，与数组元素的语义含义无关。此规则**优先级高于**上面的"类型回填规则"——无论文档"数据类型"列是否给出候选都强制执行，**不**因列值非空而改写为张量 dtype。
- 文档"数据类型"列若给 `aclIntArray` 参数列出张量 dtype（如 `FLOAT16`/`BFLOAT16`），这些列值描述的是**关联张量**的 dtype（应由独立的 `aclDataType` 参数承载，如 `aclnnCalculateMatmulWeightSizeV2.dataType`，见上方 aclDataType 规则），**不**是该数组参数的元素类型；**禁止**把它们写进 `dtype.value`（`dtype.value=["FLOAT16","BFLOAT16"]` 是把关联张量 dtype 错当成数组元素 dtype 的错误表达），也**不**写进 `allowed_range_value`（`aclIntArray` 的取值域是数组值本身，如 `[-2,-1]`，见 §4.6.3 aclIntArray 特殊取值）。
- 典型场景：`aclnnCalculateMatmulWeightSize` 的 `tensorShape`（`aclIntArray`，文档"数据类型"列 `FLOAT16`/`BFLOAT16`）→ `type.value="aclIntArray"`、`dtype.value=["int"]`；列里的 `FLOAT16`/`BFLOAT16` 描述该 shape 所属权重张量的 dtype，不是数组元素类型，不写入 `tensorShape` 的 `dtype` 或 `allowed_range_value`。错误反例：`dtype.value=["FLOAT16","BFLOAT16"]`（把关联张量 dtype 错当成数组元素 dtype）。注意：本规则只修正 `dtype` 字段的语义错误，**不**改变 `allowed_range_value` 的合规表达。

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

<!-- __MARKER_A__ -->