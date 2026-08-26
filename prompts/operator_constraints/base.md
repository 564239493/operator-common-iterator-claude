# 算子约束提取通用提示词（ACLNN）

> **用途**：从昇腾 CANN（Compute Architecture for Neural Networks）算子官方说明文档（Markdown / HTML）中，**人工 + LLM 协同** 提取结构化的算子约束信息，并以**纯 JSON** 形式输出，可直接喂给下游的测试用例生成引擎。
>
> **适用对象**：所有 `aclnn*` / `aclop*` 类算子（NN / Transformer / 通信 / 量化 / 格式转换等），尤其是《[Transformer 类算子清单](https://www.hiascend.com/document/detail/zh/canncommercial/900/API/aolapi/context/ops-transformer/op_api_list.md)》与《NN 类算子清单》中收录的算子。
>
> **设计目标**：
> 1. **机器可读** —— 输出严格遵循项目 `OperatorRule` 模型，可被 `pydantic.BaseModel.model_validate_json()` 直接解析；
> 2. **人类可读** —— 提示词本身有清晰的目录结构与可读注释，便于维护；
> 3. **可溯源** —— 关键字段保留 `src_text`，便于人工校对与回溯。
>
> **Schema 对齐**：输出对象结构见本文件 §3 输出对象合同，唯一事实源为
> `agent/generators/common_model_definition.py` 中的 `OperatorRule` / `ParamAttributes` /
> `ValueWithSrcText` / `InterParamConstraint`；提示词不复制 schema 文本，以三条校验命令
> 保证一致（见 §3）。

---

## 0. 使用与装配顺序

本文件是 ACLNN 基础提示词（**canonical 直接维护**），只保留稳定任务、字段提取
流程、边界处理和质量门禁。`prompts/history/operator_constraints_extract_v4.md` 为历史来源（provenance），
一次性机械拆分已完成，不再作为生成源。运行时按以下顺序，
步骤 1-5 由 `scripts/select_prompt.py` 在 run 初始化（PLAN）阶段完成并冻结为
`prompt_v1.md` + `prompt_preanalysis.json` + `prompt_assembly.json`（含 sha256），
extractor 只执行步骤 6（读冻结快照提取 JSON 并实际校验）：

1. 完整阅读当前算子文档并完成结构预分析（`route_aclnn_knowledge.preanalyze_document`）；
2. 加载 ACLNN 默认基础知识与通用知识（manifest `default_load` 模块）；
3. 依据当前文档信号加载特征知识，并按算子名精确加载单算子知识（`triggers` / `operator_name_eq`）；
   `source_analysis` 类知识还必须由运行配置显式开启，默认不加载；
4. 逐模块做适用性判断（含 `reject_on` 负向否决）。默认以当前文档为最高事实源；若冻结
   快照中含显式启用的 `source_analysis` 模块，则将其作为锁定源码版本的附加约束源，
   所有采用或冲突条目必须保留来源、commit 与可信度，不得静默覆盖；
5. 冻结 `base + applicable knowledge` 快照和组装记录后再提取（`select_prompt.assemble`）；
6. 生成 JSON，执行规范化与 `OperatorRule` 实际校验。

已迁出到知识库的小节，其引用均改写为「`knowledge/aclnn/<file>.md`
§<标题>」的文件+标题定位，不再依赖本文件内的章节编号。ACLNN 与 torch_npu 使用
完全独立的提示词和知识根，禁止跨 family 引用。
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

---

## 2. 输出规则（输出形式 + 5 条铁律，缺一不可）

输出为一段**纯 JSON 字符串**，结构与 §3 输出对象合同的 `OperatorRule` 完全一致，
**无任何多余内容**（不允许解释、前言、Markdown 代码块、注释、前后缀），须能被
`OperatorRule.model_validate_json()` 直接校验通过。在此基础上必须满足 5 条铁律：

1. **格式**：仅返回纯 JSON 字符串，**无任何** 解释、代码块、换行备注、前后缀；
2. **范围**：只输出**顶层类**的完整结构，自动嵌套填充所有内层类，**不单独**输出任何内层类；
3. **字段约束**：字段名、字段类型、层级结构必须与 §3 引用的项目模型 **完全一致**；禁止新增、缺失、修改字段（`extra="forbid"`）；
4. **类型匹配**：严格遵循类型注解（`str` / `int` / `bool` / `List` / `Dict` 等）；空值统一用 `null`（JSON 规范），不随意填充无效值；
5. **嵌套完整**：所有嵌套结构必须完整；若文档中无相关信息，**可选字段**填 schema 默认值，**必填字段**（`Field(...)`）填合理空值（`""` / `0` / `[]` / `{}`），**严禁留 `null` 占据必填位**。

---

## 3. 输出对象合同（唯一事实源）

不得在提示词中复制或重声明 Pydantic schema。输出必须严格使用
`agent/generators/common_model_definition.py` 中当前版本的完整
`OperatorRule` 对象结构，包括全部嵌套对象、必填字段和 `extra="forbid"` 规则。

生成 JSON 后必须实际执行：

```text
python scripts/validate_operator_rule.py <constraints.json>
python scripts/normalize_constraints.py <constraints.json>
python scripts/validate_artifacts.py constraints <constraints.json>
```

只有三条命令均成功，且 `OperatorRule` 校验不抛异常，结果才有效；不得仅凭模型
自检宣称结构正确。校验失败时必须修正 JSON 后重跑，不能跳过或降级。
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
- **一段式算子例外**：一段式算子没有 `workspaceSize`/`workspace`/`executor`/`stream`，其输出常为**标量指针**（如 `uint64_t *weightTensorSize`、`int64_t *xxx`）。标量指针输出**必须**进 `outputs`，**不得**因 `uint64_t*` 与 `workspaceSize` 同类型而误判为流程参数排除。其 `ParamAttributes`：`type.value` 去掉 `*`（如 `uint64_t`）、`format.value="N/A"`、`dimensions.value=[]`、`is_operator_param.value=true`、`dtype.value` 取文档"数据类型"列（空则按 type 回填，如 `["uint64_t"]`）、`is_support_discontinuous.value="N/A"`。

#### 4.6.2 二级 key（平台名）

- 二级 key 为**平台名**，取值集合：
  - 第 5.1 章列出的标准平台名；
  - **每个非隐式参数都必须为 `product_support` 中列出的每一个平台分别产出条目**。
    即使所有平台下 `ParamAttributes` 字段值完全一致，也必须**逐平台复制**相同的卡片，
    不得用单个平台名"代笔"，也不得遗漏任何平台；典型反例见 §6.3、§5。
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
| `dtype.value` | 是 | `List[str]` | 支持的 dtype 字符串（见 `knowledge/aclnn/common/platform_dtype.md` §dtype）；标量参数允许填写其自身类型字符串（如 `"bool"`、`"char"`、`"int"`）；不适用 → `[]` |
| `dtype.src_text` | 是 | `str` | 摘录原文 |
| `dimensions.value` | 是 | `List[int]` 或 `[]` | **合法 rank 的显式枚举**：列表中的每个整数都是一个真实可选 rank，不表示区间；如 `[1, 2, 3]` 表示支持 1～3 维，`[1, 3]` 表示只支持 1 维或 3 维；固定 3 维写 `[3]`；不适用 → `[]` |
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
  "当前仅支持输入 nullptr"/"仅支持传空指针"/"必须为空指针"，应提取为 `is_optional.value=false`
  （参数仍必填），并把取值约束的**主导表达**写入 `allowed_range_value`：
  `{"value": [null], "type": "enum", "src_text": "当前仅支持输入nullptr"}`——`null` 在此是
  "取值语义"（参数出现且值为 `nullptr`），不是"缺席语义"。`allowed_range_value.src_text`
  必须摘录含 `nullptr`/`空指针` 关键词的原文，以通过校验层 `_EXPLICIT_NULL_RE` 放行（见
  `scripts/validate_artifacts.py` 的 `_validate_dynamic_allowed_ranges`）。**不得**在
  `constraints_in_parameters` 中为该必选参数追加 `param is None` 条目：`param is None` 编码
  "缺席语义"，与本次确立的"null 取值语义"冲突，且生成器对必选参数强制 `is_present=True`，
  会把 `param is None` 归约为 `z3.Not(True)=False` 导致求解 UNSAT；必选 + 只支持 `nullptr`
  的取值语义由 `allowed_range_value={"value":[null], "type":"enum"}` 唯一承载。该参数仍是必填
  入参，只是必填值为 `nullptr`（见 §6.32 自检）。
- **`is_optional.src_text` 必须引用真实分类或显式说明**：非可选参数推荐摘录 `"输入"`、`"输出"`
  或包含该分类的表格行原文；可选参数摘录 `"可选输入"` / `"可不传（即为nullptr）"` 等明确原文。
  禁止 `src_text` 仅写 `"Optional"`，除非这是文档分类/正文中的独立显式说明，而不是参数名的一部分。
- **反例**：`aclnnSwinAttentionScoreQuant` 的 `biasQuantOptional`、`biasDequant1Optional`、
  `biasDequant2Optional`、`paddingMask1Optional`、`paddingMask2Optional` 在参数表"输入/输出"列均为
  "输入"，因此 `is_optional.value=false`；不得因名称后缀 `Optional` 置为 `true`。其中
  `paddingMask2Optional` 的"当前仅支持输入nullptr"**不得**作为可选性证据，也**不得**写成
  `constraints_in_parameters` 的 `param is None` 条目；必须提取为
  `allowed_range_value={"value": [null], "type": "enum", "src_text": "当前仅支持输入nullptr"}`，
  参数仍为必填（`is_optional.value=false`），只是必填值为 `nullptr`（见上方"支持/仅支持输入
  nullptr"规则与 §6.32 自检）。

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
- 文档明确参数"只支持传空指针""必须为空指针"或"仅支持空指针"时，`dtype.value` 保持 `[]`
  （参数本身无有效元素类型）；此时取值约束由 `allowed_range_value={"value": [null], "type":
  "enum"}` 承载，`src_text` 摘含 `nullptr`/`空指针` 的原文（见 §4.6「"支持/仅支持输入 nullptr"」
  规则与 §6.32 自检）；
- 回填仅补 `dtype.value`，不得伪造 `dtype.src_text`。
- 注：`aclIntArray` 的 dtype 不走"文档张量 dtype 列回填"——见下方「aclIntArray 参数的固定 dtype 规则」（`dtype.value` 固定 `["int"]`）。

**dtype / value 候选逐字一致性硬规则**：

- 每个约束条目 `value` 数组中的 dtype 字符串（以及 format 字符串、`allowed_range_value` 的字符串枚举候选、`dtype_support_description` / `format_support_description` 的 combo 值）必须**逐字复制**自文档原文 / 该条目 `src_text`，仅允许 `knowledge/aclnn/common/platform_dtype.md` §dtype / §format 明确登记的规范化映射（`BF16` / `bfloat16` / `bf16` → `BF16`；`float` / `Float` / `FLOAT` → `FLOAT32`）。**禁止**任何其他形式的缩写、漏字母、字母替换、大小写改写、词干截断或意译。
- **条目内自洽**：同一条目中，`src_text` 里出现的每个 dtype/format token 必须能在 `value` 数组中找到**逐字一致**的对应项，反之 `value` 数组每个元素也必须能在 `src_text` / 文档原文中找到逐字一致来源。若 `src_text` 记 `HIFLOAT8` 而 `value` 写 `HFLOAT8`（或反之），即为提取错误，必须修正为与文档原文逐字一致。
- **受控字典缺口处置**：当文档原文使用的 dtype token 不在 `knowledge/aclnn/common/platform_dtype.md` §dtype 受控字典中时，**禁止**将其改写为字典中"形似"的项（漏字母 / 截断 / 替换字母以凑出字典里已有的串，如把 `HIFLOAT8` 改成 `HFLOAT8`）；应**原样保留**文档 token、在 `src_text` 摘录原文，并在该参数 `description` 末尾补注 `[DICT_GAP:<token>]` 标记供人工扩容字典。此为字典缺口，不属誊写错误，不受 §6.4「dtype 必须来自 `knowledge/aclnn/common/platform_dtype.md` §dtype」的拒绝（以原样 token 为准）。
- 该规则同样适用于 `allowed_range_value.value` 的字符串枚举候选、`format.value` 的格式串，以及 `dtype_support_description` / `format_support_description` 中的 combo 值。
- **反例（必须避免）**：算子文档原文与条目 `src_text` 均为 `HIFLOAT8`，但 `value` 数组误写为 `HFLOAT8`（漏字母 `I`）——属本规则明确禁止的誊写错误；下游生成器仅识别 `HIFLOAT8`，`HFLOAT8` 会触发 `_infer_sort` KeyError 并被 try/except 静默吞掉，最终 ZERO_CASES_GENERATED。

**aclDataType 参数的固定 dtype 规则**：
- 当 `type.value == "aclDataType"` 时，`dtype.value` **固定**为 `["string"]`。`aclDataType` 是表示数据类型的标量枚举，参数本身取值为 dtype 名称字符串，故其"自身 dtype"恒为 `string`。此规则**优先级高于**上面的"类型回填规则"——无论文档"数据类型"列是否给出候选都强制执行，**不**因列值非空而改写，也**不**走"其他非 Tensor 参数使用 `type.value` 回填"分支（否则会错误地产出 `["aclDataType"]`）。
- 文档"数据类型"列里的候选（如 `FLOAT16`/`BFLOAT16`/`INT8`）是参数的**取值域**，必须写入 `allowed_range_value`：`type="enum"`、`value=["FLOAT16","BFLOAT16","INT8"]`；若文档允许"空/缺省/不传"则追加 `null` 候选。**禁止**把这些候选写进 `dtype.value`，也**禁止**给 `dtype.type` 填 `"enum"`（`ValueWithSrcText.type` 仅 `allowed_range_value` 使用，`dtype.type` 恒为 `null`）。
- 其余字段按标量非 Tensor 处理：`format.value="N/A"`、`dimensions.value=[]`（非 `aclTensor`/`aclTensorList`，按下方"类型前置规则"恒为空）、`is_support_discontinuous.value="N/A"`、`array_length="N/A"`。
- 典型场景：`aclnnCalculateMatmulWeightSizeV2` 的 `dataType`（函数原型 `aclDataType dataType`，文档"数据类型"列 `FLOAT16、BFLOAT16、INT8`）→ `type.value="aclDataType"`、`dtype.value=["string"]`、`allowed_range_value.value=["FLOAT16","BFLOAT16","INT8"]`（`type="enum"`）。错误反例：把候选抄进 `dtype.value=["FLOAT16","BFLOAT16","INT8"]`（与 `allowed_range_value` 重复，且使 `dtype` 语义从"参数自身类型"退化为"取值候选"）——`dtype` 应只表达参数自身类型，取值候选由 `allowed_range_value` 承载。注意：本规则只修正 `dtype` 字段的语义错误，**不**改变 `allowed_range_value.type=enum` 这一合规表达；若下游生成器对字符串枚举 `allowed_range_value` 仍有 Z3 求解缺陷，属生成器侧 bug，不在此规则范围内。

**aclIntArray 参数的固定 dtype 规则**：
- 当 `type.value == "aclIntArray"` 时，`dtype.value` **固定**为 `["int"]`。`aclIntArray` 是 int 元素的数组，其"自身元素 dtype"恒为 `int`，与数组元素的语义含义无关。此规则**优先级高于**上面的"类型回填规则"——无论文档"数据类型"列是否给出候选都强制执行，**不**因列值非空而改写为张量 dtype。
- 文档"数据类型"列若给 `aclIntArray` 参数列出张量 dtype（如 `FLOAT16`/`BFLOAT16`），这些列值描述的是**关联张量**的 dtype（应由独立的 `aclDataType` 参数承载，如 `aclnnCalculateMatmulWeightSizeV2.dataType`，见上方 aclDataType 规则），**不**是该数组参数的元素类型；**禁止**把它们写进 `dtype.value`（`dtype.value=["FLOAT16","BFLOAT16"]` 是把关联张量 dtype 错当成数组元素 dtype 的错误表达），也**不**写进 `allowed_range_value`（`aclIntArray` 的取值域是数组值本身，如 `[-2,-1]`，见 `knowledge/aclnn/common/allowed_range.md` §aclIntArray特殊取值）。
- 典型场景：`aclnnCalculateMatmulWeightSize` 的 `tensorShape`（`aclIntArray`，文档"数据类型"列 `FLOAT16`/`BFLOAT16`）→ `type.value="aclIntArray"`、`dtype.value=["int"]`；列里的 `FLOAT16`/`BFLOAT16` 描述该 shape 所属权重张量的 dtype，不是数组元素类型，不写入 `tensorShape` 的 `dtype` 或 `allowed_range_value`。错误反例：`dtype.value=["FLOAT16","BFLOAT16"]`（把关联张量 dtype 错当成数组元素 dtype）。注意：本规则只修正 `dtype` 字段的语义错误，**不**改变 `allowed_range_value` 的合规表达。

**类型前置规则（必须先于下表执行）**：
- 仅当 `type.value` 为 `aclTensor` 或 `aclTensorList` 时，才从文档提取并填写 `dimensions.value`；
- 其他所有类型（包括 `aclIntArray`、`aclFloatArray`、`aclScalar`、`bool`、整数、浮点数和字符串）的 `dimensions.value` 必须为 `[]`，即使其描述中出现"长度""数组""维度""axes"或方括号取值；
- 非 Tensor 容器的元素个数写入 `array_length`，具体数组候选值写入 `allowed_range_value`，二者都不得写入 `dimensions`。

##### G. 条件 Shape 描述识别（门控维度，v3 新增，通用规则）

文档中常出现 "X 的 shape 为 (A, B)；当 Y 配置为 True 时 shape 为 (C, D)" 或者
"若 Y 为 True 则 X shape 为 (C, D)，否则为 (A, B)" 的描述。此时 shape **不是无条件
的，而是门控于某 enum/boolean 参数 Y**。必须把这条规则识别为**单一条件约束**而
非两条独立 shape 描述，否则下游生成器会把 (C, D) 与 (A, B) 当作两个独立候选而
不区分 Y，从而在生成用例时把门控后的 shape 配给非门控的 Y 取值（典型反例见
`iter_001/analysis.json` 15/21 failures：把 `transposeX2=True` 的 `x2.shape=(N, H*rankSize)`
与 `transposeX2=False` 的 `x2.shape=(H*rankSize, N)` 当成独立候选，结果 5 个
transposeX2=True 用例仍按 (H*rankSize, N) 生成）。

**维数特例（rank-only 门控）**：当门控目标不是具体 shape 元组而是**维数/rank**
（如「weight 为 2D / 3D」「dimNum=2」「separated → 2D」「单单单 → 3D」），属本节
特例，按 `knowledge/aclnn/operators/grouped_matmul_v5.md` §4.6.3 H「条件维数 / dimNum 门控（支持场景表）」处理——只门控
`len(目标.shape)`，不门控具体轴；`dimensions` 留全集，门控单独落库，**禁止**把
`dimensions=[2,3]` 当无条件并集留下而不配跨参数门控 expr。

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

**输出形式**：见 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 6（gate_conditional_shape），必须使用 if/elif/else
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

##### I. 转置语义的两种编码与落库方式（通用规则）

> 文档中的“转置”可能由 stride/数据排布表达，也可能由 shape 元组重排表达；先判定编码语义，再选择约束形式。

**语义 A：shape 不反映转置（stride 编码）**——函数签名无转置标志，文档转置定义指向
stride / 数据排布且 **shape 元组不变**（如"shape 为 [M,K] 时 stride 为 [1,M]、数据
排布为 [K,M]"）。此时 `shape[-1]` 永远是逻辑末轴，取不到转置后的物理末轴。

- **必须**按 `knowledge/aclnn/operators/batch_matmul_weight_nz.md` §B.1 新增隐式 bool（`<param>_transposed`）+ `knowledge/aclnn/operators/batch_matmul_weight_nz.md` §D+ 的
  if/elif/else 门控分支；

**语义 B：shape 元组重排编码转置**——文档在不同 groupType / 场景行给出 shape 元组的
**两种顺序**（如 weight "(g,N,K) 或 (g,K,N)"、groupType=2 时 x shape=(K,M) 而
groupType∈{-1,0} 时 x shape=(M,K)）。转置状态直接体现在 shape 元组上。

- **不**引入隐式 bool，**不**写 if/else 分支；
- `shape[-1]` 在两种布局下都等于文档"最后一维"（不转置=K/N，转置=M/K），直接按
  `knowledge/aclnn/common/expression_language.md` §语法 第 6 条写 `shape[-1] < X`；条件映射（"K 轴或 M 轴"）仅放 `src_text`；
- 转置状态本身（如"groupType=2 → x 必须转置即 x.shape=(K,M)"）落成 groupType /
  场景门控下的 **shape 等式**约束，沿用本节 H 的 `cross_param_constraint` unless
  范式，把 `len(weight.shape) == N` 推广为轴等式（共享 K：`x.shape[0]==weight.shape[0]`；
  M：`x.shape[1]==out.shape[1]`；N：`weight.shape[1]==out.shape[2]`）。

**判别信号**：文档若在 shape 元组里直接列出两种顺序（(M,K)/(K,M) 或 (K,N)/(N,K)），
即语义 B；若只给 stride / 数据排布描述而 shape 元组保持不变，即语义 A。两者不可混用：
语义 B 误套 A 的隐式 bool 会引入文档未声明的参数；语义 A 误套 B 的 `shape[-1]` 会
取错轴。

##### J. 条件布局的轴关系不得丢失门控（通用规则）

文档给出同一张量的多种布局或转置形态时，每套轴等式都必须保留其成立条件。不得把
互斥布局的等式合并成无前提 `or`，否则求解器可能选择与实际 Tensor 布局不一致的分支。

**适用判定**（全部满足时执行）：

1. 文档对同一张量参数 `<param>` 给出 shape 元组的**两种顺序**（如 weight
   `(n,k)` 或 `(k,n)`），或明示「可转置/必须转置/不支持转置」且转置态与默认态
   shape 元组顺序不同；
2. 函数签名**无**真实 transpose bool 参数（如 `transposeX1`/`transposeX2`/
   `transposeWeight`）；若有，转置由真实参数表达，走 §4.6.3 G 条件 shape，不属本节；
3. 文档给出了区分这些布局的条件，例如真实 transpose 参数、场景枚举、format、
   连续/非连续要求或其他可执行状态。

**强制规则**：

- **禁止**把两套布局的轴等式写成无前提 `or` 析取。以下为反例（必须避免）：

  ```text
  # ❌ 反例：无前提 OR，丢失转置前提，析取第二支在连续 weight 下被 kernel 拒绝
  expr_type: shape_value_dependency
  expr: (x.shape[1] == weight.shape[0] and weight.shape[1] == out.shape[1])
        or (x.shape[1] == weight.shape[1] and weight.shape[0] == out.shape[1])
  ```

- 选择门控方式时，优先使用函数签名中的真实参数或文档场景参数。只有当前用例模型和
  executor 都能物化某个隐式状态时，才可按
  `knowledge/aclnn/features/transpose_shape.md` 引入隐式 bool；隐式 bool 不能代替
  executor 无法构造的 stride/非连续状态。
- 当前执行能力只能覆盖一种布局时，可在**执行副本/专项能力护栏**中收窄到该布局，
  但不得把文档原始合法域改写成“仅支持该布局”。确定性等式必须带当前场景/布局门控，
  或仅在场景已经被显式固定时生成。

  正例应使用目标算子的真实场景变量为轴关系加门控；如果场景在进入求解前已经固定，
  才可在该执行副本中直接写确定性轴等式。具体表达式和文档原文必须写入对应的
  feature/operator 知识，base 不预设任何算子的参数名、轴位置或默认布局。

- **判别信号**：文档出现「shape 为 (A,B) 或 (B,A)」「可转置」「转置时必须非连续」
  或按 format/场景切换轴位置时触发。具体轴位置与执行能力限制放入对应 feature/operator
  知识，不在 base 中固定某个算子的默认布局。

##### L. 可选参数存在性必须忠实于文档与所选场景（通用规则）

- `is_optional.value=true` 表示 API 合法域包含缺席分支。不得仅因某次运行或 executor
  恰好构造了该参数，就把它提升为必选或额外强制 `param is not None`。
- 参数存在时，其 shape/dtype/format/value 约束必须生效；参数缺席时，使用
  `param is None or (...)` 等守卫保留合法缺席分支。存在性由文档与显式
  `scene_directive` 决定；生成结果仅用于一致性审计，不得反向作为参数必传或缺席的
  语义证据，也不得让求解器自行改变存在性以逃避约束。
- 只有文档明确写“必须传”“不得为空”时才产出强制 present；只有文档明确写“必须传空/
  仅支持 nullptr”时才把 `[null]` 写入 canonical 合法域。
- 因当前生成器或 executor 能力不足而临时投影为缺席时，必须放在精确 operator/feature
  知识中并标记为 `TEMP_CAPABILITY_GUARD`，写明原因、影响范围、解除条件和回归验证；
  不得把临时投影描述成 API 永久约束，也不得放入 base 作为跨算子规则。

### 4.7 `constraints_in_parameters`（跨参数 / 单参数约束）

#### 4.7.1 顶层 key

- 平台名；不存在平台差异时**各平台使用相同的约束列表**（不要删减为单项 `"common"`）。

#### 4.7.2 `InterParamConstraint` 字段

| 字段 | 必填 | 说明 |
| ---- | ---- | ---- |
| `expr_type` | 是 | **自由字符串**。优先从 `knowledge/aclnn/common/expression_language.md` §expr_type 字典中选用；若字典无法覆盖，允许使用实际语义值（如 `cross_param_constraint`、`parameter_representation`、`self_value_enum`、`self_string_length`、`self_value_dependency`、`shape_choice`） |
| `expr` | 是 | 规范化后合法的 Python 布尔表达式（第 6 章）；允许裸 `null`，执行前转换为 `None`；**不得为空字符串**——无法形式化的约束改记入相关参数 `description`/`src_text`，不产出 `constraints_in_parameters` 条目（见 `knowledge/aclnn/features/format_cast.md` §4.6.7、`knowledge/aclnn/features/broadcast.md` §B、§5、`knowledge/aclnn/common/expression_language.md` §语法 第 10 条） |
| `relation_params` | 是 | 表达式中**所有**被引用的参数名（按出现顺序，去重） |
| `src_text` | 是 | 原文摘录，**可为空字符串** |

#### 4.7.3 提取规则

1. **跨参数约束优先**：涉及 ≥2 个参数的约束**必须**进入 `constraints_in_parameters`，不要只在 `inputs`/`outputs` 中备注。
2. **单参数约束复写**：若约束在 `allowed_range_value` 中已有表达，仍可在 `constraints_in_parameters` 中**附加一条带 `expr` 的形式化版本**（不视为冗余，而是机器可判定性的增强）。
3. **单参数 shape 约束**：若已在 `dimensions` 中表达（如 `[2,3]`），可省略重复。
4. **存在性约束**必须用完整、可求解的布尔表达式，不允许退化为“可选/必选”自然语言。
   当前生成链路中，凡等价关系任一侧含 `is None` / `is not None`，**禁止**写成
   `(A) == (B)`。两个 Optional 参数共存时，当前生成链路使用 `if/else` 表达完整两态；
   例如 `scale` 与 `zeroPoint` 必须共存时生成：

   ```text
   (zeroPoint is None) if (scale is None) else (zeroPoint is not None)
   ```

   不要把两个 Optional 的共存关系写成 OR-of-ANDs；当前缺参预处理在混合 presence
   组合上可能把“两个分支均为 False”错误清空。presence 与**必选 Tensor** rank 双向
   绑定也使用 `if/else`，避免求解器把分支内的“presence and 属性”改写成蕴含后令
   整个 OR 空泛为 True。例如“P 为空时 T 为 2D，P 存在时 T 为 3D”生成：

   ```text
   (len(T.shape) == 2) if (P is None) else (len(T.shape) == 3)
   ```

   上式只适用于 `T` 是必选参数、在两个分支都存在的情况。若 `T` 也可缺席，必须把
   `T is None` / `T is not None` 纳入对应分支，禁止在 `T is None` 分支访问 `T.shape`。
   若原文只给出单向规则（例如仅写“P 为空时 T 为 2D”），优先生成
   `(len(T.shape) == 2) if (P is None) else True`，不得臆造“P 存在时 T 为 3D”的反向
   分支。只有 if/else 无法清晰承载时，才回退到等价的
   `P is not None or len(T.shape) == 2`。

   还要避免把“必须存在/缺席”与 dtype、shape、value 条件放进同一个 `and`，例如
   `P is not None and P.range_value == 1` 在当前求解器中会被解释为
   `P is not None -> P.range_value == 1`，并不会强制 P 存在。需要同时强制 presence 时，
   拆成 `P is not None` 与 `P is None or P.range_value == 1` 两条约束。

   **不得**只保留 `P is None and len(T.shape) == 2`，否则会错误删除合法的 P 存在
   分支。只有所选场景已明确固定 P 必须缺席时，才可在该场景的条件分支内直接合取
   `P is None` 与 rank 条件。
5. **禁止**把"算子功能说明"或"参数描述"塞入 `constraints_in_parameters`。
6. **保护值语义**：参数名为 `epsilon`/`eps`，且功能描述明确称其为"除0保护值"、
   "分母保护值"或数值稳定项时，应推导严格正值约束。若另有上界说明，合并成链式
   不等式，例如 `0 < epsilon.range_value <= 1e-4`。`src_text` 必须同时摘录保护值
   描述和上界说明，使隐式下界可追溯。
7. **NZ 块尺寸必须显式落库**：见 `knowledge/aclnn/features/nz_matmul.md` §4.6.5 C。NZ 张量的 `shape[3]` /
   `shape[4]` 块尺寸硬约束**必须**用 `shape_equality`（或 `shape_value_dependency`）
   形式化写出，**禁止**把 `[[16,16],[16,16]]` / `[[16,16]]` 写进 `allowed_range_value`
   冒充块尺寸约束（该字段只约束元素数据值，生成器按元素值解释，见 `knowledge/aclnn/features/nz_matmul.md` §4.6.5 D）。
8. **条件 Shape 约束必须按门控参数分支**：当某参数 X 的 shape 由
   enum/boolean 门控参数 Y 的取值决定（见 §4.6.3 G），必须在
   `constraints_in_parameters[平台]` 中为 X 产出**单一条件 shape 约束**条目，
   形如：

   ```text
   # 推荐写法：if/elif/else 链（参见 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 6）
   (X.shape == [A, B]) if (Y.range_value == "{value_A}")
   else (X.shape == [C, D]) if (Y.range_value == "{value_B}")
   else True
   ```

   只有 if/elif/else 无法清晰承载或存在求解器兼容性要求时，才使用 `unless` 等价形式：

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

9. **Forward-Output Partial-Shape 必须显式落库**：满足 `knowledge/aclnn/features/backward_partial.md` §4.6.6 的 backward /
   grad 场景，必须同时检查并落库“前缀维度跟随”“rank 一致”“文档明确给出的派生轴
   公式”。不得用 `gradInput.shape == self.shape` 或仅末维公式代替前两项。

10. **大小/数量语义参数的隐式 >0 约束**：当某标量取值参数的
    `description` 含"空间大小"/"的数据量"/"元素个数"/"的数量"/"占用空间大小"
    等表示"大小/数量/个数"的语义短语时，必须按 `knowledge/aclnn/features/implicit_pos.md` §4.6.9 在 `constraints_in_parameters`
    中追加 `P.range_value > 0` 条目（`expr_type=value_dependency`，
    `allowed_range_value.value=[]`）。不适用于 shape/dtype/format/枚举/bool 参数。
11. **公共互推导 / broadcast 引用必须展开**：当文档引用
    `互推导关系.md` 或 `broadcast关系.md`，必须按 `knowledge/aclnn/features/broadcast.md` §4.6.10 产出对应的
    `type_dependency` / `shape_broadcast` / `shape_value_dependency` 约束；不得只在
    `src_text` 中保留链接。

    **`type_equality` 与 `type_dependency` 的强制分界**：

    - `type_equality` **只**用于无条件、无门控、无可选存在性判断、无其他
      属性参与的纯 dtype 等式。典型形式为 `x.dtype == y.dtype`，或多参数
      无条件等值链 `x.dtype == y.dtype and x.dtype == z.dtype`。此类约束独立描述
      两个或多个参数的 dtype 属性始终相等。
    - 只要 dtype 关系受**任何条件**影响，必须使用 `type_dependency`。包括但不
      限于：枚举/布尔/标量参数门控，`is None` / `is not None` 可选存在性
      判断，`if/else`，“某场景下一致”，“某模式下允许不一致”，不同 dtype
      合法组合的析取表达，以及 dtype 与 value/shape/format/presence 的联合关系。
    - **不得根据结果子式中出现 `x.dtype == y.dtype` 就选 `type_equality`**；必须
      对整条 `expr` 分类。整条表达式只要还引用了 `.range_value`、`None`、
      非 dtype 属性或条件运算符，就是 `type_dependency`。

    ```text
    # 正例：独立、无条件的属性相等
    expr_type: type_equality
    expr: x.dtype == y.dtype

    # 正例：模式参数决定 dtype 是否需要相等
    expr_type: type_dependency
    expr: (mode.range_value == "PA_NZ") or (x.dtype == y.dtype)

    # 正例：可选参数存在时才要求 dtype 一致
    expr_type: type_dependency
    expr: optional is None or x.dtype == optional.dtype

    # 反例：以下两条都不得标为 type_equality
    (cacheModeOptional.range_value == "PA_NZ") or (key.dtype == value.dtype)
    compressLensOptional is None or slotMapping.dtype == compressLensOptional.dtype
    ```
12. **MatMul Reduce 维度相等必须落库**：当文档写
    "mat2 的 Reduce 维度需要与 self 的 Reduce 维度大小相等"、"self 的 last dim 与
    mat2 的 penultimate dim 相同" 等语义时，必须按实际布局落为 `shape_value_dependency`。
    文档即使没有另写“Reduce 维度相等”，只要参数 shape 表使用同名符号明确给出轴语义，
    也必须建立真实 Tensor 轴关系。例如 `x=[M,K1]`、`weight1=[K1,N1]`、
    `weight2=[K2,N2]`、`y=[M,N2]` 至少产出：

    ```text
    x.shape[-1] == weight1.shape[-2]
    x.shape[:-1] == y.shape[:-1]
    weight2.shape[-1] == y.shape[-1]
    ```

    若正文另有 `K1=N2`、`N1=K2` 或 `N1=2*K2`，还要把它们绑定到对应真实轴；不得只
    生成 weight 间关系而遗漏 `x` 与第一个 weight 的 Reduce 轴。优先直接表达 Tensor
    轴等式；只有该符号还参与外部参数计算或无法用真实轴直接表达时，才登记隐式
    `dimension_variable`。
13. **派生值可求解约束必须落库**：当 `knowledge/aclnn/features/format_cast.md` §4.6.7 适用（存在派生子接口）且
    文档存在从子接口入参到派生输出 `D` 取值的**确定映射**（`dtype_support_description` /
    `format_support_description` 或正文 combo 表）时，必须在 `constraints_in_parameters`
    中产出 `derived_value` 条目，其 `expr` 编码该映射为可 `eval()` 的布尔表达式
    （恒等映射用等式、查找表用析取、格式派生用 actualFormat→format 析取，见 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 9）；
    `expr` **不得为空串**；`relation_params` 包含 `D` 及全部键参数。文档无确定映射时
    不产出该条目，派生语义由 `[DERIVED]` description 承载（`knowledge/aclnn/features/format_cast.md` §4.6.7）。

14. **格式转换算子 dtype 等式必须落库**：当 `knowledge/aclnn/features/format_cast.md` §4.6.7 适用（算子为格式转换 /
    布局变换类，文档 dtype 表每行 src.dtype == dst.dtype）时，必须在
    `constraints_in_parameters[每个支持平台]` 中追加 `srcTensor.dtype == dstTensor.dtype`
    的 `type_equality` 约束；dstTensor 值域沿用 src，不得按不同 dtype 负值域生成。

15. **条件性取值/存在性关系必须形式化为 cross_param_constraint**：当文档写「参数 A
    取值 v 时参数 B 必须/不得取 w」「B 仅在 A=w 场景下支持」「A=v 时 B 必须传空/
    不取某值」等**条件性取值或存在性限制**时，**必须**在
    `constraints_in_parameters` 中产出 `cross_param_constraint`（或
    `value_dependency` / `presence_dependency`，按语义）expr，形如：

    ```text
    # A 取 v ⇒ B 不得取 w
    not(A.range_value == v) or (B.range_value != w)
    # A 取 v ⇒ B 必须传空（缺席语义）
    not(A.range_value == v) or (B is None)
    # A 取 v ⇒ B 仅可取 w
    not(A.range_value == v) or (B.range_value == w)
    ```

    `relation_params` 必须同时含 A 与 B；`src_text` 摘录原文中「A 取 v 时 B …」短语。
    **禁止**只在 `B.description` / `B.allowed_range_value.src_text` 里自然语言备注
    「须传空」「仅某场景支持」而不产出可判定的 `expr`——下游生成器不会消费
    `description` 里的自然语言限制，会自由产出违反该限制的取值组合。

    **语义边界**：“失效 / 不参与计算 / 取值被忽略”不等于“必须传空”。若原文只表示
    B 的值在 A=v 时不影响结果，且未要求 B 缺席或限定取值，则不收窄 B 的合法域；只在
    `description/src_text` 记录忽略语义，供 golden/executor 决定是否使用。只有原文明示
    “必须传空 / 不得传入 / 仅支持 nullptr”时才生成 presence/缺席约束。

    **正例**（aclnnGroupedMatmulV5：groupListType=2 仅全量化且 groupType=0 场景下支持）：

    ```text
    expr_type: cross_param_constraint
    expr: not(groupListType.range_value == 2) or (groupType.range_value == 0)
    relation_params: ["groupListType", "groupType"]
    src_text: "groupListType=2：仅全量化且groupType=0场景下支持"
    ```

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
- **dtype×format 交叉联合表禁用**：当组合表**同一行同时含 dtype 列与
  format 列，且 dtype 与 format 存在行内依赖**（不同 dtype 对应不同 format 候选；
  判据：若把表按列拆成「纯 dtype 表 + 纯 format 表」会丢失信息、产生原本非法的
  dtype×format 组合）——如 `srcTensor.dtype × dstTensor.dtype × dstTensor.format`
  中 INT8→FRACTAL_NZ、INT32→FRACTAL_NZ_C0_16——**不得**填入本字段；拆解会丢失行内
  dtype↔format 对应，并产生数值枚举码与 dtype 名混用（如 `additionalDtype="2"` 来自
  `ACL_INT8(2)`）。此类**交叉**表必须落库为 `constraints_in_parameters` 的一条
  OR-of-ANDs `derived_value`/`cross_param_constraint` expr（见 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 9「主接口
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
- **dtype×format 交叉联合表禁用**：与 §4.9 同理，**同一行同时含 dtype 列
  与 format 列且 dtype 与 format 存在行内依赖**的交叉表**不得**填入本字段；禁止用
  「`srcTensor` format 列表 × `dstFormat` 笛卡尔积、`actualFormat=dstFormat`」之类
  凭空捏造的格式组合凑数（典型反例：aclnnNpuFormatCast A3/A2 `format_support_description`
  出现 `srcTensor=ND × dstFormat∈{2,29,30,32,33}` 的 25 行捏造组合）。交叉表必须落库
  为 OR-of-ANDs expr（`knowledge/aclnn/common/expression_language.md` §常用模式 模式 9），本字段留 `{}`。
- **以下两类仍填本字段，不属交叉表**：① **纯 format 组合表**——只有 format 列（哪怕
  跨多个参数，如 `x1.format × x2.format × out.format`）；② **同表但独立的 dtype+format
  表**（任意 dtype 配任意 format、拆开不丢失信息）的 format 部分——按 §4.9 同类情形
  处理，不强求 OR-of-ANDs。

## 5. 边缘场景处理

| 场景 | 处理方式 |
| ---- | -------- |
| 文档仅给"产品支持"无 dtype 组合表 | `dtype_support_description={}` |
| 文档仅给"产品支持"无 format 组合表 | `format_support_description={}` |
| 多平台 dtype 列表完全一致 | 各平台各自复制相同列表；不用"common"合并 |
| 参数是 `aclIntArray *xxx` | `type.value="aclIntArray"`，`array_length` 必填实值 |
| 参数是 `aclDataType xxx`（标量数据类型枚举） | `type.value="aclDataType"`、`dtype.value=["string"]`（固定，见 §4.6.3 aclDataType 规则）；文档"数据类型"列候选写入 `allowed_range_value`（`type="enum"`），**不**写入 `dtype` |
| 文档出现 `Optional` 后缀但未说明是否可空 | `is_optional.value=false`（保守），`src_text` 摘录原文待人工复核 |
| 文档写"shape 为 [B,H] 或 [B,1,H]" | 拆为 `shape_choice` / `shape_dependency` 约束；不要并成模糊规则 |
| 文档写"x 和 y 数据类型必须一致"，且不受任何模式/场景/可选性条件影响 | `expr_type="type_equality"`，`expr="x.dtype == y.dtype"`，`relation_params=["x","y"]` |
| 文档写"当 mode=M 时 dtype 一致/可以不一致" | `expr_type="type_dependency"`；expr 保留 `mode.range_value` 门控，不得因子式含 dtype 等式而标为 `type_equality` |
| 文档写"可选参数 y 存在时与 x 数据类型一致" | `expr_type="type_dependency"`，`expr="y is None or x.dtype == y.dtype"`；存在性判断使其不再是纯等式 |
| 文档引用 `互推导关系.md` 或写"数据类型推导规则" | 按 `knowledge/aclnn/features/broadcast.md` §A 的推导表生成 `type_dependency`；输出若要求与推导后 dtype 一致，必须绑定输出 dtype；推导结果不在输出 dtype 允许集合内的输入组合必须排除 |
| 文档引用 `broadcast关系.md` 或写"满足 broadcast 关系" | 按 `knowledge/aclnn/features/broadcast.md` §B 的广播规则生成 `shape_broadcast`；若输出轴由 broadcast 推导得到，还要生成输出轴等于 broadcast 结果的 `shape_value_dependency` |
| MatMul 文档写"Reduce 维度需要相等" | 生成 `shape_value_dependency` 绑定真实 Reduce 轴；若存在转置/非转置布局，必须按对应 bool 门控分支；不得只写 `ceil(k,k0)=k1` 而允许 NPU 逻辑 Reduce 维度不等 |
| 多个参数 shape 表复用同名符号，如 `x=[M,K1]`、`weight1=[K1,N1]`、`weight2=[K2,N2]`、`y=[M,N2]` | 将每次出现映射到真实 Tensor 轴并生成 `shape_value_dependency`；至少绑定首个 MatMul Reduce 轴和输出前导轴。不得因正文未重复写“相等”而把同名轴独立随机生成 |
| 场景指令选择 non-quant/quant/pseudo-quant，文档规定其他场景参数不得输入 | 将场景选择落为完整的可执行约束；被排除场景的全部专属 Optional 参数必须**逐参数**生成 `expr_type="presence_dependency"`、`expr="<param> is None"`、`relation_params=["<param>"]` 的条目，`src_text` 摘录文档中的禁止输入依据。不能只在 `description` / `src_text` 中备注，也不能通过省略 `presence_dependency` 期待参数自动缺席；无 presence 约束的 Optional 参数仍可能被生成器随机置为存在。仅“未选择参数”不能作为强制缺席依据：未选择参数继续按文档和已选场景适配，只有已选场景或文档明确禁止时才生成 `<param> is None` |
| 文档写"仅 Atlas A3 支持 BF16" | 在对应平台的 `dtype.value` 中体现差异，`src_text` 摘录原文 |
| 文档给出"确定性计算：默认确定性" | `deterministic_computing["平台"].value = "true"`，`src_text` 摘录该句 |
| 文档给出"确定性计算：默认非确定性" | `deterministic_computing["平台"].value = "false"`，`src_text` 摘录该句 |
| 文档**完全没有** `返回码` 章节 | `return_info=[]` |
| `allowed_range_value` 只有单边界或开区间 | `allowed_range_value.value=[]`；在 `constraints_in_parameters` 中用 `value_dependency` 不等式表达，禁止为 `type=range` 写 `null` 端点 |
| **文档写 bool 参数（无固定值约束）** | `allowed_range_value.type="enum"`、`value=[false, true]`；强行 bool 枚举，不允许填 `[]` 配 `type="range"`（否则下游生成器按浮点填充，会产生 1.0/1.23e-40 等非法值） |
| 表达式无法用 Python 表达（自然语言公式） | **不**产出 `constraints_in_parameters` 条目（空 `expr` 违 §4.7.2）；把语义记入相关参数 `description`/`src_text` 摘录原文，待人工校对 |
| 文档出现矛盾（A段dtype=X，B段dtype=Y） | 优先**保守**取值（取并集），`src_text` 摘录矛盾原文，等待人工确认 |
| 文档写"1维，最大长度256" | `dimensions.value=[1]`，**长度256 不得放入 `dimensions`**；须在 `constraints_in_parameters` 中加 `self_shape_axis_value` 约束 |
| 文档写"shape 与 weight1 一致" / "与输入相同" | `dimensions.value=[]`；**跨参数引用留给 `constraints_in_parameters`** 的 `shape_equality` 约束 |
| 文档写"(BS, H) 或 (BS/rankSize, rankSize*H)" | 拆为 `shape_choice` 约束 + `parameter_representation` 约束；两个 shape 都是 2 维，因此 `dimensions.value=[2]` |
| 文档写"其中k0=16" | `k0` 归类为 `constant`，`constant_value=16`；不放入 `inputs`（直接写入 `expr` 表达式） |
| 文档写"H*rankSize"中的 `rankSize` 仅在复合表达式出现 | 归类为 `external_constant`，按平台分别给 `allowed_range_value` |
| 文档写"Reduce 维度需要…" | `Reduce` 是 reduce 操作概念词，**不**抽取为隐式维度变量 |
| 文档写"Softmax、LayerNorm" | **不**抽取为隐式维度变量（是操作名 / 算法名） |
| 文档写"支持配置空或者[-2,-1]"（aclIntArray） | `allowed_range_value.value=[null, [-2, -1]]`，`type=enum`；"空"表示未传值，不得写成字符串 |
| 文档写"仅 Atlas A2 支持 BF16" | 在对应平台的 `dtype.value` 中体现差异，`src_text` 摘录原文 |
| 文档写"shape 为 [E, N1] / [N1]（per-channel / per-tensor）" | `dimensions.value=[1, 2]`（按各 shape 的实际 rank 去重枚举），shape 选择逻辑走 `shape_choice` / `shape_value_dependency` 约束；若同段还出现 3 维 shape，才扩为 `[1, 2, 3]` |
| 文档写"x 和 y 必须共存，要么都存在要么都不存在" | 生成一条 `presence_dependency`：`(y is None) if (x is None) else (y is not None)`。禁止写布尔 `==`；两个 Optional 共存也不使用 OR-of-ANDs，避免混合 presence 在当前缺参预处理中空泛通过 |
| 文档写"actType 取值为 0 到 5" | `allowed_range_value.value=[[0, 5]]`，`type=range`；可附加 `self_value_range`：`0 <= actType.range_value <= 5` 增强机器可判定性 |
| 文档把 epsilon/eps 描述为"除0保护值"，并建议"≤1e-4" | `allowed_range_value.value=[]`；增加 `value_dependency`：`0 < epsilon.range_value <= 1e-4`，`src_text` 同时摘录两句 |
| **文档写"NZ格式各个维度表示：（b, n1，k1，k0，n0），其中k0 = 16， n0为16"** | 按 `knowledge/aclnn/features/nz_matmul.md` §4.6.5 全流程处理：①`mat2.dimensions.value=[5]`；②`mat2.allowed_range_value.value=[]`（块尺寸是 shape 约束，不入元素取值字段）；③`constraints_in_parameters` 追加 `mat2.shape[3]==16` 与 `mat2.shape[4]==16` 两条 `shape_equality`，`src_text` 摘录完整原文 |
| **文档写"NZ格式各个维度表示：（b, k1，n1，n0，k0），其中n0 = 16， k0为16"（转置 NZ）** | 同上，但**作为独立两条约束**落库（与上一种布局不合并），`src_text` 摘录对应的转置原文；`mat2.allowed_range_value.value=[]` |
| **文档同时写明非转置与转置 NZ 两种布局** | 两套布局的 `mat2.shape[3]==16` / `mat2.shape[4]==16` 必须分别落库（共 4 条 `shape_equality`）；`mat2.allowed_range_value.value=[]`（块尺寸约束不入元素取值字段，约束条目按布局拆分） |
| **`product_support` 含 ≥2 个平台，但 `inputs`/`outputs` 中某非隐式参数只产出 1 个平台条目** | 漏抽：必须**逐平台复制相同 `ParamAttributes`**（即便各平台字段值完全一致）。常因模型误读 §4.6.2 旧措辞（"约束完全一致可用单个平台名"）所致——该规则禁止用于"代笔"其他平台 |
| **文档写"X 的 shape 为 (A, B)；当 Y 配置为 True 时 shape 为 (C, D)"** | **不可**拆为两条独立无条件 shape 描述；必须在 `constraints_in_parameters` 中为 X 产出**单一条件 shape 约束**（`knowledge/aclnn/common/expression_language.md` §常用模式 模式 6），用 `Y.range_value` 等门控参数分支；`expr_type` 优先 `shape_choice` 或 `parameter_representation`；`src_text` 同时摘录默认 shape 短语与"配置为 X 时…为…"短语，确保门控可溯源（典型反例：aclnnAlltoAllMatmul 中 x2.shape 在 transposeX2=True 时应为 (N, H*rankSize) 而非无条件 (H*rankSize, N)） |
| **`shape_value_dependency` 写成无条件形式（含 `mat2.shape[j]` / `self.shape[i]` 引用但未按 `knowledge/aclnn/operators/batch_matmul_weight_nz.md` §B.1 隐式 bool 门控）** | 改写为 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 6.1 单条 if/else 或 unless 多分支；`relation_params` 包含对应隐式 bool；`src_text` 同时摘录"非转置 NZ (b, n1, k1, k0, n0)" 与 "转置 NZ (b, k1, n1, n0, k0)" 原文 |
| **一段式算子：函数原型无 `GetWorkspaceSize`** | `function_signature` 取唯一函数声明；参数列表无 `workspaceSize`/`executor`。不得伪造 `GetWorkspaceSize` 段；**不得**写入 `is_single_function_mode` 字段 |
| **一段式算子：输出为标量指针（`uint64_t*`/`int64_t*` 等）** | 该参数**进 `outputs`**（`type.value` 去 `*`、`format="N/A"`、`dimensions=[]`、`is_operator_param=true`），**不**当流程参数排除；`aclnnCalculateMatmulWeightSize` 的 `weightTensorSize` 即此 |
| **aclIntArray 参数的 dtype 固定为 int** | `type.value="aclIntArray"` → `dtype.value=["int"]`（固定，见 §4.6.3 aclIntArray 规则）；文档"数据类型"列若列张量 dtype（如 `FLOAT16`/`BFLOAT16`）描述的是关联张量，**不**写入 `dtype`（不得写成 `dtype.value=["FLOAT16","BFLOAT16"]`） |
| **aclIntArray / aclFloatArray / aclBoolArray 的 expr 禁用 `.shape`** | 这类非 Tensor 数组无 `.shape` 属性；长度约束写 `len(paramName)`（如 `2 <= len(tensorShape) <= 6`、`len(tensorShape) >= 1`），**禁止** `len(paramName.shape)` / `paramName.shape[i]`（运行期 `AttributeError`）；`.shape` 仅 `aclTensor`/`aclTensorList` 可用 |
| **aclTensorList 参数 P 写“长度与 Q 相同”** | P 为 Optional 时生成 `(P is None) or (len(P) == len(Q))`，否则生成 `len(P) == len(Q)`；禁止 `.array_length` 和 `len(P.shape)`；相同文案出现在多个参数行时逐参数生成，不能去重 |
| **backward / grad 文档写“gradOutput 与 self/input 维度一致”，同时末尾轴由 padding 等参数派生** | 按 `knowledge/aclnn/features/backward_partial.md` §4.6.6 / `knowledge/aclnn/common/expression_language.md` §常用模式 模式 7 拆分：①前缀切片相等；②rank 相等；③文档明确的派生轴公式。禁止只提取末维公式，也禁止用 `gradInput.shape == self.shape` 替代 gradOutput 跟随关系 |
| **文档写参数描述含“空间大小/数据量/元素个数/数量”等大小/数量语义短语** | 按 `knowledge/aclnn/features/implicit_pos.md` §4.6.9 处理：在 `constraints_in_parameters` 中追加 `P.range_value > 0`（`expr_type=value_dependency`），`allowed_range_value.value=[]`；`src_text` 摘录 description 原文 + 补注“大小/数量语义隐含 >0”；不适用于 shape/dtype/format/枚举/bool 参数 |
| **文档按产品分节给出同一参数的不同候选值 / 固定占位值** | 按 `knowledge/aclnn/features/format_cast.md` §4.6.7 处理：逐平台产出 `allowed_range_value`（`type="enum"`），各平台 `value` 取该产品分节/示例的实际候选；占位产品 `value` 为单元素列表（如 `[-1]`），不得追加总表候选、不得留空 `[]`；`src_text` 逐平台摘录该分节原文/示例代码；`type`/`dtype`/`format` 逐平台一致，仅 `allowed_range_value.value` 随产品分歧 |
| **派生输出参数标记 [DERIVED] 且文档存在确定映射（如 dtype/format 组合表 → actualFormat）** | 按 `knowledge/aclnn/features/format_cast.md` §4.6.7 与 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 9 处理：产出 `derived_value` 条目，`expr` 编码映射为可 `eval()` 的布尔表达式（恒等映射用等式、查找表用析取）；`expr` 不得为空串；`relation_params` 含 `D` 及全部键参数。文档无确定映射时不产出该条目，由 `[DERIVED]` description 承载。典型反例：aclnnNpuFormatCast dstTensor.format/actualFormat 的 `derived_value` expr 留空，生成器随机赋值致 86/100 A3 用例不一致 |
| **格式转换算子文档 dtype 表每行 src.dtype == dst.dtype** | 按 `knowledge/aclnn/features/format_cast.md` §4.6.7 处理：产出 `type_equality` 约束 `srcTensor.dtype == dstTensor.dtype`；dstTensor 值域沿用 src，不得按不同 dtype 负值域生成。典型反例：aclnnNpuFormatCast 300/300 条用例 src.dtype != dst.dtype（uint8→int8），dstTensor.range_values 按 int8 负值域 [-255,-1] 生成 |
| **文档组合表是 dtype×format 交叉联合表（同一行同时含 dtype 列与 format 列，且 dtype 与 format 存在行内依赖——不同 dtype 对应不同 format 候选，拆成纯 dtype 表+纯 format 表会丢失信息/产生非法组合；如 srcTensor.dtype×dstTensor.dtype×dstTensor.format 中 INT8→FRACTAL_NZ、INT32→FRACTAL_NZ_C0_16）** | **不得**拆进 `dtype_support_description`/`format_support_description`（拆解会丢失行内 dtype↔format 对应，并产生数值枚举码与名字混用）；按 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 9「主接口联合组合表」落库为**一条** `derived_value`（或 `cross_param_constraint`）OR-of-ANDs expr，析取所有合法行；`dtype_support_description`/`format_support_description` 对该算子留 `{}`。典型反例：aclnnNpuFormatCast iter_001 把联合表拆成 `dtype_support_description`（`additionalDtype` 抄成 `"2"` 而非 INT8、`dstFormat`/`actualFormat` 抄数值枚举码）与 `format_support_description`（`srcTensor=ND × dstFormat` 笛卡尔积、`actualFormat=dstFormat` 凭空捏造 25 行），`derived_value.expr` 留空 |
| **文档组合表只有 dtype 列（纯 dtype 表，跨多参数也行），或只有 format 列（纯 format 表）** | 纯 dtype 表填 `dtype_support_description`、纯 format 表填 `format_support_description`，**不**落 OR-of-ANDs expr；不属 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 9 适用范围 |
| **文档组合表同表含 dtype 列与 format 列但二者独立（任意 dtype 配任意 format，拆开不丢失信息）** | 按"单独 dtype 约束 + 单独 format 约束"处理：dtype 部分填 `dtype_support_description`、format 部分填 `format_support_description`（或用 `type_equality` + format 枚举 + `format_rank_consistency`）；**不**强制 OR-of-ANDs。判据：拆成纯 dtype 表+纯 format 表后是否产生原本非法的 dtype×format 组合——不产生即为独立 |
| **`constraints_in_parameters` 出现 `expr=""` 空壳条目（`derived_value`/`cross_param_constraint` 等）** | 违 §4.7.2/`knowledge/aclnn/features/format_cast.md` §4.6.7：`derived_value` 在文档存在确定映射时 `expr` 必须编码为可求解 OR-of-ANDs/等式 expr（`knowledge/aclnn/common/expression_language.md` §常用模式 模式 9），不得为空；不可形式化的约束（如「转 NZ 后不许 contiguous/transpose」）**不**产出条目，改记入 `description`/`src_text`。典型反例：aclnnNpuFormatCast iter_001 三平台 `derived_value.expr=""` 与 `cross_param_constraint.expr=""` 空壳 |
| **条目内 `src_text` 的 dtype/format token 与 `value` 数组元素不逐字一致** | 违 §4.6.3「dtype / value 候选逐字一致性硬规则」与 §6.33：必须修正 `value` 元素使其与 `src_text` / 文档原文逐字一致；典型反例：`src_text` 含 `HIFLOAT8` 而 `dtype.value` 写 `HFLOAT8`（漏字母 `I`），下游生成器 `HIFLOAT8` 之外一律 KeyError 被静默吞掉，终致 ZERO_CASES_GENERATED |
| **文档原文 dtype token 不在 `knowledge/aclnn/common/platform_dtype.md` §dtype 受控字典** | **不得**改写为字典内"形似"项凑数（漏字母/截断/替换字母）；原样保留文档 token，`src_text` 摘录原文，并在该参数 `description` 末尾补注 `[DICT_GAP:<token>]` 供人工扩容字典；此情形不属 §6.4 拒绝，以原样 token 为准 |

## 6. 自检清单（提取完成后必跑）

> 模型在生成 JSON 之后、提交给用户之前，必须执行本章**全部自检项**。任何一项不通过均需重做。

1. **JSON 校验**：用 `OperatorRule.model_validate_json(json_str)` 解析，**不抛异常**。
2. **字段完整**：`OperatorRule` 的**全部 11 个**必填字段均存在且非 `None`；数组/对象至少是空容器。
3. **平台字典一致 & 平台覆盖完整**：`product_support` 中的每个平台名，在
   `deterministic_computing`、`constraints_in_parameters` 的 key 中**至少出现一次**。
   **`inputs` / `outputs` 中的每个 `is_operator_param.value=true` 的非隐式参数，必须为
   `product_support` 中的每一个平台都产出条目**——即使各平台 `ParamAttributes` 内容完全
   一致，也必须逐平台复制；不得用单个平台名"代笔"。常见错误模式：从 `Atlas 350 加速卡`
   文档表格读取约束后，只输出 `Atlas 350 加速卡` 条目，遗漏 `Atlas A3 / A2` 条目。
4. **dtype/format 字典一致**：所有 `dtype.value` 元素来自 `knowledge/aclnn/common/platform_dtype.md` §dtype（含标量类型）；非 Tensor 参数若非"仅支持空指针"，`dtype.value` 不得为空，缺失时按 type 回填；所有 `format.value` 元素来自 `knowledge/aclnn/common/platform_dtype.md` §format 或为 `"N/A"`。各 dtype/format 字符串须与该条目 `src_text` / 文档原文逐字一致（仅允许 `knowledge/aclnn/common/platform_dtype.md` §dtype / §format 登记的规范化映射，见 §4.6.3「dtype / value 候选逐字一致性硬规则」与 §6.33）。
5. **表达式合法**：每条 `expr`（非空）先把裸 `null` token 规范化为 `None`，再用
   Python AST 解析；不得有 `SyntaxError`。`null`/`None` 不得作为数值大小比较边界。
6. **关系参数一致**：`expr` 中**所有出现的标识符**都在 `relation_params` 中；`relation_params` 中所有参数名都在 `inputs`/`outputs` 有对应卡片（隐式维度变量/外部常量允许例外，但须在 `inputs` 中登记）。
7. **来源可溯**：`function_explanation`/`dtype`/`format`/`dimensions`/`allowed_range_value` 的 `src_text` 至少 30% 非空（无来源的纯模型外推视为无效）。
8. **隐式参数完整性**：所有在 `constraints_in_parameters` 的 `expr` 中出现的**非函数签名标识符**（如 `BS`、`H`、`N`、`rankSize`），必须**全部**出现在 `inputs` 中，且 `is_operator_param.value=false`。
9. **dimensions 合理性与类型门禁**：仅 `aclTensor` / `aclTensorList` 允许 `dimensions.value` 非空；其他类型必须为 `[]`。非空时必须是去重、升序的合法 rank 显式枚举，且每项均为满足 `0 ≤ rank ≤ 10` 的整数。连续范围必须完整展开：原文“1～3维”写 `[1,2,3]`，禁止写成 `[1,3]`；只有原文明示“1维或3维”时才写 `[1,3]`。轴长度及每轴范围不得写入 `dimensions.value`，应由 `constraints_in_parameters` 表达。
10. **枚举拆分完整**：若 `allowed_range_value.type=enum` 且 value 是 `List[str]`，则字符串中**不得**再包含 `/`、`、`、`以及`、`and`、`/` 等分隔符（必须已被拆成独立元素）。
11. **range 的 null 禁令**：若 `allowed_range_value.type=range`，所有区间端点必须为
    实际数值且不得为 `null`；`type=enum` 的离散候选允许包含 `null`。
12. **数值范围表达式**：禁止生成 `.range_value in [[min, max]]`；必须改写为
    `min <= param.range_value <= max` 或对应的单边/开区间不等式。
13. **空值枚举序列化**：若 `allowed_range_value.type=enum` 且原文的"空"表示未传值、
    缺省、空指针或 `nullptr`，候选必须是 JSON `null`，不得是字符串 `"空"`；只有
    原文明示零长度容器时才使用空容器候选 `[[]]`。**必选参数 + 原文"仅支持 nullptr"场景**
    下 `value` 应为单元素 `[null]`，`src_text` 含 `nullptr`/`空指针` 关键词（见 §6.32）。
14. **bool 参数 allowed_range_value 强枚举**：对所有 `type.value` 为 `"bool"` 的参数，
    `allowed_range_value.type` 必须为 `"enum"`，`value` 必须是 `[false]` / `[true]` /
    `[false, true]` 三者之一；禁止留 `value=[]` 或 `type="range"`（否则生成器按浮点
    范围填充会产生非法 bool 取值，触发 `create_dataset` 报告 `attr bool error`）。
15. **NZ 块尺寸硬约束**：若存在 5D NZ 张量（`format ∈ {"NZ","FRACTAL_NZ","FRACTAL_NZ_C0_16"}` 且 `dimensions.value=[5,5]`），
    必须满足**全部**下列子项：
    a. `mat2.allowed_range_value.value=[]`（空）或文档**显式约束元素取值**的端点；**禁止**为表达块尺寸而写 `[[16,16],[16,16]]` / `[[16,16]]`（块尺寸是 shape 约束，只落 `knowledge/aclnn/features/nz_matmul.md` §4.6.5 §C 的 `shape_equality`，见 `knowledge/aclnn/features/nz_matmul.md` §4.6.5 §D）；
    b. `constraints_in_parameters[每个支持平台]` 含 `mat2.shape[3] == 16` 与 `mat2.shape[4] == 16` 两条 `shape_equality`（或 `shape_value_dependency`）；
    c. 文档同时描述非转置与转置 NZ 两种布局时，两套 `shape[3]/shape[4]==16` 须**分别落库**为不同条目（共 4 条），`src_text` 摘录对应原文；
    d. 各 `shape_equality` 的 `src_text` 非空，且包含 `k0` / `n0` / `16` 等关键词。
16. **一段式算子一致性**：若 `function_signature` **不含** `GetWorkspaceSize`（一段式），必须满足**全部**：
    a. 函数名与 `operator_name` 一致（无 `GetWorkspaceSize` 后缀）；
    b. 标量指针输出（如 `uint64_t*`/`int64_t*`）在 `outputs` 中，`type.value` 去 `*`、`format.value="N/A"`、`dimensions.value=[]`、`is_operator_param.value=true`；
    c. 不得出现 `workspaceSize`/`executor`/`stream` 被误标为 `outputs` 流程参数。
    两段式算子的 `function_signature` 应含 `GetWorkspaceSize`。**不得**在 JSON 中写入 `is_single_function_mode` 字段——一段式判定由 `function_signature` 隐式表达，写入该字段会触发校验阻断。
17. **非 Tensor 数组类型禁用 `.shape`**：对所有 `type.value` 为 `aclIntArray` / `aclFloatArray` / `aclBoolArray` 的参数，`constraints_in_parameters` 的 `expr` 中**禁止**出现 `paramName.shape`（这些类型无 `.shape` 属性，执行/校验期对一维数组实例求值会触发 `AttributeError`）；其长度用 `len(paramName)` 表达（如 `2 <= len(tensorShape) <= 6`、`len(tensorShape) >= 1`）。仅 `aclTensor` / `aclTensorList` 允许 `.shape` / `.dtype` / `.format` 属性引用。
18. **条件 Shape 约束自检**：遍历所有 `inputs` 中 `type.value == "bool"`
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
    c. 必选参数且原文没有空值语义时禁止包含 `null`；**但原文写"当前仅支持输入 nullptr"/"仅支持传空指针"/"必须为空指针"/"仅支持空指针"时算"有空值语义"**，此时必选参数 `allowed_range_value` 应为 `[null]`（`type=enum`），`src_text` 摘含 `nullptr`/`空指针` 的原文；详见 §6.32；
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
23. **支持场景表与 Tensor rank 联动完整性**：若文档按 `groupType`、`splitItem`、
    单/多 Tensor 等离散条件给出目标 Tensor 的二维/三维等 rank 差异，必须满足：
    a. `dimensions.value` 保留文档允许 rank 的全集；
    b. 每个合法场景分支均存在引用门控参数与目标 Tensor 的条件 rank expr；
    c. rank 使用 `len(param.shape)` 表达，不得使用约束语言不存在的 `.dimNum`；
    d. 不得只保留无条件 rank 并集，使求解器在具体场景中任意选择非法 rank。
24. **format↔rank 完整性**：逐平台遍历所有 `aclTensor` / `aclTensorList` 参数；若
    `format.value` 是包含不同标准 rank 格式的列表，必须满足**全部**：
    a. 存在且仅存在一条引用该参数的 `format_rank_consistency` 约束；
    b. `format.value` 中除 `ND` 外的每种已知格式，都在该约束中有对应的精确
       `len(T.shape) == rank` 分支；`ND` 使用文档给出的显式 rank 枚举，并在关系表达式中按原文范围约束；
    c. `relation_params` 包含该参数，表达式同时引用 `T.format` 与 `T.shape`；
    d. 对表达式逐分支做反例检查：`NCDHW + 非5D`、`NDC1HWC0 + 非6D`、
       `FRACTAL_Z_3D + 非4D`、`NZ/FRACTAL_NZ + 非5D` 必须全部求值为 false；
    e. 对 `aclnnNpuFormatCast`，`srcTensor` 与 `dstTensor` 必须逐平台分别通过上述检查，
       禁止只为其中一个张量生成守卫。
25. **大小/数量语义参数的隐式 >0 约束**：遍历全部输入和输出参数的
    `description`，凡含"空间大小"/"占用空间大小"/"数据量"/"元素个数"/"的数量"/
    "个数"等表示"大小/数量/个数"语义短语的**标量取值参数**（非 shape/dtype/
    format/枚举/bool），必须满足**全部**：
    a. `constraints_in_parameters[每个支持平台]` 中存在 `P.range_value > 0` 条目
       （`expr_type=value_dependency` 或 `self_value_range`）；
    b. `allowed_range_value.value` 为 `[]`（未伪造 0 下界）；
    c. `relation_params` 仅含该参数自身；
    d. `src_text` 摘录 description 中"空间大小/数据量/个数"原文字句并补注
       "大小/数量语义隐含 >0"；
    e. 若文档已显式写明该参数取值范围并已落库对应约束，则不重复追加（见 `knowledge/aclnn/features/implicit_pos.md` §4.6.9）。
26. **公共互推导 / broadcast 知识展开自检**：重新扫描原文和参数
    `description`：
    a. 若出现 `互推导关系.md`、"数据类型推导规则"、"推导之后的数据类型保持一致"，
       必须存在引用相关输入和输出的 `type_dependency` 约束；
    b. 若输出 dtype 需要等于推导结果，`type_dependency` 必须排除推导结果不在输出
       `dtype.value` 中的输入组合，不能只保留各参数独立 dtype 枚举；
    c. 若出现 `broadcast关系.md` 或 "满足 broadcast 关系"，必须存在对应
       `shape_broadcast` 约束；
    d. 若输出轴写明 "经过 broadcast 推导后一致"，必须存在输出轴等于 broadcast 结果的
       `shape_value_dependency`；
    e. 若出现 "Reduce 维度需要与 ... 相等"，必须存在真实 Reduce 轴相等约束。
27. **产品相关参数取值范围差异自检**：逐平台遍历全部 `inputs`/`outputs`
    中 `is_operator_param.value=true` 的非张量标量/枚举参数 `P`；若文档"约束说明"
    按产品分节或调用示例表明 `P` 的候选值随产品分歧（某产品固定为占位值如 `-1`，
    另一产品为总表候选子集/全集），必须满足**全部**：
    a. `P` 在 `product_support` 每个平台都有 `allowed_range_value` 条目（与 §6
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
28. **空 `expr` 禁令与 `derived_value` 可求解性**：遍历
    `constraints_in_parameters` 各平台全部条目，必须满足**全部**：
    a. 每条 `expr` 为**非空**字符串，规范化后是可 `eval()` 的合法 Python 布尔
       表达式（违 §4.7.2、`knowledge/aclnn/common/expression_language.md` §语法）；
    b. **不得**出现 `expr=""` 的空壳条目；`expr_type="derived_value"` 条目**允许**
       存在，但其 `expr` **必须**是可求解的查找/派生表达式（`knowledge/aclnn/features/format_cast.md` §4.6.7），不得为空；
       若文档无确定映射，则不应产出 `derived_value` 条目（`knowledge/aclnn/features/format_cast.md` §4.6.7），派生语义由
       `[DERIVED]` description 承载；
    c. 文档约束无法形式化为 Python 布尔表达式（自然语言公式、broadcast 特殊
       dtype 不可靠形式化等）时，**不**产出 `constraints_in_parameters` 条目，
       改把语义记入相关参数 `description`/`src_text`（`knowledge/aclnn/features/broadcast.md` §B、§5、`knowledge/aclnn/common/expression_language.md` §语法 第 10 条）；
    d. 对 `aclnnNpuFormatCast`，当 `dtype_support_description` 含 actualFormat 确定映射时，
       `constraints_in_parameters` 中**必须**含可求解 `derived_value` 条目（如 A3/A2
       `actualFormat.range_value == dstFormat.range_value`），**不得**为 `expr=""` 空壳；
       dstTensor.format 亦须由映射派生（不得独立随机赋值）。
29. **格式转换算子 dtype 等式自检**：当算子 `function_explanation` 或正文
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
30. **dtype×format 交叉联合组合表自检**：当文档组合表（GetWorkspaceSize
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
       （`knowledge/aclnn/common/expression_language.md` §常用模式 模式 9「主接口联合组合表」）；`expr` 不得为空（违 §6.28）；
    c. 析取**必须覆盖全部合法行**：遗漏一行会使该组合下 dst 取值无约束、生成器随机
       赋值；多值映射（如某 dtype 对应两种 format）在该行用 `or` 表达；
    d. dtype 引用用 `knowledge/aclnn/common/platform_dtype.md` §dtype 受控字典名（`FLOAT`→`FLOAT32`）、format 引用用 `knowledge/aclnn/common/platform_dtype.md` §format 受控
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
31. **`type_equality` / `type_dependency` 分类自检**：逐平台遍历
    `constraints_in_parameters` 的所有 dtype 关系，必须满足：
    a. 标为 `type_equality` 的 expr 只能由参数 `.dtype` 间的 `==` 及用于
       连接多个无条件等式的 `and` 组成；
    b. `type_equality` expr 若出现 `.range_value`、`.shape`、`.format`、
       `is None`、`is not None`、`if/else`、否定门控或析取分支，必须改为
       `type_dependency`；
    c. 重新阅读每条 `src_text`；若含“当…时”、“对应场景”、“仅当”、
       “除…外”、“可以不一致”、“参数存在时”等条件语义，其 dtype
       关系必须标为 `type_dependency`；
    d. 专项反例检查：
       `(cacheModeOptional.range_value == "PA_NZ") or (...)` 和
       `optional is None or x.dtype == optional.dtype` 必须均为
       `type_dependency`，不得为 `type_equality`。
**聚合表达式求解器兼容性补充检查**：遍历所有 `expr`：
    a. 不得出现 `sum(... for ... in ...)`、`sum(... for ... in zip(...))` 或
       `sum([comprehension])`；当前生成器只支持 `sum(param.range_value)` 这类
       对完整数组的直接求和；
    b. 对 `reduceSum(A[i] - B[i])` 等线性聚合，必须改写为
       `sum(A.range_value) - sum(B.range_value)`，并保留原有门控与容量不等式；
    c. 不得用 `range(min(len(A), len(B)))` 静默截断较长数组；只在文档
       明确要求等长时另建长度约束；
    d. `aclnnScatterPaKvCache` 专项形式必须为
       `(seqLensOptional is None or compressLensOptional is None) or `
       `(sum(seqLensOptional.range_value) - sum(compressLensOptional.range_value) <= `
       `num_blocks.range_value * block_size.range_value)`，不得产出索引生成器或
       `zip()` 生成器。

32. **必选参数"只支持 nullptr"取值语义自检**：遍历全部 `inputs`/`outputs`
    中 `is_optional.value=false` 且原文/description 含"仅支持输入 nullptr"/"当前仅支持输入
    nullptr"/"只支持传空指针"/"必须为空指针"/"仅支持空指针"等短语的参数，必须满足**全部**：
    a. `allowed_range_value` 为 `{"value":[null], "type":"enum", "src_text":"<含 nullptr/空指针 的原文>"}`，不得为 `value=[]`、不得为 `type=range`、不得把 `null` 写成字符串 `"null"`/`"空"`；
    b. `allowed_range_value.src_text` 必须包含 `nullptr`/`空指针`/`未传`/`缺省`/`支持空`/`可为空`/`配置空` 之一，以通过 `validate_artifacts.py` 的 `_EXPLICIT_NULL_RE` 放行（必选 + enum 含 `null` + `src_text` 无关键词会被拦截，见 `scripts/validate_artifacts.py:394-448`）；
    c. `is_optional.value=false` 保持不变，参数仍为必填；**不得**在 `constraints_in_parameters` 追加 `param is None` 条目（生成器对必选参数强制 `is_present=True`，会使该 expr 归约为 `z3.Not(True)=False` 导致 UNSAT，且 `param is None` 编码"缺席语义"与"取值语义"冲突）；
    d. `dtype.value=[]`（见 §4.6 dtype 回填规则）；
    e. 若除 `nullptr` 外原文还允许其他取值（如"支持传 nullptr 或 [0,1]"），`value` 写成混合候选 `[null, [0,1]]`（`type=enum`），`src_text` 摘录完整原文段；
    f. 典型正例：`aclnnSwinAttentionScoreQuant` 的 `paddingMask2Optional`（参数表"输入/输出"列为"输入"，使用说明"当前仅支持输入nullptr"）→ `is_optional.value=false`、`dtype.value=[]`、`allowed_range_value={"value":[null], "type":"enum", "src_text":"当前仅支持输入nullptr"}`。

33. **dtype / value 候选逐字一致性自检**：逐平台遍历全部 `inputs`/`outputs`
    参数及 `constraints_in_parameters` 条目，必须满足**全部**：
    a. `value` 数组中的每个 dtype/format 字符串逐字复制自文档原文 / 该条目
       `src_text`，仅允许 `knowledge/aclnn/common/platform_dtype.md` §dtype / §format 登记的规范化映射（`BF16`/`bfloat16`/`bf16`→`BF16`，
       `float`/`Float`/`FLOAT`→`FLOAT32`）；禁止缩写、漏字母、字母替换、大小写改写、
       词干截断或意译；
    b. **条目内自洽**：同一条目 `src_text` 中出现的每个 dtype/format token 必须能在
       `value` 数组中找到逐字一致的对应项，反之 `value` 每个元素也必须能在 `src_text` /
       文档原文中找到逐字一致来源；`src_text` 记 `HIFLOAT8` 而 `value` 写 `HFLOAT8`
       （或反之）即为提取错误，必须修正；
    c. 文档原文 dtype token 不在 `knowledge/aclnn/common/platform_dtype.md` §dtype 受控字典时，原样保留该 token、`src_text` 摘录原文、
       并在该参数 `description` 末尾补注 `[DICT_GAP:<token>]`；此为字典缺口，不属誊写
       错误，不受 §6.4「dtype 必须来自 `knowledge/aclnn/common/platform_dtype.md` §dtype」的拒绝；
    d. 同样检查 `allowed_range_value.value` 的字符串枚举候选、`format.value` 的格式串，
       以及 `dtype_support_description` / `format_support_description` 中的
       combo 表 dtype/format 值；发现不一致即重做该条目。

34. **条件布局门控自检**：遍历每个平台 `constraints_in_parameters` 中所有
    `shape_value_dependency` / `shape_equality` / `shape_choice` 条目；凡同一 expr
    引用同一 weight/x 参数的两种 shape 顺序并以无前提 `or` 连接（如
    `(x.shape[1]==weight.shape[0]...) or (x.shape[1]==weight.shape[1]...)`），且文档
    同时写明布局切换条件时，不得保留无前提 `or` 析取。必须使用真实 API/场景参数门控；
    只有用例模型与 executor 都能物化隐式状态时才可引入隐式 bool。执行能力只能覆盖
    单一布局时，将收窄写入专项 `TEMP_CAPABILITY_GUARD`，不得篡改文档合法域。

35. **条件性取值/存在性关系自检**：重新扫描全部参数 `description`、`src_text` 与
    文档「约束说明」原文，凡含「A 取 v 时 B …」「B 仅在 A=w 场景支持」「A=v 时 B
    必须传空/不取某值」者，**必须**存在引用 A 与 B 的
    `cross_param_constraint`（或 `value_dependency` / `presence_dependency`）expr；
    只在 `description` / `allowed_range_value.src_text` 自然语言备注而未形式化者，
    视为漏抽，必须补产 `expr` 条目（见 §4.7.3 item 15）。若仅写“失效/不影响计算”，
    先确认是否真有限制；没有“必须传空/限定取值”原文时不得擅自收窄合法域。

36. **可选参数语义与临时能力护栏回扫**：遍历所有 `is_optional.value=true` 参数上
    形如 `param is None or (...)` 的约束（`type_dependency` / `shape_equality` /
    `shape_value_dependency` / `cross_param_constraint` 等含 `param is None` 前置析取支）；
    确认 presence/absence 是否有文档或所选场景的直接证据。不得根据一次 cases_executor
    的构造行为把可选参数提升为必选。若 `[null]` 仅因生成能力不足而设置，必须位于精确
    operator/feature 知识的 `TEMP_CAPABILITY_GUARD`，并包含解除条件；base 和文档合法域
    仍保持参数可选。能力恢复后必须删除护栏并补跑 present/absent 两分支回归。

37. **场景选择与 Optional 缺席落地自检**：按以下三态逐参数检查场景指令：
    a. 用户明确选择的参数，按选择值 `fix` 或按选择子集 `expand`；
    b. 用户未明确选择的参数，继续根据算子文档和已选场景提取、适配，不能把
       `param_modes` 缺键解释为删除 presence 或自动缺席；
    c. 已选场景或文档明确禁止的 Optional 参数 `P`，必须在每个支持平台生成独立的
       `presence_dependency` 条目，`expr="P is None"`、`relation_params=["P"]`，并以
       禁止输入原文作为 `src_text`。不得只写自然语言备注，不得依赖“未产出 presence
       约束”隐式屏蔽。多场景同时保留时使用场景条件门控；已收窄到单一场景时可化简为
       无条件 `P is None`。

## 7. 调用模板


下面给出一份**可直接复制**的 prompt 调用片段：

```text
# System
你是一名昇腾 CANN 算子约束抽取专家。
请严格遵循本提取提示词（§0-§7）的所有规则，并参考知识库：
- 解析 shape/dimensions 时参考 `knowledge/aclnn/common/dimensions.md` §解析表
- 识别隐式维度变量时参考 `knowledge/aclnn/common/implicit_parameters.md` §隐式参数（概念词/操作名/类型词需剔除）
- 处理 NZ / FRACTAL_NZ 张量时参考 `knowledge/aclnn/features/nz_matmul.md` §4.6.5（块尺寸硬约束、转置/非转置布局区分）
- 处理多格式 Tensor 时参考 `knowledge/aclnn/features/format_cast.md` §4.6.7 与 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 8；必须生成逐格式
  `format_rank_consistency` 守卫，尤其禁止 `NCDHW + 非5D`
- 识别条件 Shape（被 enum/boolean 门控的 shape）时参考 §4.6.3 G 与 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 6
- 对含 `self_transposed` / `mat2_transposed` 隐式 bool 的 NZ 算子，`shape_value_dependency`
  必须参考 `knowledge/aclnn/operators/batch_matmul_weight_nz.md` §D+ 与 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 6.1 按隐式 bool 门控
- 处理 aclTensorList 容器长度关系时参考 §4.6.3 TensorList 长度规则与 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 0
- 处理 backward / grad 的 gradOutput partial-shape 跟随时参考 `knowledge/aclnn/features/backward_partial.md` §4.6.6 与 `knowledge/aclnn/common/expression_language.md` §常用模式 模式 7
- 处理大小/数量语义参数的隐式 >0 约束时参考 `knowledge/aclnn/features/implicit_pos.md` §4.6.9
- 处理派生输出张量（CalculateSizeAndFormat 类子接口）时参考 `knowledge/aclnn/features/format_cast.md` §4.6.7；当文档存在
  确定映射时 `derived_value.expr` 必须编码为可求解表达式（`knowledge/aclnn/common/expression_language.md` §常用模式 模式 9），不得为空串
- 处理格式转换算子时参考 `knowledge/aclnn/features/format_cast.md` §4.6.7；当 dtype 表每行 src.dtype == dst.dtype 时必须
  产出 `type_equality` 等式约束
- 文档引用 `互推导关系.md` 或 `broadcast关系.md` 时参考 `knowledge/aclnn/features/broadcast.md` §4.6.10（推导表与广播规则
  已内联于该节）
- 写 expr 表达式时参考 `knowledge/aclnn/common/expression_language.md` §常用模式 模式库（按关系特征匹配模板；NZ 块尺寸使用模式 5；
  条件 Shape 使用模式 6；shape_value_dependency 隐式 bool 门控使用模式 6.1；
  Partial-Shape 使用模式 7；TensorList 长度相等使用模式 0；派生值查找使用模式 9）
- 写 allowed_range_value 时参考 `knowledge/aclnn/common/allowed_range.md` §映射表
- dtype / format / 枚举候选必须与 src_text / 文档原文逐字一致（见 §4.6.3
  「dtype / value 候选逐字一致性硬规则」与 §6.33）；文档 token 不在 `knowledge/aclnn/common/platform_dtype.md` §dtype 时
  原样保留并标 `[DICT_GAP:<token>]`，不得改写为字典内形似项凑数

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
2. 按 §3 输出对象合同（`OperatorRule`）输出 JSON；
3. 内部执行第 6 章全部自检项（含 §6.15 NZ 块尺寸、§6.16 一段式一致性自检、§6.17 非 Tensor 数组禁用、§6.18 条件 Shape 与 shape_value_dependency 门控完整性、
   §6.19 TensorList 长度关系、§6.20 动态取值边界、§6.21 Partial-Shape 自检、§6.23 支持场景表→rank 联动、§6.25 大小/数量语义隐式 >0、§6.26 公共互推导/broadcast 知识展开、
   §6.28 derived_value 可求解性、§6.29 格式转换 dtype 等式、§6.30 联合交叉 dtype/format 组合表、§6.32 必选参数"只支持 nullptr"取值语义、§6.33 dtype/value 逐字一致性、§6.34 条件布局门控、§6.35 条件存在性关系、§6.36 临时能力护栏）；
4. **仅返回 JSON 字符串**，不要包含任何解释、代码块标记或额外文字。
```

---

> **附录迁移说明**：历史变更记录（原附录 B）已移至 `prompts/CHANGELOG.md`；10 个典型算子对齐示例（原附录 A）已移至 `prompts/examples.md`。两份文件**不参与约束提取**，仅作维护参考，本提示词加载时不含其内容。

> **生成器层 null 语义 gap**（extractor 不直接执行，属 GENERATE/EXECUTE 层对齐缺口，
> 含 5 条 file:line 分析与 `is_null` 建模改造关键点）已移至
> `docs/GENERATOR_NULL_SEMANTICS_GAP.md`；本提示词仅在 §6 自检侧保留对应约束
> （"必选参数只支持 nullptr"取值语义）。
