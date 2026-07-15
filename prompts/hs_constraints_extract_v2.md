# torch_npu 海思算子约束提取提示词 v2

本提示词适用于 `torch_npu.*` 海思融合算子。输出仍必须严格符合项目
`agent/generators/common_model_definition.py::OperatorRule` schema；通用字段、平台、
dtype 命名和表达式语法遵循 `prompts/operator_constraints_extract_v4.md`。以下规则覆盖
其中仅面向 aclnn 的假设。

> **v2 变更摘要**（详见 `hs_constraints_extract_v2_changes.md`）：在 v1 基础上并入
> 多轮真实 NPU 反馈驱动的 7+1 类缺陷修复规则：(1) 张量秩结构化——`dimensions.value`
> 必填、layout→轴语义映射、显式 `len(x.shape)==N` 秩约束双保险、dtype 保护；(2) 按
> 文档声明类型分类参数——文档标 `Tensor` 的参数（含可选/预留/"仅支持 None"）必须
> 保留张量通道、张量槽位按签名顺序位置对齐、scalar 通道不被误升；(3) 跨参数形状联动
> 约束——配套约束以"参数 X 某维 == 参数 Y 某维"或"固定值"形式落库；(4)
> `presence_dependency` 表达——optional tensor 在非启用场景为 None、presence 用
> `presence_dependency` 不用 `allowed_range_value`；(5) 枚举值与默认值隔离——"仅支持
> X" 的 `allowed_range_value` 不混入默认 0/None；(6) 约束可求解性——禁无界序列量词、
> 禁变量%变量非线性取模。原 v1 章节（核心原则 1-6、必做提取、六类场景、自检 8 项）全
> 部保留。**不**针对单算子、不硬编码算子名/参数名。

## 核心原则

1. 函数原型是参数顺序、参数种类、可选性和默认值的最高优先级来源。
   `*` 之前是必选位置参数；有 `=...` 的参数一律 optional。文档正文不得把 optional
   参数误写成 required。
2. `torch_npu.*` 是 PyTorch E2E API，`operator_name` 保留完整点号名称，
   `api="pytorch"`，不要伪造 `aclnn_name` 或 ACLNN workspace 接口。
3. 先提取场景/模式，再提取参数。layout、量化模式、稀疏模式、page attention、
   Prompt/Decode、RoPE/MLA 是场景开关，必须用 presence/type/shape/value dependency
   将同一场景的参数绑定，禁止独立随机组合。
4. 文档给出的调用示例是合法 baseline，但不能覆盖正文约束。示例中的符号维度
   B/S/T/N/D/Dr/H 等应建立共享维度变量和 shape equality/binding。
5. 默认值是一个确定值，不是随机范围。生成阶段必须把 scalar attr 实例化为单值；
   禁止在最终 case 的 scalar `range_values` 中保留 `[min,max]`。
6. 输出 JSON 使用项目内部类型名，而不是直接照抄 Python 标注：`Tensor`、
   `Optional[Tensor]`、`torch.Tensor` 一律写 `aclTensor`；`List[Tensor]` 写
   `aclTensorList`；`List[int]` 写 `aclIntArray`；`List[float]` 写
   `aclFloatArray`；`List[bool]` 写 `aclBoolArray`；`str` 写 `string`。
   optional 性只由 `is_optional` 表达，不得把 `Optional[...]` 留在 `type.value`。
7. **文档声明的参数类型是张量还是标量，属性的属性**（v2 新增）：函数原型与参数
   说明中标注为 `Tensor` / `Optional[Tensor]` / `torch.Tensor` 的参数一律按张量通道
   建条目（见后续"按文档声明类型分类参数规则"）；标注为 `double` / `int` /
   `int64` / `string` / `bool` 等的参数一律按 scalar attr 通道建条目（见 §K）；
   **不得**根据"该参数被标注为可选"/"该参数仅支持默认值 None"/"该参数为预留"等修
   饰语把 Tensor 降级为 scalar，也不得因参数被声明为 scalar 而误升张量。

## 必做提取

- 产品支持差异按平台分别保存，特别是 A2/A3 不支持的 fp8/hif8/mxfp8 模式。
- 每个 tensor 的 dtype、format、rank、是否连续、空 tensor 支持情况。
- layout 到 rank/轴语义的完整映射；复合 layout（例如输入/输出 layout 不同）必须保留，
  不得凭名称猜测为非法值。
- optional tensor 的启用条件和互斥条件；未启用时必须为 None（presence 用
  `presence_dependency` 显式表达，详见后续 §M）。
- 量化 mode 与 scale/offset dtype、shape、presence 的联合约束。
- sequence length 与 tensor S/T 轴、batch、block table、block size 的关系。
- 所有整数整除、对齐、前缀和、有界索引、最大内存相关约束。
- 返回 tensor 只写入 outputs，不得混入 inputs。
- 跨参数形状联动约束（如某 layout 下 sparse_indices.shape[0]==query.shape[0]、
  sparse.shape[1]==1、sparse.shape[2]==KV_N 等）必须按"配套约束"显式落库，不得仅
  在 `src_text` 中带过。
- 枚举参数"仅支持 X"时，`allowed_range_value.value` 只列 X；默认值 0/None 由
  `is_optional` 与 `default` 字段单独表达，**不**进枚举。

## 张量秩结构化规则（v2 新增，通用）

> 本节规则按"字段就绪度"语义触发，**不**按算子名或具体参数名硬编码。
>
> **反面教训 / 真实 NPU 报错**：v1 提取的 tensor 结构化 `dimensions` 字段被整体留空
> （`dimensions.value == []`），生成器 `param_constraint_utils.py::build_param_shape_len_constraint`
> 唯一读取该字段生成 `len(param.shape)==N` 秩约束——空值时该参数被 `continue` 跳过、
> 不产生秩约束，于是 Z3 自由采样张量秩产出 1D/5D/7D/8D 等畸形 shape（典型：
> `RuntimeError: Tensor sparse_indices is empty` / `EZ9999 block_table dim=1` 形
> 状级 NPU 报错）。只在 `src_text`/`description` 里写 shape 文字不算数，机器不消费。

### A. 每个 tensor 类型参数必须填充结构化 `dimensions.value`

- 对每一个 `type.value == "aclTensor"`（含 `aclTensorList`）的输入参数与每一个
  tensor 类型的输出，在该参数结构内的 `dimensions.value` 填入其**秩**：
  - 秩唯一时填整数（如 `2`）；
  - 秩因 layout / 场景不同而有多种合法取值时，填**允许秩列表**覆盖所有分支
    （如 `[3, 4]`）。
- `dimensions.value` 是生成器生成 `len(param.shape) == N`（int）或
  `len(param.shape) in [...]`（list）秩约束的**唯一机器可消费字段**。留空会
  导致该张量秩被 Z3 自由采样、产出畸形 shape，属高危缺陷，**禁止发生**。
- `dimensions.value` 的秩必须与文档 shape 契约（正文 / 返回值说明）一致；
  `src_text` 仍保留原文摘录以便追溯，但不得以 `src_text` 有 shape 文字为由把
  `value` 留空。

### B. `dimensions` 秩必须与 layout→轴语义映射一致

- 建立 layout 名到轴序列的映射，秩 = 轴数量：
  - `BSND = [B, S, N, D]` → 秩 4；
  - `TND = [T, N, D]` → 秩 3；
  - `PA_BSND = [block_num, block_size, N, D]` → 秩 4；
  - 其它 layout 依同法按其轴序列定秩。
- 参数在多个受支持 layout 下秩不同的，用**允许秩列表**覆盖所有分支，例如：
  - 某 query 支持 `BSND`（秩 4）/ `TND`（秩 3）→ `dimensions.value = [3, 4]`；
  - 某 key 支持 `PA_BSND`（秩 4）/ `BSND`（秩 4）/ `TND`（秩 3）→
    `dimensions.value = [3, 4]`；
  - 明确写为"N 维 tensor"的（如"shape 为 2 维"、"长度为 B 的一维 tensor"）
    分别取 `2`、`1`。
- 允许秩列表必须是所有受支持分支秩的并集，不遗漏也不臆造未支持的秩。

### C. 追加显式秩约束作为双保险（闭合负索引伪满足退路）

- 仅有 `dimensions` 尚不足以阻断"负索引在低秩被伪满足"的退路：`shape[-1] == C`
  在 1D 张量中退化为唯一元素恒真、`shape[-2] == C` 在 1D 中左侧不存在被空真
  满足。因此在 `constraints_in_parameters` 追加显式秩约束条目 `len(x.shape) == N`，
  与 `dimensions` 形成双保险：
  - 秩随 layout 分支变化的，用条件绑定表达，例如
    `((layout_x.range_value == "BSND") and (len(x.shape) == 4)) or
    ((layout_x.range_value == "TND") and (len(x.shape) == 3))`；
  - 秩唯一的直接写 `len(x.shape) == N`（如 `len(block_table.shape) == 2`、
    `len(actual_seq_len.shape) == 1`）。
- 该显式秩约束仍须遵守本提示词"约束可求解性规则"：**不得**引入无界序列量词
  （`for i in range(...)` / `all(...)` / `any(...)`），**不得**引入"变量 %
  变量"非线性取模。仅用 `len(x.shape)`、常量、`==`/`and`/`or` 构造。

### D. 非 tensor 的 scalar / 枚举参数不填 `dimensions`

- `type.value` 非 tensor（`double`/`int`/`string`/`aclIntArray` 等 scalar 或枚举）
  的参数**不需要**秩，`dimensions` 保持空/`None`，**不得**误加秩列表。秩语义
  只对张量成立；给 scalar 加 `dimensions` 会污染下游。

### E. dtype 保护

- 每个 tensor 类型参数的结构化 `dtype.value` 字段必须按文档填全（如 `int32` /
  `int8` / `bfloat16` / `float16` 等），且**不得被本节 shape / 秩规则波及或弱
  化**。dtype 与秩是相互独立的两个通道，填充 `dimensions` 时不得改动、删除或
  降级已正确的 `dtype` 字段。
- 说明：若下游 case 的 dtype 出现回退（如整型张量被写成 fp16），根因在生成器
  dtype 消费路径，**不属**本提示词职责；本提示词只负责把 `dtype.value` 结构化
  填正确并保持。

## 按文档声明类型分类参数规则（v2 新增，通用）

> 本节规则按"文档声明的参数类型"语义触发，**不**按算子名或具体参数名硬编码。
>
> **反面教训 / 真实 NPU 报错**：当文档把某个张量参数描述为"可选"、"预留"、"仅
> 支持默认值 None" 时，提取者因"它看起来不被使用"把它降级为 `type=double/int/
> string/None` 等 scalar / 缺省形态，导致下游 TTK E2E 适配层按 API 函数原型
> 中张量参数**签名顺序**做位置化张量槽位映射时该张量槽空缺、**后续所有张量
> 参数的整体位置错位**。典型真实 NPU 报错：在签名较后位置的某张量参数处出现
> `EZ9999 the dim num of <tensor_param> is 1, it should be 2`——即该张量收到
> 的是上游因签名顺序错位落到了本位置的另一张量的 1D 数据。

### F. 文档声明为 `Tensor` 的参数必须保留张量通道

适用范围：**函数原型 / 参数说明**中明确把参数类型标注为 `Tensor` /
`Optional[Tensor]` / `torch.Tensor`（或其语义等价形式）的全部输入与输出参数，
含以下三种修饰形态——修饰语**不**改变其张量本性：

1. **必选张量**：函数原型中无 `=...` 默认值的 `Tensor` 参数；
2. **可选张量**：`Optional[Tensor] = ...`、`Tensor = None`、`... = None` 等带
   默认值的 `Tensor` 参数；
3. **预留张量 / 仅支持默认值 None 的张量**（"预留参数，仅支持默认值"、
   "reserved parameter, only default value supported"、"仅支持默认值 None"、
   "暂未支持" 等语义），其在原型中仍以 `Tensor` 类型出现，仅是当前软硬件 /
   算子实现版本下其运行时取值被限制为 None。

凡落入上述三类的参数，必须同时满足下列建模要求：

- `type.value = "aclTensor"`（或其数组型 `aclTensorList` 等张量家族），**严
  禁**改为 `double` / `int` / `int64` / `string` / `bool` / `aclIntArray` 等
  scalar 或非张量类型；类型判定**优先于**"该参数是否被使用"、"其 `description`
  是否说 'reserved/optional'"等修饰语。
- `is_optional.value`：
  - 必选张量填 `"false"`；
  - 可选张量 / 预留张量一律填 `"true"`（含"仅支持默认值 None"的预留张量），
    **不得**因为描述语是"预留"而把 `is_optional` 设为 `"false"`。可选性只
    由 `is_optional` 表达，不得把 `Optional[...]` 留在 `type.value`（与核心
    原则 6 一致）。
- `dimensions.value` 按 §A / §B 填秩：
  - 该张量在文档其它行 / 调用示例 / 约束说明中出现明确 shape 时，按其秩或
    秩并集填（int 或 [秩, ...]）；
  - 该张量在文档中**仅**被描述为"预留参数，仅支持默认值 None"而**未**给出
    形状时：保留 `type=aclTensor` 与 `is_optional=true` 不变，但
    `dimensions.value` **禁止**作为降级依据——若选不到合理秩，应按 §A 要
    求给出占位秩（典型做法与该算子其它同族张量的秩对齐，如一维占位 `1` /
    与其紧邻的张量维度族对齐），并在 `src_text` 与 `description` 摘录"仅
    支持默认值 None / 预留"原文，**严禁**以此把 `type` 改为 scalar；
  - 严禁的反例：把"仅支持 None"Tensor 写成 `type=double / NoneType / scalar`，
    即使该参数在当前实现下运行时缺省、永远传 None，也必须保留为张量条目。

### G. "仅支持 None 的可选/预留张量"的正确表达方式

对于文档描述为"预留参数，仅支持默认值 None"、"reserved, only None"、"暂不
支持非 None 取值"等仅以 None 作为唯一合法运行时值的可选张量，按下列规范建模：

- **`ParamAttributes` 字段**：
  - `type.value = "aclTensor"`（按 §F 强制）；
  - `is_optional.value = "true"`（按 §F 强制）；
  - `dimensions.value` 按 §F 与 §A / §B 处理：能取到秩则取秩，否则给占位
    秩并在 `src_text` 与 `description` 标明；
  - `dtype.value` 按文档对 Tensor 的 dtype 声明填写（若文档未给出 dtype，
    则按该算子其它同类张量的 dtype 给出占位枚举，或留空但**不**把空值
    解读为"该参数非张量"）；
  - `format.value` 取文档声明（一般是 `ND`）；
  - `allowed_range_value.value = []`（**不**在 `allowed_range_value` 里
    枚举 `None`；None 是 presence 语义，不是取值枚举）；presence/缺省由
    `is_optional=true` 配合 `presence_dependency` 表达（见 §M）；
  - `description` 与 `src_text` 必须摘录"预留参数，仅支持默认值 None"/
    "reserved parameter"等原文，便于人工复审与回溯。
- **`constraints_in_parameters` 表达（presence 与场景绑定）**：
  - 若文档未声明任何启用条件（"仅支持 None" 即"任何场景下都缺省"），可不
    写额外 `presence_dependency`，仅靠 `is_optional=true` 与可选张量槽
    默认输 `None` 占位即可；
  - 若文档对该张量在不同场景/模式（如不同量化模式、layout、attribution 模
    式）下是否启用有任何隐含区别，按 §M `presence_dependency` 用
    `expr_type` 显式表达；
  - **禁止**把"该参数仅支持 None"反推为"该参数不是张量"——见 §F 高危反例。

### H. TTK E2E 张量槽位的位置对齐后果（高危根因）

本节是 §F / §G 的**运行后果**侧说明，**不**新增字段要求，仅以通用术语提醒：

- 执行适配层（TTK E2E）按 API 函数原型中的张量参数**签名顺序**做位置化张
  量槽位映射：对原型的张量参数按出现顺序编号 `Tensor_1, Tensor_2, ...,
  Tensor_N`，然后把约束 `inputs` / `outputs` 中按位置拉出 N 个张量条目一
  一对应填充。
- 任何函数原型里声明的 `Tensor` 参数，如果被错误建模为 scalar/NoneType/
  attr 或被遗漏，约束侧的张量条目数就会从 N 降到 N-1 或更少，下游适配
  层会把第 N 个槽位的 Tensor 拉到第 N-1 个槽位，原本应填第 N 个槽位的
  数据因此整体左移；典型 NPU 报错侧记：在签名较后位置的某张量参数处出现
  `EZ9999 the dim num of <tensor_param> is 1, it should be 2`，即为该
  参数被位置错位地填入了一个原本属于另一张量的 1D 数据（更前位置张量的
  None 缺省信号又被该参数收到）。修复路径**唯一**地落在约束提取：函数
  原型中**每一个**张量标注**必须**有对应张量条目，`type` 通道不可被
  scalar 化，`is_optional` 必须按文档 `Optional` 标注准确表达。

### I. scalar / 字符串参数保持 scalar 通道（与 §F 互为反命题）

与 §F 互为反命题：所有类型为 `double` / `int` / `int64` / `string` / `bool`
/ `aclIntArray` / `aclFloatArray` / `aclBoolArray` 等**非张量**类型的参数
必须保留为 scalar attr 或数组型 scalar：

- 严禁把文档声明为 `double` / `int` / `str` 的参数误升为 `aclTensor`（例如
  看到 `pre_tokens = 2^63-1` 之类的可选整数就误升为 1D 张量）；
- 严禁为其填 `dimensions.value`（§D 同样适用此条）；
- 仅在 `allowed_range_value` 中表达取值范围、`type` 字段写其 scalar 类型；
- 该条与 §F 互为反命题：本小节防"标量被误升张量"，§F 防"张量被降级为标
  量"，两者共同保证"函数原型声明的类型 ↔ 约束 `type` 字段"的通道一致。

## 跨参数形状联动约束（v2 新增，通用）

> 本节规则按"配套约束以'参数 X 某维 == 参数 Y 某维'或'固定值'形式出现"语
> 义触发，**不**按算子名或具体参数名硬编码。
>
> **反面教训 / 真实 NPU 报错**：v1 提取把"sparse_indices 的某维 == query
> 的某维 / == 1（KV_N）"等配套约束漏抽，下游 Z3 求解器对各张量 shape 独
> 立漂浮，NPU 真实报错
> `sparse_indices layout is X, shape is [got], expected shape is [exp]` 中
> 的 `expected shape` 列出的形状由这些联动约束定义，缺抽即直接 NPU 形状
> 违规侧记。

### J. 联动约束必须显式落库

- 配套约束常以"参数 X 的某维 == 参数 Y 的某维"形式出现（如
  `sparse_indices.shape[0] == query.shape[0]`、BSND 时
  `sparse.shape[1] == query.shape[1]` 与 `sparse.shape[2] == 1`（KV_N）、
  TND 时 `sparse.shape[1] == 1`）。它们是定义"expected shape" 的源头，必
  须**显式**落进 `constraints_in_parameters`。
- 任何"参数 X 某维 == 固定常量"的绑定（如 `sparse.shape[2] == 1` 中的
  常量 `1`，或文档中"D 取值固定为 576"等）按 `shape_value_dependency` 或
  `value_dependency` 落库；`relation_params` 必须包含 X 与 Y 或常量所在
  参数，`src_text` 必须摘录原文。
- 联动约束与"张量秩"是不同维度：秩由 §A / §B / §C 控制"几个轴"，联动约
  束控制"每个轴取什么值"；二者必须**同时**落库，否则 Z3 会在 4D 张量
  上自由给各轴赋值而无视配套语义。

## presence_dependency 表达规则（v2 新增，通用）

> 本节规则按"optional tensor 在某些 layout / 模式下才存在"语义触发，**不**
> 按算子名或具体参数名硬编码。
>
> **反面教训 / 真实 NPU 报错**：v1 提取把"某 optional tensor 仅在
> `layout_kv == PA_BSND` 时存在、其它 layout 应为 None"的语义漏抽为
> `allowed_range_value`，下游 NPU 报
> `CheckBlockTable: layout_kv=TND 时 block_table 应=null`，即非启用场景下
> 该张量被实际传值，NPU 端在校验阶段拦截。

### K. presence 用 `presence_dependency` 显式表达，不用 `allowed_range_value`

- optional tensor 在某些 layout / 模式 / 场景下才存在（如
  `block_table` 仅在 `layout_kv == "PA_BSND"` 时存在、其它 layout 应为
  None），必须建模为 `presence_dependency`，例如
  `(layout_kv.range_value == "PA_BSND") or (block_table is None)`。
- 严禁把"该参数在某些场景下为 None"语义塞进 `allowed_range_value`——
  `allowed_range_value` 是取值域枚举（`type="enum"` / `type="range"`），
  `None` 是 presence/缺省语义，混入会污染 Z3 域并被生成器静默丢弃。
- 若文档未声明任何启用条件（"仅支持 None" 即"任何场景下都缺省"），可不
  写额外 `presence_dependency`，仅靠 `is_optional=true` 与可选张量槽默
  认输 `None` 占位即可（与 §G 配合）。

## 枚举值与默认值隔离规则（v2 新增，通用）

> 本节规则按"枚举参数'仅支持 X' 时不允许默认值 0/None 混入合法枚举"语义
> 触发，**不**按算子名或具体参数名硬编码。
>
> **反面教训 / 真实 NPU 报错**：v1 提取把"某枚举参数仅支持值 2，但描述含
> '默认值为 0'" 写成 `allowed_range_value.value=[0, 2]`，下游生成器
> 自由赋 0，NPU 真实报错 `attention_mode should be 2, got 0`——默认 0 错入
> 枚举导致 NPU 端在校验阶段拦截。

### L. 枚举值不混入默认值

- 文档"仅支持传入 X" 时，`allowed_range_value.value` **只列 X**；默认
  值 0/None 由 `is_optional` 与文档 `default` 字段单独表达，**不要**因"有
  默认值"把 0/None 加入合法枚举。
- 同样适用于"仅支持传入 [X1, X2, ...]" 的有限枚举：候选列表严格按文档
  声明列举，不擅自扩展。
- 若文档同时声明"默认值为 D"且"合法值 ∈ {X1, X2, ...}"且 D ∉ {X1,
  X2, ...}，按 §C 哨兵处理：把 D 显式列在 `allowed_range_value.value` 候
  选里（`type="enum"`）作为合法离散候选之一；同时在 `is_optional` 标
  `true` 表达"不传即默认 D" 的语义。**严禁**写"`D or in {X1, X2, ...}`"
  这种绕过文档合法域的恒真等式。

## 约束可求解性规则（v2 新增，通用）

> 本节新增背景：v1 提取的约束虽忠实原文且可满足，但其中两类表达式在下游
> Z3 求解器上落入不可判定片段——一类触发 MBQI/序列理论 + 全称量词
> （ForAll），一类触发非线性算术（mod）。两类叠加后单条 dtype 检查耗时数
> 分钟，整体求解 60s 超时、0 用例产出。根因不是约束过紧或矛盾，而是
> **约束表达方式**。本节规定两类 Z3 难解形式的等价可求解写法；规则按
> "约束表达方式"语义触发，**不**按算子名硬编码。

### M. 禁止无界序列量词；shape 相等用直接 `==`

**禁止**对无界 Z3 序列使用 `for i in range(...)` / `all(...)` / `any(...)`
/ 推导式表达形状相等或逐元素相等，包括但不限于：

```text
# 反例 1（输出 shape 与 query shape 逐轴相等，原文"输出shape与入参query的shape保持一致"）
(len(output.shape) == len(query.shape)) and all(
    output.shape[i] == query.shape[i] for i in range(len(query.shape)))

# 反例 2（任意 all(x.shape[i] == y.shape[i] for i in range(...)) 变体）
all(A.shape[i] == B.shape[i] for i in range(len(A.shape)))
```

**等价可求解写法**：直接对 Z3 序列使用 `==`（框架内置对 `Seq.__eq__` 的
语义支持）：

```text
# 改写 1（推荐，shape 完全相等）
output.shape == query.shape

# 改写 2（rank 相等 + 逐轴相等用 len 与序列 == 联合表达；不出现 range/all）
(len(output.shape) == len(query.shape)) and (output.shape == query.shape)
```

**适用范围**：`expr_type=shape_equality` 及任何含 `*.shape[i] for i in
range(...)` 推导式的条目。判断标准：若 expr 文本包含 `for i in range(...)`
或 `all(...)` / `any(...)` 包裹形状/张量序列，则必须改写。

### N. 禁止"变量 % 变量"非线性取模；按枚举展开或改用常量取模

**禁止**在 expr 中出现两侧均为 Z3 自由变量的取模（`%`）运算，包括但不限
于：

```text
# 反例 1（变量 % 变量：key.shape[1] 与 sparse_block_size.range_value 均为 Z3 变量）
key.shape[1] % sparse_block_size.range_value == 0

# 反例 2（同模式变体：任意 x.shape[i] % <scalar_attr>.range_value == 0）
```

**等价可求解写法**（按右侧变量的离散性选择其一）：

1. **右侧为有限枚举**（`allowed_range_value.type="enum"`，候选为离散值
   列表，如 `{1,2,4,8,16}`）：必须展开为“候选值判断 + 对应常量取模”的
   OR-of-ANDs 分支，保留变量取值与整除条件之间的关联：

   ```text
   ((divisor.range_value == 1 and x % 1 == 0) or
    (divisor.range_value == 2 and x % 2 == 0) or
    (divisor.range_value == 4 and x % 4 == 0) or
    (divisor.range_value == 8 and x % 8 == 0) or
    (divisor.range_value == 16 and x % 16 == 0))
   ```

   禁止只写全部常量取模的合取（会错误收紧为同时被所有候选整除），也禁止
   只写常量取模的析取（候选含 1 时几乎恒真，丢失 divisor 与 x 的对应关系）。

2. **右侧为连续范围 / 无离散枚举**：必须先把右侧替换为常量（如
   `x % 16 == 0`），或拆为多条**常量取模**约束条目；不得保留"变量对变
   量"形态。
3. **业务语义为"X 整除 block_size 且 X ∈ 枚举"**：使用第 1 条的
   OR-of-ANDs 条件分支，不得把全部候选取模条件合取。

**判断标准**：若 expr 文本包含 `*.range_value` 与 `*.shape[i]` / 其它
`*.shape[...]` 之间的 `%` 运算（即两侧至少一侧是 shape 轴），按本节改写
为常量取模或枚举展开。

## 六类算子场景要求

- FIA：区分 Q_S=1 Decode 与 Q_S>1 Prompt；layout、mask、sparse、量化、PA、RoPE、
  shared-prefix 分场景建模。
- MLA Prolog v3：cache_mode、BS 合轴、weight/kv/query quant mode 与 cache 原地输出联动；
  FRACTAL_NZ 权重格式不可丢失。
- LI/QLI：query/key layout、PA block_table、actual seq、sparse_count 与输出索引范围联动；
  QLI 的 query/key quant mode 与 dequant scale 联动。
- SFA/KV-SFA：sparse_indices 必须是合法结构化索引，不能用无界随机数；RoPE、PA、
  quant repository/tile mode 分场景建模。

## 输出前强制自检

- required/optional 是否与函数原型一致。
- 每个场景中 shape rank 是否与 layout 一致。
- dtype 组合是否受当前产品支持。
- scalar attr 是否具有可实例化的确定候选值。
- optional tensor 是否只在启用场景出现。
- 是否存在负 uint、越界 index、未满足整除/对齐或明显超内存 shape。
- outputs 是否被错误放入 inputs。
- `type.value` 是否已经转换为上述内部类型；禁止残留 `Tensor`、`List[int]`、`str`。
- **（v2 新增 A）** 遍历所有 tensor 类型的输入与输出，确认其结构化
  `dimensions.value` **非空**：秩唯一填整数、多 layout 分支填允许秩列表
  （如 `[3, 4]`），且与 layout→轴语义映射（BSND/PA_BSND→4、TND→3、明
  文 N 维→N）一致。任一 tensor `dimensions.value == []` 即为高危缺陷，
  必须补齐。
- **（v2 新增 B）** 遍历 `constraints_in_parameters`，确认每个 tensor
  都有显式秩约束 `len(x.shape) == N`（秩随 layout 变化的用条件绑定覆
  盖所有分支），与 `dimensions` 双保险；且这些秩约束不引入无界序列量
  词或"变量%变量"取模。同时确认非 tensor 的 scalar / 枚举参数**未**被
  误加 `dimensions`。
- **（v2 新增 C）** 逐个 tensor 复核结构化 `dtype.value` 已按文档填全
  （int32/int8/bfloat16/float16 等），且未因填充 `dimensions`/shape 规
  则被改写、删除或弱化——dtype 与秩是独立通道。
- **（v2 新增 D）** 遍历函数原型（含 `*` 分隔前后）与参数说明中**每一
  个**被标注为 `Tensor` / `Optional[Tensor]` / `torch.Tensor` 的参数
  名（含可选、预留、"仅支持默认值 None" 等修饰形态），确认 `inputs` /
  `outputs` 中均有对应条目，且 `type.value == "aclTensor"`、
  `is_optional.value` 与文档 `Optional` 标注一致（必选张量 `"false"`，
  可选/预留张量 `"true"`）。任何文档 `Tensor` 标注的参数若被建模为
  scalar / 数组型 / NoneType / 缺失，即高危缺陷，修复后才能进入
  GENERATE / EXECUTE。该条是阻断 TTK E2E 张量槽位错位的关键拦截。
- **（v2 新增 E）** 对所有 `is_optional.value == "true"` 且
  `type.value == "aclTensor"` 的参数，逐项检查其 `description` 与文档
  原文；若原文含"预留参数，仅支持默认值"、"reserved, only None"、
  "暂未支持非 None 取值"等语义，确认 `type=aclTensor` 与
  `is_optional=true` **同时**成立、`dimensions.value` 按 §F / §A 处
  理、`src_text` 摘录原文；**严禁**因"仅支持 None"而把 `type` 改为
  scalar，也**严禁**因 `dimensions` 留空而把 `type` 误判为非张量。
- **（v2 新增 F）** TTK E2E 张量槽位位置对齐复核：在函数原型张量参
  数按签名顺序编号 `Tensor_1 .. Tensor_N`；约束 `inputs` / `outputs`
  中的张量条目**个数等于 N**、**顺序与签名一致**。任何签名中的
  Tensor 被建模为 attr，都会使后续张量在 TTK 端位置错位（真实 NPU
  报错侧记：在签名较后位置的某张量参数处出现 `EZ9999 the dim num of
  <tensor_param> is 1, it should be 2`）——必须按 §F 拦截。
- **（v2 新增 G）** 遍历 `inputs` / `outputs`，确认**所有**文档声
  明为 scalar / 字符串类型（`double` / `int` / `int64` / `string` /
  `bool` / `aclIntArray` / `aclFloatArray` / `aclBoolArray` 等）的参
  数**未被误升**为 `aclTensor`，也**未**为这些 scalar 参数填
  `dimensions`（与 §D 协同）。本条与 §D 互为反命题，§F 防"张量被降
  级"，本条防"标量被误升"，共同保证"函数原型声明的类型 ↔ 约束
  `type` 字段"的通道一致。
- **（v2 新增 H）** 遍历 `constraints_in_parameters` 所有 `expr`
  文本，**不得出现** `for i in range(...)` / `all(... for i in
  range(...))` / `any(... for i in range(...))` 形式的逐元素推导
  式；shape 相等必须直接写 `A.shape == B.shape`（或与
  `len(A.shape) == len(B.shape)` 联合）。违反则改写为直接序列相等。
- **（v2 新增 I）** 遍历 `constraints_in_parameters` 所有 `expr`，
  **不得出现**两侧均为 Z3 变量的 `%` 运算（典型
  `x.shape[i] % <scalar>.range_value == 0` /
  `<scalar>.range_value % x.shape[i] == 0`）；若右侧 `<scalar>` 已
  定义为离散枚举（`allowed_range_value.type="enum"`），按枚举展开
  为各常量取模的合取或析取；若非枚举，先把右侧替换为常量后再写
  `%`。违反则改写。
- **D1（v2 新增）** 每个 tensor 的 `dimensions.value` 非空（按 layout
  秩语义正确）。
- **D2（v2 新增）** "仅支持 None"的预留张量被建模为
  `type=aclTensor` 可选张量（非 attr/NoneType）。
- **D3（v2 新增）** tensor 参数顺序与 API 函数原型顺序一致（按签
  名顺序逐一核对）。
- **D4（v2 新增）** 跨参数 shape 联动约束已加（sparse_indices↔
  query、各张量 D 维固定值等）。
- **D5（v2 新增）** `presence_dependency` 已覆盖（optional tensor
  在非启用场景为 None）。
- **D6（v2 新增）** 枚举值未混入默认值（仅"仅支持 X"时不含默认
  0/None）。
- **D7（v2 新增）** dtype 结构化字段完整且与文档一致。

仅输出纯 JSON，不输出 Markdown、解释或注释。
