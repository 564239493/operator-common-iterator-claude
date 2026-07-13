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

本提示词共 10 章 + 1 附录（知识库路径速查表）。历史变更记录见 `prompts/CHANGELOG.md`，典型算子示例见 `prompts/examples.md`，二者不参与提取。建议按下列顺序阅读并使用：

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
| 9 | 自检清单 | 提取完成后必须执行 30 项检查（含条件 Shape、TensorList 长度、动态边界、Partial-Shape 自检、大小数量语义隐式 >0、公共互推导/broadcast 知识、derived_value 可求解性、格式转换 dtype 等式、联合交叉 dtype/format 组合表） |
| 10 | 调用模板 | 完整可复制的 prompt 调用片段（含知识库引用提示） |
| 附录 | 知识库路径速查表 | 本提示词与 `knowledge/` 的对应关系（维护参考） |
| （外部）`CHANGELOG.md` | v1→v3 变更记录 | 不参与提取，维护参考 |
| （外部）`examples.md` | 10 算子对齐示例 | 不参与提取，维护参考 |

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

### ttk / torch_npu Python 原型提取规则（toolchain=ttk）

> 仅当 `toolchain=ttk` 时适用（由 `extract-constraints` SKILL 步骤 2 分支进入）。`atk` 走 §4.4 既有的 aclnn C 两段/一段规则，本节不生效。ttk 产出的 `constraints.json` 与 atk **同 schema**（11 字段 `extra:forbid`），本节只规定 Python 原型相关的 delta；通用规则（空值/枚举/range/维度/跨参关系等）一律沿用既有章节。

**A. function_signature 与 operator_name**
- 取文档“函数原型”段整行 Python 签名**逐字**（如 `torch_npu.npu_fused_infer_attention_score(query, key, value, *, pse_shift=None, ...) -> (Tensor, Tensor)`），不含 `GetWorkspaceSize`/`workspaceSize`/`executor`/`stream`/`aclnnStatus`。
- `operator_name` 取**完整点分名**（与文档标题一致，如 `torch_npu.npu_fused_infer_attention_score`）。
- **不得**写 `is_single_function_mode` / `toolchain` 字段进 JSON。

**B. 参数分类**（按 Python 语法，非 C regex）
- `*` **之前** = 位置参数（如 query/key/value）：`inputs`，`type=aclTensor`，`required=true`。
- `*` **之后** = keyword-only 参数：均 `required=false`，按注解映射 type：
  - `Tensor` → `aclTensor`（如 pse_shift/atten_mask/block_table/key_shared_prefix/value_shared_prefix/query_rope/key_rope 等）；
  - `List[int]` → `aclIntArray`（如 actual_seq_lengths/actual_seq_lengths_kv/actual_shared_prefix_len）；
  - `int` → `aclInt`（如 num_heads/pre_tokens/next_tokens/sparse_mode/inner_precise/block_size/antiquant_mode/key_antiquant_mode/value_antiquant_mode/num_key_value_heads）；
  - `float` → `aclFloat`（如 scale）；
  - `bool` → `aclBool`（如 softmax_lse_flag）；
  - `str`（带枚举）→ attr + `allowed_range_value` enum（`input_layout` 候选 [BSH,BSND,BNSD,BNSD_BSND,BSH_NBSD,BSND_NBSD,BNSD_NBSD,TND,TND_NTD,NTD_TND]）。
- 默认值写进对应 input 的 `default`；`=None` → 空值候选 `null`（沿用既有空值规则）。

**C. outputs**（从返回标注，非指针输出参数）
- `-> (Tensor, Tensor)` → `outputs=["attention_out","softmax_lse"]`（名取自文档“返回值说明”段）；attention_out dtype 候选 [FLOAT16,BFLOAT16,INT8]、format=ND；softmax_lse dtype=FLOAT32、format=ND。
- 返回 tuple 有 N 个 `Tensor` → `outputs` 列 N 个名，按文档“返回值说明”段顺序对齐。

**D. dtype 规范化**（文档小写 → 规范大写）
- float16→FLOAT16，bfloat16→BFLOAT16，int8→INT8，int64→INT64，float32→FLOAT32，bool→BOOL，uint8→UINT8，uint64→UINT64；`int4`(int32) 记 `INT32` 并在 `src_text` 注明“int4 打包成 int32”。

**E. constraints_in_parameters**（照常从文档“约束说明”段提取，按 platform 分桶）
- schema 与 atk 同——约束面是算子语义（张量 shape/dtype/跨参关系），与调用语言无关。FIA 约束面极大（Q_S=1 vs >1、MLA rope、page attention、quant/antiquant、prefix、padding、NZ、TND），首轮提取不全由后续 OPTIMIZE/SUPPLEMENT 轮补，不阻断。

**F. 其余字段照既有规则**（`function_explanation`/`return_info`/`dtype_support_description`/`format_support_description`/`product_support`/`deterministic_computing`）从文档对应段提取；本节只规定 Python 原型 delta（A–E），不重述通用规则。

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
- 原文给出由“或 / 或者 / 或是”连接的多个闭区间时，必须逐区间保留，禁止合并成覆盖范围。
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

- 对 `type.value="aclTensorList"`，文档中的“长度”表示 TensorList 包含的 Tensor
  个数，表达式必须写 `len(param)`；它既不是 Tensor 的 rank，也不是某个 Tensor 的
  shape，因此禁止写 `len(param.shape)`。
- `array_length` 是约束 JSON 的静态元数据字段，不是求解表达式支持的运行时属性；
  `constraints_in_parameters.expr` 中禁止出现 `param.array_length`。
- 文档明确写“P 长度与 Q 相同”时，必须生成
  `len(P) == len(Q)`；若 P 为 Optional，则写
  `(P is None) or (len(P) == len(Q))`。
- 必须逐参数、逐平台提取。多行参数描述重复出现“长度与 weight 相同”时，每个参数
  都要各自生成约束，不得按相同文案去重。
- “一般情况下/通常情况下长度相同”属于带条件关系，须继续读取综合约束确定适用条件；
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
  `constraints_in_parameters`，禁止用少量“代表性样例”伪造枚举；
- 动态表达式只编码原文明示的比较方向和边界；原文仅写“小于”时禁止自行补充
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

1. 扫描每个参数的完整描述，先建立“条件 Shape 登记表”，记录目标参数 X、门控参数
   Y、Y 的门控值、默认 shape、门控后 shape 和完整原文；不得在逐字段生成 JSON 时
   边读边丢弃上下文。
2. 每条登记记录必须且只能生成一组门控 shape 约束；`relation_params` 必须同时包含
   X、Y 以及表达式使用的全部隐式变量。
3. 生成结束后反向核对登记表：每条记录都必须能找到包含 `Y.range_value` 和
   `X.shape` 的表达式。若只找到 X 的无条件 shape 表达式，判定为漏抽并重写。
4. `src_text` 必须合并摘录默认 shape 与“配置为/等于某值时”的 shape 原文，禁止只
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

<!-- 以下 §4.6.5–§4.6.12 已移至 prompts/modules/（nz_matmul / backward_partial / format_cast / implicit_pos / broadcast），由 scripts/select_prompt.py 按算子类按需加载。未加载时，§9 中对应的条件自检项不触发。 -->

### 4.7 `constraints_in_parameters`（跨参数 / 单参数约束）

#### 4.7.1 顶层 key

- 平台名；不存在平台差异时**各平台使用相同的约束列表**（不要删减为单项 `"common"`）。

#### 4.7.2 `InterParamConstraint` 字段

| 字段 | 必填 | 说明 |
| ---- | ---- | ---- |
| `expr_type` | 是 | **自由字符串**。优先从 §7 字典中选用；若字典无法覆盖，允许使用实际语义值（如 `cross_param_constraint`、`parameter_representation`、`self_value_enum`、`self_string_length`、`self_value_dependency`、`shape_choice`） |
| `expr` | 是 | 规范化后合法的 Python 布尔表达式（第 6 章）；允许裸 `null`，执行前转换为 `None`；**不得为空字符串**——无法形式化的约束改记入相关参数 `description`/`src_text`，不产出 `constraints_in_parameters` 条目（见 §4.6.8 C、§4.6.10 B.4、§8、§6.1 第 10 条） |
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
8. **条件 Shape 约束必须按门控参数分支（v3 新增）**：当某参数 X 的 shape 由
   enum/boolean 门控参数 Y 的取值决定（见 §4.6.3 G），必须在
   `constraints_in_parameters[平台]` 中为 X 产出**单一条件 shape 约束**条目，
   形如：

   ```text
   # 推荐写法：if/elif/else 链（参见 §6.3 模式 6）
   (X.shape == [A, B]) if (Y.range_value == "{value_A}")
   else (X.shape == [C, D]) if (Y.range_value == "{value_B}")
   else True
   ```

   或者使用 `unless` 等价形式（多条分支合并）：

   ```text
   not(Y.range_value == "{value_A}") or (X.shape == [A, B])
   not(Y.range_value == "{value_B}") or (X.shape == [C, D])
   ```

   **不允许**产出以下"两条独立无条件 expr"的退化形式：

   ```text
   # 反例：丢失门控，生成器会把 [A, B] 与 [C, D] 当作独立候选
   X.shape == [A, B]
   X.shape == [C, D]
   ```

   若文档同时给出 Y 的默认值与非默认值两套 shape（例如 "shape 为 (H*rankSize, N)，配
   置为True时为 (N, H*rankSize)"），**必须**把它们合并为**单一**带门控的 expr 条目，
   并在 `src_text` 中摘录原文中的"配置为True时…为…"那一短语。

9. **Forward-Output Partial-Shape 必须显式落库**：满足 §4.6.6 的 backward /
   grad 场景，必须同时检查并落库“前缀维度跟随”“rank 一致”“文档明确给出的派生轴
   公式”。不得用 `gradInput.shape == self.shape` 或仅末维公式代替前两项。

10. **大小/数量语义参数的隐式 >0 约束（v3 增补）**：当某标量取值参数的
    `description` 含"空间大小"/"的数据量"/"元素个数"/"的数量"/"占用空间大小"
    等表示"大小/数量/个数"的语义短语时，必须按 §4.6.9 在 `constraints_in_parameters`
    中追加 `P.range_value > 0` 条目（`expr_type=value_dependency`，
    `allowed_range_value.value=[]`）。不适用于 shape/dtype/format/枚举/bool 参数。
11. **公共互推导 / broadcast 引用必须展开（v3 增补）**：当文档引用
    `互推导关系.md` 或 `broadcast关系.md`，必须按 §4.6.10 产出对应的
    `type_dependency` / `shape_broadcast` / `shape_value_dependency` 约束；不得只在
    `src_text` 中保留链接。
12. **MatMul Reduce 维度相等必须落库（v3 增补）**：当文档写
    "mat2 的 Reduce 维度需要与 self 的 Reduce 维度大小相等"、"self 的 last dim 与
    mat2 的 penultimate dim 相同" 等语义时，必须按实际布局落为 `shape_value_dependency`。
    对 `aclnnBatchMatMulWeightNz`：

   ```text
   expr_type: shape_value_dependency
   expr: (self.shape[2] == mat2.shape[2] * 16)
           if (self_transposed.range_value == False and mat2_transposed.range_value == False)
         else (self.shape[2] == mat2.shape[1] * 16)
           if (self_transposed.range_value == False and mat2_transposed.range_value == True)
         else (self.shape[1] == mat2.shape[2] * 16)
           if (self_transposed.range_value == True and mat2_transposed.range_value == False)
         else (self.shape[1] == mat2.shape[1] * 16)
           if (self_transposed.range_value == True and mat2_transposed.range_value == True)
         else True
   relation_params: ["self", "mat2", "self_transposed", "mat2_transposed"]
   src_text: "mat2 的 Reduce 维度需要与 self 的 Reduce 维度大小相等；NZ k1 与 k0=16 表示 Reduce 维度"
   ```

   若文档写 `ceil(k, k0)=k1`，且 `k0=16`，也可等价表达为
   `((K + 15) // 16 == k1)`；但必须保证生成用例时真实 NPU 校验的 Reduce 维度一致，
   不得让 `self.shape[-1]=2034` 而 `mat2` 还按 `k1*16=2048` 通过 CPU golden。

13. **派生值可求解约束必须落库（v3 增补）**：当 §4.6.8 适用（存在派生子接口）且
    文档存在从子接口入参到派生输出 `D` 取值的**确定映射**（`dtype_support_description` /
    `format_support_description` 或正文 combo 表）时，必须在 `constraints_in_parameters`
    中产出 `derived_value` 条目，其 `expr` 编码该映射为可 `eval()` 的布尔表达式
    （恒等映射用等式、查找表用析取、格式派生用 actualFormat→format 析取，见 §6.3 模式 9）；
    `expr` **不得为空串**；`relation_params` 包含 `D` 及全部键参数。文档无确定映射时
    不产出该条目，派生语义由 `[DERIVED]` description 承载（§4.6.8 C.2）。

14. **格式转换算子 dtype 等式必须落库（v3 增补）**：当 §4.6.12 适用（算子为格式转换 /
    布局变换类，文档 dtype 表每行 src.dtype == dst.dtype）时，必须在
    `constraints_in_parameters[每个支持平台]` 中追加 `srcTensor.dtype == dstTensor.dtype`
    的 `type_equality` 约束；dstTensor 值域沿用 src，不得按不同 dtype 负值域生成。

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
- **dtype×format 交叉联合表禁用（v3 增补）**：当组合表**同一行同时含 dtype 列与
  format 列，且 dtype 与 format 存在行内依赖**（不同 dtype 对应不同 format 候选；
  判据：若把表按列拆成「纯 dtype 表 + 纯 format 表」会丢失信息、产生原本非法的
  dtype×format 组合）——如 `srcTensor.dtype × dstTensor.dtype × dstTensor.format`
  中 INT8→FRACTAL_NZ、INT32→FRACTAL_NZ_C0_16——**不得**填入本字段；拆解会丢失行内
  dtype↔format 对应，并产生数值枚举码与 dtype 名混用（如 `additionalDtype="2"` 来自
  `ACL_INT8(2)`）。此类**交叉**表必须落库为 `constraints_in_parameters` 的一条
  OR-of-ANDs `derived_value`/`cross_param_constraint` expr（见 §6.3 模式 9「主接口
  联合组合表」），本字段与 `format_support_description` 对该算子留 `{}`。
- **以下两类仍填本字段，不属交叉表**：① **纯 dtype 组合表**——只有 dtype 列（哪怕
  跨多个参数，如 `x1.dtype × x2.dtype × out.dtype`，"各产品下 x1/x2/out 的 dtype
  组合"即此形态）；② **同表但独立的 dtype+format 表**——dtype 列与 format 列共存但
  互不影响（任意 dtype 都可配任意 format，拆开不丢失信息），此时按"单独 dtype 约束 +
  单独 format 约束"处理：dtype 部分填 `dtype_support_description`、format 部分填
  `format_support_description`（或用 `type_equality` + format 枚举），不强求 OR-of-ANDs。

### 4.10 `format_support_description`（format 组合支持表）

- 结构与 `dtype_support_description` 对称：key 为平台名，value 为格式组合列表；
- 仅当文档存在**显式 format 组合表格**时填写；
- 无此表时填 `{}`。
- **dtype×format 交叉联合表禁用（v3 增补）**：与 §4.9 同理，**同一行同时含 dtype 列
  与 format 列且 dtype 与 format 存在行内依赖**的交叉表**不得**填入本字段；禁止用
  「`srcTensor` format 列表 × `dstFormat` 笛卡尔积、`actualFormat=dstFormat`」之类
  凭空捏造的格式组合凑数（典型反例：aclnnNpuFormatCast A3/A2 `format_support_description`
  出现 `srcTensor=ND × dstFormat∈{2,29,30,32,33}` 的 25 行捏造组合）。交叉表必须落库
  为 OR-of-ANDs expr（§6.3 模式 9），本字段留 `{}`。
- **以下两类仍填本字段，不属交叉表**：① **纯 format 组合表**——只有 format 列（哪怕
  跨多个参数，如 `x1.format × x2.format × out.format`）；② **同表但独立的 dtype+format
  表**（任意 dtype 配任意 format、拆开不丢失信息）的 format 部分——按 §4.9 同类情形
  处理，不强求 OR-of-ANDs。

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
ND, NC, NCL, NCHW, NCDHW, NHWC, HWCN, NZ, FRACTAL_NZ, FRACTAL_Z, FRACTAL_Z_3D,
NDC1HWC0, FRACTAL_NZ_C0_16, FRACTAL_NZ_C0_32, NDHWC, NCHW_VECT_C0_16, NC1HWC0, NC1HWC0_C04
```

- Tensor 参数始终用 `List[str]`：多格式如 `["FRACTAL_Z_3D", "ND"]`，单格式也必须写成 `["ND"]`，没有明确格式时使用 `[]`；
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
5. **"维数 vs 长度"**：表达式中的 `len(x.shape)` 表示 rank（仅 `aclTensor` / `aclTensorList` 有 `.shape`），"shape size" 永远指 rank，**不是**各维大小乘积。`aclIntArray` / `aclFloatArray` / `aclBoolArray` **没有 `.shape`**，其元素个数直接写 `len(paramName)`（裸参数名），**禁止** `len(paramName.shape)`。
6. **负索引优先**：当约束引用了以字母命名的维度（如 `H`、`W`）且该维度在 shape 描述中**始终处于固定语义位置**（如"最后一维"），必须使用 `shape[-1]` 而非固定正索引 `shape[1]` 或 `shape[3]`。
7. **命名维度变量 / 外部常量引用**：使用 `变量名.range_value` 形式（如 `BS.range_value`、`rankSize.range_value`），不写 `BS.shape[0]`。
8. **已知常量直接使用数值**：若文档给出 `k0 = 16` 这种赋值，表达式里直接写 `16`，不需要 `k0.range_value`；NZ 块尺寸硬约束中 `mat2.shape[3] == 16` / `mat2.shape[4] == 16` 即此规则的体现（v2 新增）。
9. **禁止关键字**：`lambda`、非蕴含三元运算符滥用、`implies`、伪代码、平台值作为判断条件。
10. **`null` / `None`**：表达式允许使用 JSON 风格裸值 `null`，执行前会规范化为
    Python `None`；也可直接写 `None`。它只用于空值、可选值和存在性判断，例如
    `bias is null` 或 `bias is not None`，
    不得作为数值区间端点参与 `<`、`<=`、`>`、`>=`。**整条约束无法形式化为
    Python 布尔表达式时，不得产出空 `expr` 的 `constraints_in_parameters` 条目**
    （违 §4.7.2）；改把语义记入相关参数 `description`/`src_text`。不要用整个
    JSON 值 `null` 代替 `expr` 字符串。
11. **参数名冲突**：当参数名为 `max`/`min`/`sum` 等内置函数名时，表达式中**不要再调用**同名内置函数；`relation_params` 仍写原名。
12. **Partial-Shape 切片**：当文档明确表明只有最后一维是派生轴时，
    `gradOutput.shape[:-1] == self.shape[:-1]` 是合法的 `shape_equality`
    表达式。`-1` 表示排除这个已被文档确认的末维派生轴，并非由 backward /
    1d 名称自动决定。必须直接使用 shape 切片等式，不得改写为
    `in [self.shape[:-1]]`，也不得用无关的 `gradInput.shape` 近似替代；其他切片
    边界只有在文档明确给出对应派生轴时才能使用。

### 6.2 表达式与 src_text 的对应

- `expr` 表达什么，`src_text` 就摘录什么；
- 表达式无法直接对应原句（如文档只给 "shape 与 x 一致"）时，`expr` 写 `out.shape == x.shape`，`src_text` 摘录 `"out 的 shape 与 x 保持一致"`。

### 6.3 表达式模式库（按关系特征匹配）

> 来自 `knowledge/relation_skills/` 4 个模式文件。按以下流程匹配：
> 先识别场景特征 → 套用对应模板。

#### 模式 0：Optional TensorList 长度相等

**适用场景**：参数 P、Q 均为 `aclTensorList`，文档明确说明“P 长度与 Q 相同”。

```text
# P 为 Optional
(P is None) or (len(P) == len(Q))

# P 为必选
len(P) == len(Q)
```

示例：

```text
(biasOptional is None) or (len(biasOptional) == len(weight))
(offsetOptional is None) or (len(offsetOptional) == len(weight))
(antiquantScaleOptional is None) or (len(antiquantScaleOptional) == len(weight))
(antiquantOffsetOptional is None) or (len(antiquantOffsetOptional) == len(weight))
```

`relation_params` 必须为 `[P, Q]`（按表达式首次出现顺序去重），`expr_type` 可使用
`presence_dependency`。禁止以下错误写法：

```text
len(P.shape) == Q.array_length
P.array_length == Q.array_length
```

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
# 维度数量（aclTensor/aclTensorList）：{min} <= len({param}.shape) <= {max}
# 数组长度（aclIntArray/aclFloatArray/aclBoolArray，无 .shape）：{min} <= len({param}) <= {max}
# 各维大小（aclTensor/aclTensorList）：all(d <= {max} for d in {param}.shape)
# 空 Tensor 限制（aclTensor/aclTensorList）：all(d > 0 for d in {param}.shape)
```

**非 Tensor 数组类型的长度区间**：`aclIntArray` / `aclFloatArray` / `aclBoolArray` **本身即数组**（int/float/bool 的一维序列），**没有 `.shape` 属性**——`.shape` 仅对 `aclTensor` / `aclTensorList` 合法。当文档对这类数组参数写"支持 N-M 维"（如 `aclnnCalculateMatmulWeightSize.tensorShape` 的"输入shape支持2-6维"），表达的是**数组长度区间**，必须写成 `2 <= len(tensorShape) <= 6`（裸参数名直接入 `len()`）；**禁止**写成 `2 <= len(tensorShape.shape) <= 6`（aclIntArray 无 `.shape`，运行期 `AttributeError`）或 `2 <= tensorShape.shape[0] <= 6`（第一元素值范围，语义错误）。`array_length.value=[2,6]`（`type="range"`），`dimensions.value=[]`（非 Tensor 类型按 §4.6.3 类型前置规则恒为空，**不**写 `[2,6]`）。

**隐式 >0 约束（大小/数量语义参数，v3 增补）**：当某标量取值参数的 description 含
"空间大小"/"数据量"/"元素个数"/"数量"等语义短语时，必须按 §4.6.9 追加
`P.range_value > 0` 条目（`expr_type=value_dependency`）。此约束来自参数语义而非
文档显式取值范围描述，`src_text` 摘录 description 原文并补注"大小/数量语义隐含 >0"。

<!-- §6.3 模式 5（NZ 块尺寸）已移至 modules/nz_matmul.md，按需加载。 -->
#### 模式 6：门控条件 Shape（`gate_conditional_shape.md`，v3 新增）

**适用场景**：某参数的 shape 由 enum/boolean 门控参数 Y 的取值决定（典型：
MatMul 类算子的 `transposeX2` 门控 x2 的 shape、轴变换算子的 `axis` 门控 output 的
shape、MoE 类算子的 `group` 门控 token 排布等）。文档常常先写默认 shape，再写
门控后的 shape，例如：

> "x2 的 shape 为 (H\*rankSize, N)；配置为 True 时右矩阵 Shape 为 (N, rankSize\*H)"

**推荐写法（if/elif/else 链）**：

```text
# 二选一（单 bool 门控）
({target}.shape == [{shape_default}]) if ({gate}.range_value == {default_value})
else ({target}.shape == [{shape_gated}]) if ({gate}.range_value == {gated_value})
else True

# 真实例子（aclnnAlltoAllMatmul）：x2 shape 由 transposeX2 门控
# - transposeX2=False: x2.shape == [H*rankSize, N]
# - transposeX2=True:  x2.shape == [N, H*rankSize]
(x2.shape == [H.range_value * rankSize.range_value, N.range_value])
    if (transposeX2.range_value == False)
else (x2.shape == [N.range_value, H.range_value * rankSize.range_value])
    if (transposeX2.range_value == True)
else True
```

**等价写法（`unless` 多分支）**：

```text
# 每条分支用 not(or)/or 包成"前提不满足则跳过"
not({gate}.range_value == {default_value}) or ({target}.shape == [{shape_default}])
not({gate}.range_value == {gated_value}) or ({target}.shape == [{shape_gated}])
```

**`expr_type` 选择**：

- 优先 `shape_choice`（多个候选 shape 中选其一），便于下游按枚举遍历；
- 也可使用 `parameter_representation` 或 `shape_value_dependency`；
- 若门控参数取值范围文档未完全枚举，末尾必须保留 `else True` 兜底。

**反例（禁止）**：

- 写两条独立无条件 expr（如 `x2.shape == [H*rankSize, N]` 与 `x2.shape == [N, H*rankSize]`），
  **丢失门控上下文**，下游生成器会把它们当作两个独立候选而不区分 `transposeX2`。
- 把 `transposeX2.range_value == True` 写成 `transposeX2 == True`（**禁止裸参数名**，
  必须 `参数名.range_value`）。
- 把 shape 字面量写成 `"[H*rankSize, N]"` 字符串（**禁止**）。

**触发场景示例**：

| 触发信号 | 目标 shape 字段 | 门控参数 |
| -------- | ---------------- | -------- |
| "transposeX2 为 True 时 shape 为 (N, H*rankSize)" | `x2.shape` | `transposeX2` |
| "axis=1 时输出 shape 为 (BS, N, H)" | `output.shape` | `axis` |
| "group=tp 时 x shape 为 (BS/rankSize, H)" | `x.shape` | `group` |
| "squeeze 为 True 时输出 shape 去除 axis 维" | `output.shape` | `squeeze` |

<!-- §6.3 模式 6.1 / 7 / 9 已移至 modules/{nz_matmul,backward_partial,format_cast}.md，按需加载。 -->
## 7. `expr_type` 取值字典

> `InterParamConstraint.expr_type` 类型为**自由 `str`**（不受 Pydantic 枚举约束）。
> 下表列出**已知的常用取值**作为**参考指引**；若语义无法匹配，允许使用文档实际语义值。

### 7.1 参数间约束（2+ 参数，来自 `InterConstraintsRuleType` 枚举）

| `expr_type` | 适用场景 | 典型 `expr` |
| --- | --- | --- |
| `shape_broadcast` | 形状需满足广播关系 | `all(a.shape[i] == b.shape[i] or a.shape[i]==1 or b.shape[i]==1 for i in range(N))` |
| `shape_choice` | 形状在多个候选中选其一（含 v3 新增的门控条件 Shape） | `bias.shape == gamma.shape or bias.shape == x.shape` |
| `shape_equality` | 形状完全相等 | `out.shape == x.shape` |
| `shape_dependency` | 输出 shape 由输入 + 辅助参数推导 | `out.shape[0] == pad + x.shape[0]` |
| `shape_value_dependency` | shape 中具体轴值/元素值依赖 | `x1.shape[0] == x2.shape[1] and x2.shape[1] == BS.range_value` |
| `type_equality` | dtype 必须一致 | `x1.dtype == x2.dtype` |
| `type_dependency` | dtype 依赖其他参数/条件（含互推导关系） | `(bias.dtype == "FLOAT16") if (x.dtype == "FLOAT16") else (bias.dtype == "FLOAT32")`；互推导用合法 dtype 组合析取表达 |
| `value_dependency` | 取值依赖/取值范围 | `BS.range_value % rankSize.range_value == 0` |
| `format_equality` | 数据格式必须一致 | `x1.format == x2.format` |
| `presence_dependency` | 共存规则（None/非None） | `(scale is None) == (zeroPoint is None)` |
| `derived_value` | 派生输出取值由子接口确定映射推导（须可求解，见 §4.6.8 C.1、§6.3 模式 9） | `actualFormat.range_value == dstFormat.range_value`（恒等）；查找表用析取 |

### 7.2 单参数约束（扩展值，不在 `InterConstraintsRuleType` 枚举中但实际广泛使用）

| `expr_type` | 适用场景 | 典型 `expr` |
| --- | --- | --- |
| `cross_param_constraint` | 通用跨参数约束（语义较泛） | 按具体上下文 |
| `parameter_representation` | 隐式维度变量/外部常量与张量 shape 的绑定 | `x1.shape[0] == BS.range_value` 或 `rankSize.range_value in [2,4,8]` |
| `self_value_range` | 单参数取值范围（区间） | `0 <= actType.range_value <= 5` |
| `self_value_enum` | 单参数取值枚举 | `activation.range_value in ["relu", "gelu", "silu"]` |
| `self_value_dependency` | 单参数取值 ≈ 固定布尔/唯一合法值 | `transposeX1.range_value == False` |
| `self_string_length` | 字符串参数长度约束 | `0 < len(group.range_value) < 128` |
| `self_shape_dim_range` | 单参数维度（rank）/ 数组长度范围 | `2 <= len(x.shape) <= 3`（`aclTensor`/`aclTensorList`）；`2 <= len(arr) <= 6`（`aclIntArray` 等无 `.shape` 数组，裸参数名） |
| `self_shape_axis_value` | 单参数某轴值约束 | `x.shape[0] >= 1` |

---

## 8. 边缘场景处理

| 场景 | 处理方式 |
| ---- | -------- |
| 文档仅给"产品支持"无 dtype 组合表 | `dtype_support_description={}` |
| 文档仅给"产品支持"无 format 组合表 | `format_support_description={}` |
| 多平台 dtype 列表完全一致 | 各平台各自复制相同列表；不用"common"合并 |
| 参数是 `aclIntArray *xxx` | `type.value="aclIntArray"`，`array_length` 必填实值 |
| 参数是 `aclDataType xxx`（标量数据类型枚举） | `type.value="aclDataType"`、`dtype.value=["string"]`（固定，见 §4.6.3 aclDataType 规则）；文档"数据类型"列候选写入 `allowed_range_value`（`type="enum"`），**不**写入 `dtype` |
| 文档出现 `Optional` 后缀但未说明是否可空 | `is_optional.value=false`（保守），`src_text` 摘录原文待人工复核 |
| 文档写"shape 为 [B,H] 或 [B,1,H]" | 拆为 `shape_choice` / `shape_dependency` 约束；不要并成模糊规则 |
| 文档写"x 和 y 数据类型必须一致" | `expr_type="type_equality"`，`expr="x.dtype == y.dtype"`，`relation_params=["x","y"]` |
| 文档引用 `互推导关系.md` 或写"数据类型推导规则" | 按 §4.6.10 A 的推导表生成 `type_dependency`；输出若要求与推导后 dtype 一致，必须绑定输出 dtype；推导结果不在输出 dtype 允许集合内的输入组合必须排除 |
| 文档引用 `broadcast关系.md` 或写"满足 broadcast 关系" | 按 §4.6.10 B 的广播规则生成 `shape_broadcast`；若输出轴由 broadcast 推导得到，还要生成输出轴等于 broadcast 结果的 `shape_value_dependency` |
| MatMul 文档写"Reduce 维度需要相等" | 生成 `shape_value_dependency` 绑定真实 Reduce 轴；若存在转置/非转置布局，必须按对应 bool 门控分支；不得只写 `ceil(k,k0)=k1` 而允许 NPU 逻辑 Reduce 维度不等 |
| 文档写"仅 Atlas A3 支持 BF16" | 在对应平台的 `dtype.value` 中体现差异，`src_text` 摘录原文 |
| 文档给出"确定性计算：默认确定性" | `deterministic_computing["平台"].value = "true"`，`src_text` 摘录该句 |
| 文档给出"确定性计算：默认非确定性" | `deterministic_computing["平台"].value = "false"`，`src_text` 摘录该句 |
| 文档**完全没有** `返回码` 章节 | `return_info=[]` |
| `allowed_range_value` 只有单边界或开区间 | `allowed_range_value.value=[]`；在 `constraints_in_parameters` 中用 `value_dependency` 不等式表达，禁止为 `type=range` 写 `null` 端点 |
| **文档写 bool 参数（无固定值约束）** | `allowed_range_value.type="enum"`、`value=[false, true]`；强行 bool 枚举，不允许填 `[]` 配 `type="range"`（否则下游生成器按浮点填充，会产生 1.0/1.23e-40 等非法值） |
| 表达式无法用 Python 表达（自然语言公式） | **不**产出 `constraints_in_parameters` 条目（空 `expr` 违 §4.7.2）；把语义记入相关参数 `description`/`src_text` 摘录原文，待人工校对 |
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
| **文档写"X 的 shape 为 (A, B)；当 Y 配置为 True 时 shape 为 (C, D)"（v3 新增）** | **不可**拆为两条独立无条件 shape 描述；必须在 `constraints_in_parameters` 中为 X 产出**单一条件 shape 约束**（§6.3 模式 6），用 `Y.range_value` 等门控参数分支；`expr_type` 优先 `shape_choice` 或 `parameter_representation`；`src_text` 同时摘录默认 shape 短语与"配置为 X 时…为…"短语，确保门控可溯源（典型反例：aclnnAlltoAllMatmul 中 x2.shape 在 transposeX2=True 时应为 (N, H*rankSize) 而非无条件 (H*rankSize, N)） |
| **`shape_value_dependency` 写成无条件形式（含 `mat2.shape[j]` / `self.shape[i]` 引用但未按 §4.6.5 B.1 隐式 bool 门控）** | 改写为 §6.3 模式 6.1 单条 if/else 或 unless 多分支；`relation_params` 包含对应隐式 bool；`src_text` 同时摘录"非转置 NZ (b, n1, k1, k0, n0)" 与 "转置 NZ (b, k1, n1, n0, k0)" 原文 |
| **一段式算子：函数原型无 `GetWorkspaceSize`（v3 新增）** | `function_signature` 取唯一函数声明；参数列表无 `workspaceSize`/`executor`。不得伪造 `GetWorkspaceSize` 段；**不得**写入 `is_single_function_mode` 字段 |
| **一段式算子：输出为标量指针（`uint64_t*`/`int64_t*` 等）（v3 新增）** | 该参数**进 `outputs`**（`type.value` 去 `*`、`format="N/A"`、`dimensions=[]`、`is_operator_param=true`），**不**当流程参数排除；`aclnnCalculateMatmulWeightSize` 的 `weightTensorSize` 即此 |
| **aclIntArray 参数的 dtype 固定为 int** | `type.value="aclIntArray"` → `dtype.value=["int"]`（固定，见 §4.6.3 aclIntArray 规则）；文档"数据类型"列若列张量 dtype（如 `FLOAT16`/`BFLOAT16`）描述的是关联张量，**不**写入 `dtype`（不得写成 `dtype.value=["FLOAT16","BFLOAT16"]`） |
| **aclIntArray / aclFloatArray / aclBoolArray 的 expr 禁用 `.shape`** | 这类非 Tensor 数组无 `.shape` 属性；长度约束写 `len(paramName)`（如 `2 <= len(tensorShape) <= 6`、`len(tensorShape) >= 1`），**禁止** `len(paramName.shape)` / `paramName.shape[i]`（运行期 `AttributeError`）；`.shape` 仅 `aclTensor`/`aclTensorList` 可用 |
| **aclTensorList 参数 P 写“长度与 Q 相同”** | P 为 Optional 时生成 `(P is None) or (len(P) == len(Q))`，否则生成 `len(P) == len(Q)`；禁止 `.array_length` 和 `len(P.shape)`；相同文案出现在多个参数行时逐参数生成，不能去重 |
| **backward / grad 文档写“gradOutput 与 self/input 维度一致”，同时末尾轴由 padding 等参数派生** | 按 §4.6.6 / §6.3 模式 7 拆分：①前缀切片相等；②rank 相等；③文档明确的派生轴公式。禁止只提取末维公式，也禁止用 `gradInput.shape == self.shape` 替代 gradOutput 跟随关系 |
| **文档写参数描述含“空间大小/数据量/元素个数/数量”等大小/数量语义短语（v3 增补）** | 按 §4.6.9 处理：在 `constraints_in_parameters` 中追加 `P.range_value > 0`（`expr_type=value_dependency`），`allowed_range_value.value=[]`；`src_text` 摘录 description 原文 + 补注“大小/数量语义隐含 >0”；不适用于 shape/dtype/format/枚举/bool 参数 |
| **文档按产品分节给出同一参数的不同候选值 / 固定占位值（v3 增补）** | 按 §4.6.11 处理：逐平台产出 `allowed_range_value`（`type="enum"`），各平台 `value` 取该产品分节/示例的实际候选；占位产品 `value` 为单元素列表（如 `[-1]`），不得追加总表候选、不得留空 `[]`；`src_text` 逐平台摘录该分节原文/示例代码；`type`/`dtype`/`format` 逐平台一致，仅 `allowed_range_value.value` 随产品分歧 |
| **派生输出参数标记 [DERIVED] 且文档存在确定映射（如 dtype/format 组合表 → actualFormat）（v3 增补）** | 按 §4.6.8 C.1 与 §6.3 模式 9 处理：产出 `derived_value` 条目，`expr` 编码映射为可 `eval()` 的布尔表达式（恒等映射用等式、查找表用析取）；`expr` 不得为空串；`relation_params` 含 `D` 及全部键参数。文档无确定映射时不产出该条目，由 `[DERIVED]` description 承载。典型反例：aclnnNpuFormatCast dstTensor.format/actualFormat 的 `derived_value` expr 留空，生成器随机赋值致 86/100 A3 用例不一致 |
| **格式转换算子文档 dtype 表每行 src.dtype == dst.dtype（v3 增补）** | 按 §4.6.12 处理：产出 `type_equality` 约束 `srcTensor.dtype == dstTensor.dtype`；dstTensor 值域沿用 src，不得按不同 dtype 负值域生成。典型反例：aclnnNpuFormatCast 300/300 条用例 src.dtype != dst.dtype（uint8→int8），dstTensor.range_values 按 int8 负值域 [-255,-1] 生成 |
| **文档组合表是 dtype×format 交叉联合表（同一行同时含 dtype 列与 format 列，且 dtype 与 format 存在行内依赖——不同 dtype 对应不同 format 候选，拆成纯 dtype 表+纯 format 表会丢失信息/产生非法组合；如 srcTensor.dtype×dstTensor.dtype×dstTensor.format 中 INT8→FRACTAL_NZ、INT32→FRACTAL_NZ_C0_16）（v3 增补）** | **不得**拆进 `dtype_support_description`/`format_support_description`（拆解会丢失行内 dtype↔format 对应，并产生数值枚举码与名字混用）；按 §6.3 模式 9「主接口联合组合表」落库为**一条** `derived_value`（或 `cross_param_constraint`）OR-of-ANDs expr，析取所有合法行；`dtype_support_description`/`format_support_description` 对该算子留 `{}`。典型反例：aclnnNpuFormatCast iter_001 把联合表拆成 `dtype_support_description`（`additionalDtype` 抄成 `"2"` 而非 INT8、`dstFormat`/`actualFormat` 抄数值枚举码）与 `format_support_description`（`srcTensor=ND × dstFormat` 笛卡尔积、`actualFormat=dstFormat` 凭空捏造 25 行），`derived_value.expr` 留空 |
| **文档组合表只有 dtype 列（纯 dtype 表，跨多参数也行），或只有 format 列（纯 format 表）（v3 增补）** | 纯 dtype 表填 `dtype_support_description`、纯 format 表填 `format_support_description`，**不**落 OR-of-ANDs expr；不属 §6.3 模式 9 适用范围 |
| **文档组合表同表含 dtype 列与 format 列但二者独立（任意 dtype 配任意 format，拆开不丢失信息）（v3 增补）** | 按"单独 dtype 约束 + 单独 format 约束"处理：dtype 部分填 `dtype_support_description`、format 部分填 `format_support_description`（或用 `type_equality` + format 枚举 + `format_rank_consistency`）；**不**强制 OR-of-ANDs。判据：拆成纯 dtype 表+纯 format 表后是否产生原本非法的 dtype×format 组合——不产生即为独立 |
| **`constraints_in_parameters` 出现 `expr=""` 空壳条目（`derived_value`/`cross_param_constraint` 等）（v3 增补）** | 违 §4.7.2/§4.6.8 C.1：`derived_value` 在文档存在确定映射时 `expr` 必须编码为可求解 OR-of-ANDs/等式 expr（§6.3 模式 9），不得为空；不可形式化的约束（如「转 NZ 后不许 contiguous/transpose」）**不**产出条目，改记入 `description`/`src_text`。典型反例：aclnnNpuFormatCast iter_001 三平台 `derived_value.expr=""` 与 `cross_param_constraint.expr=""` 空壳 |

## 9. 自检清单（提取完成后必跑）

> 模型在生成 JSON 之后、提交给用户之前，**内部自检** 30 项。任何一项不通过均需重做。

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
    唯一顺序特例：`aclnnBatchMatMulWeightNz` 的 `self_transposed` 与
    `mat2_transposed` 必须使用 `[true, false]`，见 §4.6.5 B.1。
15. **NZ 块尺寸硬约束（v2 新增）**：若存在 5D NZ 张量（`format ∈ {"NZ","FRACTAL_NZ","FRACTAL_NZ_C0_16"}` 且 `dimensions.value=[5,5]`），
    必须满足**全部**下列子项：
    a. `mat2.allowed_range_value.value` 包含 `[[16,16],[16,16]]` 或文档明示的其他端点（`type=range`）；
    b. `constraints_in_parameters[每个支持平台]` 含 `mat2.shape[3] == 16` 与 `mat2.shape[4] == 16` 两条 `shape_equality`（或 `shape_value_dependency`）；
    c. 文档同时描述非转置与转置 NZ 两种布局时，两套 `shape[3]/shape[4]==16` 须**分别落库**为不同条目（共 4 条），`src_text` 摘录对应原文；
    d. `src_text` 非空，且包含 `k0` / `n0` / `16` 等关键词。
16. **一段式算子一致性（v3 新增）**：若 `function_signature` **不含** `GetWorkspaceSize`（一段式），必须满足**全部**：
    a. 函数名与 `operator_name` 一致（无 `GetWorkspaceSize` 后缀）；
    b. 标量指针输出（如 `uint64_t*`/`int64_t*`）在 `outputs` 中，`type.value` 去 `*`、`format.value="N/A"`、`dimensions.value=[]`、`is_operator_param.value=true`；
    c. 不得出现 `workspaceSize`/`executor`/`stream` 被误标为 `outputs` 流程参数。
    两段式算子的 `function_signature` 应含 `GetWorkspaceSize`。**不得**在 JSON 中写入 `is_single_function_mode` 字段——一段式判定由 `function_signature` 隐式表达，写入该字段会触发校验阻断。
17. **非 Tensor 数组类型禁用 `.shape`**：对所有 `type.value` 为 `aclIntArray` / `aclFloatArray` / `aclBoolArray` 的参数，`constraints_in_parameters` 的 `expr` 中**禁止**出现 `paramName.shape`（这些类型无 `.shape` 属性，执行/校验期对一维数组实例求值会触发 `AttributeError`）；其长度用 `len(paramName)` 表达（如 `2 <= len(tensorShape) <= 6`、`len(tensorShape) >= 1`）。仅 `aclTensor` / `aclTensorList` 允许 `.shape` / `.dtype` / `.format` 属性引用。
18. **条件 Shape 约束自检（v3 新增）**：遍历所有 `inputs` 中 `type.value == "bool"`
    或 `allowed_range_value.type == "enum"`（且 value 至少 2 项）的参数作为**候选门控
    参数 G**；同时重新扫描原文及参数 `description` 中“配置为 True/False/某值时
    Shape 为……”等信号，不得只扫描已经引用 G 的 expr（否则无法发现 G 被完全漏抽）：
    a. 原文或 `description` 出现条件 Shape 信号时，必须存在同时包含
       `G.range_value` 与目标 `X.shape` 的 expr，且 `relation_params` 同时包含 G 与 X；
    b. 形如 `G.range_value == "{v}"` 出现在某 expr 中时，该 expr 必然是一个条件分支
       （即 expr 形如 `(... if G.range_value == "{v}" else ...)` 或 `not(G.range_value == "{v}") or (...)`）；
    c. 若发现某参数 X 的 shape expr 引用了 G 的某取值，但 expr 中**没有**出现 G 的
       等式分支，则视为“条件 shape 被错误地无条件化”，必须重写为模式 6 形式；
    d. 默认 shape 与门控后 shape **必须合并为单一 expr 条目**，不允许两条独立
       `shape_equality` / `shape_choice` 条目同时存在且互不引用 G；
    e. `src_text` 必须同时摘录默认 shape 短语与“配置为 X 时…为…”短语（或同义信号词），
       确保门控可溯源。
    f. **shape_value_dependency 门控完整性**：遍历所有
       `expr_type == "shape_value_dependency"` 的约束；若 `expr` 含 `mat2.shape[j]`
       引用，且 `operator_name == "aclnnBatchMatMulWeightNz"`，必须同时引用
       `mat2_transposed.range_value`；若 `expr` 含 `self.shape[i]` 引用，必须同时引用
       `self_transposed.range_value`。表达式必须以 if/elif/else 链（§6.3 模式 6）或
       `not(...)/or` 等价形式（§6.3 模式 6.1）呈现，禁止无条件 expr；`relation_params`
       必须包含对应隐式 bool 与张量。漏掉任一项视为漏抽，违反 §4.6.3 D+。
19. **TensorList 长度关系完整性**：遍历所有 `type.value="aclTensorList"` 参数；
    a. `array_length.src_text` 明确写“长度与 Q 相同”时，必须存在
       `len(P) == len(Q)` 约束；
    b. P 为 Optional 时表达式必须带 `(P is None) or ...` 守卫；
    c. 禁止在 expr 中出现 `.array_length`，也禁止用 `len(P.shape)` 表示列表长度；
    d. 相同文案出现在多个参数行时逐行核对，禁止只为第一个参数生成约束；
    e. 含“一般情况下/通常情况下”的描述必须结合综合约束补全条件，不能直接无条件化。
20. **动态取值边界分层**：若 `allowed_range_value.src_text` 引用了其他参数，并描述
    小于/大于/等于/相同/依赖等关系：
    a. `allowed_range_value.value` 必须为 `[]`，不得枚举模型猜测的样例值；
    b. 完整关系必须进入每个平台的 `constraints_in_parameters`，并在
       `relation_params` 中包含双方参数；
    c. 必选参数且原文没有空值语义时禁止包含 `null`；
    d. 例如“padding 两个数值均小于 self 最后一维”应写
       `padding.range_value[0] < self.shape[-1] and padding.range_value[1] < self.shape[-1]`；
    e. 原文未说明 padding 非负时，不得擅自增加 `0 <= padding.range_value[i]`。
21. **Forward-Output Partial-Shape 完整性**：若 backward / grad 文档明确说明
    `gradOutput` / `dout` 与 `self` / `input` 维度一致，同时又给出末尾派生轴公式：
    a. 必须存在文档可证的前缀跟随表达式；仅最后一维为派生轴时写
       `gradOutput.shape[:-1] == self.shape[:-1]`，不得仅按 1d/2d/3d 名称猜测切片；
    b. 必须存在 `len(gradOutput.shape) == len(self.shape)`；
    c. 文档明确给出的每个派生轴公式必须分别落库；
    d. `relation_params` 与切片/公式实际引用参数一致；
    e. 不得用 `gradInput.shape == self.shape` 替代 a/b；
    f. `src_text` 必须能回溯到“维度一致”和派生公式原文。
22. **array_length 结构完整性**：遍历全部输入和输出参数：
    a. `array_length` 必须为对象，且 `array_length.value` 不得为 `null`；
    b. 标量、不适用或文档未给出长度约束时，`value` 必须为 `[]`；
    c. 单个闭区间使用 `[min,max]`，多个“或”关系闭区间使用
       `[[min1,max1],[min2,max2],...]`；
    d. 对照 `src_text` 逐个核验区间数量和端点，禁止把多个可选区间合并为其包络区间。
23. **`aclnnBatchMatMulWeightNz` 转置隐式变量完整性**：当
    `operator_name == "aclnnBatchMatMulWeightNz"` 时，必须满足**全部**：
    a. `inputs` 同时包含 `self_transposed` 和 `mat2_transposed`，且两个变量均覆盖
       `product_support` 的全部平台；
    b. 两者均为 `type.value="bool"`、`dtype.value=["bool"]`、
       `is_operator_param.value=false`、`dimensions.value=[]`；
    c. 两者的 `allowed_range_value` 均严格为
       `{"value":[true,false], "type":"enum"}`，不得使用默认顺序 `[false,true]`；
    d. 两者均不出现在 `function_signature`；
    e. 文档中的转置/非转置 shape 或 NZ 布局约束分别由对应隐式变量门控，
       `relation_params` 包含实际张量与隐式变量，不存在两套互相冲突的无条件布局约束。
24. **format↔rank 完整性**：逐平台遍历所有 `aclTensor` / `aclTensorList` 参数；若
    `format.value` 是包含不同标准 rank 格式的列表，必须满足**全部**：
    a. 存在且仅存在一条引用该参数的 `format_rank_consistency` 约束；
    b. `format.value` 中除 `ND` 外的每种已知格式，都在该约束中有对应的精确
       `len(T.shape) == rank` 分支；`ND` 使用文档给出的 rank 区间；
    c. `relation_params` 包含该参数，表达式同时引用 `T.format` 与 `T.shape`；
    d. 对表达式逐分支做反例检查：`NCDHW + 非5D`、`NDC1HWC0 + 非6D`、
       `FRACTAL_Z_3D + 非4D`、`NZ/FRACTAL_NZ + 非5D` 必须全部求值为 false；
    e. 对 `aclnnNpuFormatCast`，`srcTensor` 与 `dstTensor` 必须逐平台分别通过上述检查，
       禁止只为其中一个张量生成守卫。
25. **大小/数量语义参数的隐式 >0 约束（v3 增补）**：遍历全部输入和输出参数的
    `description`，凡含"空间大小"/"占用空间大小"/"数据量"/"元素个数"/"的数量"/
    "个数"等表示"大小/数量/个数"语义短语的**标量取值参数**（非 shape/dtype/
    format/枚举/bool），必须满足**全部**：
    a. `constraints_in_parameters[每个支持平台]` 中存在 `P.range_value > 0` 条目
       （`expr_type=value_dependency` 或 `self_value_range`）；
    b. `allowed_range_value.value` 为 `[]`（未伪造 0 下界）；
    c. `relation_params` 仅含该参数自身；
    d. `src_text` 摘录 description 中"空间大小/数据量/个数"原文字句并补注
       "大小/数量语义隐含 >0"；
    e. 若文档已显式写明该参数取值范围并已落库对应约束，则不重复追加（见 §4.6.9 C.5）。
26. **公共互推导 / broadcast 知识展开自检（v3 增补）**：重新扫描原文和参数
    `description`：
    a. 若出现 `互推导关系.md`、"数据类型推导规则"、"推导之后的数据类型保持一致"，
       必须存在引用相关输入和输出的 `type_dependency` 约束；
    b. 若输出 dtype 需要等于推导结果，`type_dependency` 必须排除推导结果不在输出
       `dtype.value` 中的输入组合，不能只保留各参数独立 dtype 枚举；
    c. 若出现 `broadcast关系.md` 或 "满足 broadcast 关系"，必须存在对应
       `shape_broadcast` 约束；
    d. 若输出轴写明 "经过 broadcast 推导后一致"，必须存在输出轴等于 broadcast 结果的
       `shape_value_dependency`；
    e. 若出现 "Reduce 维度需要与 ... 相等"，必须存在真实 Reduce 轴相等约束；对于
       `aclnnBatchMatMulWeightNz`，该约束必须按 `self_transposed` / `mat2_transposed`
       分支并引用两者。
27. **产品相关参数取值范围差异自检（v3 增补）**：逐平台遍历全部 `inputs`/`outputs`
    中 `is_operator_param.value=true` 的非张量标量/枚举参数 `P`；若文档"约束说明"
    按产品分节或调用示例表明 `P` 的候选值随产品分歧（某产品固定为占位值如 `-1`，
    另一产品为总表候选子集/全集），必须满足**全部**：
    a. `P` 在 `product_support` 每个平台都有 `allowed_range_value` 条目（与 §9
       第 3 项逐平台覆盖要求一致）；
    b. 各平台 `allowed_range_value.value` 反映**该产品**实际候选，而非统一套用
       总表候选；占位产品为单元素列表（如 `[-1]`），不得为 `[]`；
    c. 各平台 `allowed_range_value.src_text` 摘录该产品分节原文或示例代码行，
       不得只抄总表"参数说明"行；
    d. 对 `aclnnNpuFormatCast`，`additionalDtype` 在
       `Atlas A3 训练系列产品/Atlas A3 推理系列产品` 与
       `Atlas A2 训练系列产品/Atlas A2 推理系列产品` 必须为
       `allowed_range_value.value=[-1]`，`Atlas 350 加速卡` 为 `[1,27,2,36]`；
       不得三平台统一为 `[1,27,2,36]`，也不得留空 `[]`。
28. **空 `expr` 禁令与 `derived_value` 可求解性（v3 增补）**：遍历
    `constraints_in_parameters` 各平台全部条目，必须满足**全部**：
    a. 每条 `expr` 为**非空**字符串，规范化后是可 `eval()` 的合法 Python 布尔
       表达式（违 §4.7.2、§6.1）；
    b. **不得**出现 `expr=""` 的空壳条目；`expr_type="derived_value"` 条目**允许**
       存在，但其 `expr` **必须**是可求解的查找/派生表达式（§4.6.8 C.1），不得为空；
       若文档无确定映射，则不应产出 `derived_value` 条目（§4.6.8 C.2），派生语义由
       `[DERIVED]` description 承载；
    c. 文档约束无法形式化为 Python 布尔表达式（自然语言公式、broadcast 特殊
       dtype 不可靠形式化等）时，**不**产出 `constraints_in_parameters` 条目，
       改把语义记入相关参数 `description`/`src_text`（§4.6.10 B.4、§8、§6.1 第 10 条）；
    d. 对 `aclnnNpuFormatCast`，当 `dtype_support_description` 含 actualFormat 确定映射时，
       `constraints_in_parameters` 中**必须**含可求解 `derived_value` 条目（如 A3/A2
       `actualFormat.range_value == dstFormat.range_value`），**不得**为 `expr=""` 空壳；
       dstTensor.format 亦须由映射派生（不得独立随机赋值）。
29. **格式转换算子 dtype 等式自检（v3 增补）**：当算子 `function_explanation` 或正文
    含"格式转换"/"数据值不变"/"纯格式转换"/"data values are preserved"等语义，且
    文档 dtype 表（GetWorkspaceSize 表或 `dtype_support_description`）每行 src.dtype ==
    dst.dtype 时，必须满足**全部**：
    a. `constraints_in_parameters[每个支持平台]` 中存在 `srcTensor.dtype == dstTensor.dtype`
       的 `type_equality` 约束（`relation_params=["srcTensor","dstTensor"]`）；
    b. dstTensor 的值域生成不得按与 src 不同的 dtype 负值域（如 src=uint8 而 dst
       按 int8 的 [-255,-1] 生成）；
    c. `src_text` 摘录文档 dtype 表行或功能说明"数据值不变"原文；
    d. 对 `aclnnNpuFormatCast`，三平台均须有 `srcTensor.dtype == dstTensor.dtype`
       等式约束，不得遗漏。
30. **dtype×format 交叉联合组合表自检（v3 增补）**：当文档组合表（GetWorkspaceSize
    主接口表或 `<details>` 分节 combo 表）是**交叉联合表**——**同一行同时含 dtype 列
    与 format 列，且 dtype 与 format 存在行内依赖**（不同 dtype 对应不同 format 候选；
    判据：拆成纯 dtype 表+纯 format 表会丢失信息/产生原本非法的 dtype×format 组合；
    如 `srcTensor.dtype × dstTensor.dtype × dstTensor.format` 中 INT8→FRACTAL_NZ、
    INT32→FRACTAL_NZ_C0_16），行间互斥——必须满足**全部**。**纯 dtype 表**（只有 dtype
    列）填 `dtype_support_description`、**纯 format 表**（只有 format 列）填
    `format_support_description`、**同表但独立**（任意 dtype 配任意 format）按"单独
    dtype + 单独 format"拆开——三者均**不**属本项：
    a. **不得**把交叉表拆进 `dtype_support_description` / `format_support_description`
       （拆解会丢失行内 dtype↔format 对应，并产生数值枚举码与 dtype/format 名字混用）；
       这两个字段对该算子留 `{}`（纯 dtype 表/纯 format 表/独立表不受此限，仍按字段本义填写）；
    b. `constraints_in_parameters[每个支持平台]` 中**必须**存在**一条** `derived_value`
       （或 `cross_param_constraint`）expr，析取表中所有合法行、每行合取键值与目标值
       （§6.3 模式 9「主接口联合组合表」）；`expr` 不得为空（违 §9.28）；
    c. 析取**必须覆盖全部合法行**：遗漏一行会使该组合下 dst 取值无约束、生成器随机
       赋值；多值映射（如某 dtype 对应两种 format）在该行用 `or` 表达；
    d. dtype 引用用 §5.2 受控字典名（`FLOAT`→`FLOAT32`）、format 引用用 §5.3 受控
       字典短名（`ACL_FORMAT_FRACTAL_NZ(29)`→`"FRACTAL_NZ"`）；**禁止**抄括号里的
       数值枚举码作为 dtype/format 值；
    e. `relation_params` 包含 expr 中全部被引用参数（如 `["srcTensor","dstTensor"]`）；
       `src_text` 摘录该平台 combo 表原文；
    f. 对 `aclnnNpuFormatCast`：**Atlas 350** 的 GetWorkspaceSize 表 dtype 决定 format
       （INT8→FRACTAL_NZ、INT32→FRACTAL_NZ_C0_16、FLOAT→C0_16/C0_32…），属交叉表，**必须**
       落库该联合 OR-of-ANDs expr；**A3/A2** 的 GetWorkspaceSize 表 dtype 与 format 独立
       （7 dtype 各可配 5 format、拆开不丢失信息），**不**属交叉表，用 `type_equality`
       （`srcTensor.dtype == dstTensor.dtype`）+ `dstTensor.format` 枚举 + `format_rank_consistency`
       表达即可，不强求 OR-of-ANDs；三平台均不得出现 `additionalDtype="2"`/`dstFormat="29"`
       这类数值枚举码混入 dtype/format 字段。

## 10. 调用模板

下面给出一份**可直接复制**的 prompt 调用片段：

```text
# System
你是一名昇腾 CANN 算子约束抽取专家。
请严格遵循《算子约束提取通用提示词 v3》的所有规则，并参考知识库：
- 解析 shape/dimensions 时参考 §4.6.3 dimensions 解析表
- 识别隐式维度变量时参考 §4.6.4（概念词/操作名/类型词需剔除）
- 处理 NZ / FRACTAL_NZ 张量时参考 §4.6.5（块尺寸硬约束、转置/非转置布局区分）
- 处理多格式 Tensor 时参考 §4.6.7 与 §6.3 模式 8；必须生成逐格式
  `format_rank_consistency` 守卫，尤其禁止 `NCDHW + 非5D`
- 识别条件 Shape（被 enum/boolean 门控的 shape）时参考 §4.6.3 G 与 §6.3 模式 6
- 对含 `self_transposed` / `mat2_transposed` 隐式 bool 的 NZ 算子，`shape_value_dependency`
  必须参考 §4.6.3 D+ 与 §6.3 模式 6.1 按隐式 bool 门控
- 处理 aclTensorList 容器长度关系时参考 §4.6.3 TensorList 长度规则与 §6.3 模式 0
- 处理 backward / grad 的 gradOutput partial-shape 跟随时参考 §4.6.6 与 §6.3 模式 7
- 处理大小/数量语义参数的隐式 >0 约束时参考 §4.6.9
- 处理派生输出张量（CalculateSizeAndFormat 类子接口）时参考 §4.6.8；当文档存在
  确定映射时 `derived_value.expr` 必须编码为可求解表达式（§6.3 模式 9），不得为空串
- 处理格式转换算子时参考 §4.6.12；当 dtype 表每行 src.dtype == dst.dtype 时必须
  产出 `type_equality` 等式约束
- 文档引用 `互推导关系.md` 或 `broadcast关系.md` 时参考 §4.6.10（推导表与广播规则
  已内联于该节）
- 写 expr 表达式时参考 §6.3 模式库（按关系特征匹配模板；NZ 块尺寸使用模式 5；
  条件 Shape 使用模式 6；shape_value_dependency 隐式 bool 门控使用模式 6.1；
  Partial-Shape 使用模式 7；TensorList 长度相等使用模式 0；派生值查找使用模式 9）
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
2. 按《算子约束提取通用提示词 v3》第 3 章 schema 输出 JSON；
3. 内部执行第 9 章 30 项自检（含 §9.15 NZ 块尺寸、§9.16 一段式一致性自检、§9.17 非 Tensor 数组禁用、§9.18 条件 Shape 与 shape_value_dependency 门控完整性、
   §9.19 TensorList 长度关系、§9.20 动态取值边界、§9.21 Partial-Shape 自检、§9.25 大小/数量语义隐式 >0、§9.26 公共互推导/broadcast 知识展开、
   §9.28 derived_value 可求解性、§9.29 格式转换 dtype 等式、§9.30 联合交叉 dtype/format 组合表）；
4. **仅返回 JSON 字符串**，不要包含任何解释、代码块标记或额外文字。
```

---

> **附录迁移说明**：历史变更记录（原附录 B）已移至 `prompts/CHANGELOG.md`；10 个典型算子对齐示例（原附录 A）已移至 `prompts/examples.md`。两份文件**不参与约束提取**，仅作维护参考，本提示词加载时不含其内容。

## 附录：知识库路径速查表

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
| [`prompts/modules/broadcast.md §A`](prompts/modules/broadcast.md) | CANN aclTensor dtype 互推导关系、推导结果与输出 dtype 绑定、非法组合排除（原 `knowledge/common/type_promotion.md`，已内联） | §4.6.10 A + §9.26 |
| [`prompts/modules/broadcast.md §B`](prompts/modules/broadcast.md) | broadcast 右对齐、维度为 1 拉伸、输出 broadcast 结果轴、特殊 dtype 限制（原 `knowledge/common/broadcast.md`，已内联） | §4.6.10 B + §9.26 |
