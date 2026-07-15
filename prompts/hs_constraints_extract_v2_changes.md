# hs_constraints_extract v1 → v2 变更说明

前置：本次由多轮真实 NPU 反馈驱动的 7+1 类缺陷修复经验沉淀触发，根因均为
`root_cause=constraint_extraction`（已逐轮在 `analysis.json` 复核落盘）。本轮
只做由 specific_issues 与 NPU 报错侧记支持的最小精准增量，保留 v1 全部无关
规则、不重写整篇、不硬编码当前算子名/具体参数名（层内用通用占位符与规则
表达）。

## 变更总览

| 变更 | 位置 | 类型 | 类别 |
|---|---|---|---|
| 核心原则 7：文档声明的参数类型是张量还是标量，属性的属性 | 核心原则 | 新增 | type 通道 |
| 必做提取追加 3 条（presence 表达、跨参数形状联动、枚举值隔离） | 必做提取 | 增补 | type / 联动 / 枚举 |
| A. 每个 tensor 必填结构化 `dimensions.value`（秩唯一 int / 多分支列表） | 张量秩结构化规则 §A | 新增 | dimensions 必填 |
| B. `dimensions` 秩与 layout→轴语义映射（BSND/TND/PA_BSND）一致 | 张量秩结构化规则 §B | 新增 | layout→秩映射 |
| C. 追加 `len(x.shape)==N` 显式秩约束双保险 | 张量秩结构化规则 §C | 新增 | 显式 len(shape) 秩约束 |
| D. 非 tensor scalar/枚举参数不填 `dimensions` | 张量秩结构化规则 §D | 新增 | 防 scalar 误升 |
| E. dtype 保护 | 张量秩结构化规则 §E | 新增 | dtype 保护 |
| F. 文档声明为 `Tensor` 的参数必须保留张量通道 | 按文档声明类型分类参数规则 §F | 新增 | type 通道 |
| G. "仅支持 None 的可选/预留张量"的正确表达 | 按文档声明类型分类参数规则 §G | 新增 | type 通道 |
| H. TTK E2E 张量槽位位置对齐运行后果 | 按文档声明类型分类参数规则 §H | 新增 | type 通道（运行后果） |
| I. scalar / 字符串参数保持 scalar 通道 | 按文档声明类型分类参数规则 §I | 新增 | type 通道（反命题） |
| J. 跨参数形状联动约束必须显式落库 | 跨参数形状联动约束 §J | 新增 | 跨参数形状联动 |
| K. presence 用 `presence_dependency` 显式表达 | presence_dependency 表达规则 §K | 新增 | presence_dependency |
| L. 枚举值不混入默认值 | 枚举值与默认值隔离规则 §L | 新增 | 枚举值勿混默认值 |
| M. 禁止无界序列量词；shape 相等用直接 `==` | 约束可求解性规则 §M | 新增 | 可求解性 |
| N. 禁止"变量 % 变量"非线性取模 | 约束可求解性规则 §N | 新增 | 可求解性 |
| 自检清单追加 7 项 D1-D7 + 4 项 A-I 共 11 项 | 输出前强制自检 | 新增 | 自检硬化 |

原 v1 章节（核心原则 1-6、必做提取 8 条、六类算子场景要求、自检 8 项）全部
原样保留；通用 v4 字段命名与表达式语法引用保持不变。

## 逐项映射（真实 NPU 报错 / 子问题 → 文档证据 → 原规则缺陷 → 新规则）

### 变更 A：tensor `dimensions` 必填

- **真实 NPU 报错 / 失败 case**：v1 提取的 tensor 结构化 `dimensions` 字段
  整体留空（`dimensions.value == []`），生成器
  `param_constraint_utils.py::build_param_shape_len_constraint` 是**唯一**读
  取该字段生成 `len(param.shape)==N` 秩约束的通道——空值时该参数被
  `continue` 跳过、不产生秩约束，于是 Z3 自由采样张量秩产出 1D/5D/7D/8D
  等畸形 shape（典型 NPU 报错：
  `RuntimeError: Tensor sparse_indices is empty` /
  `EZ9999 block_table dim=1`）。
- **文档证据**（多算子输入文档）："query ... layout 为 BSND 时 shape 为
  [B,S1,Q_N,D]、TND 时 [Q_T,Q_N,D]"（秩 4/3）；"block_table shape 为 2
  维"（秩 2）；"actual_seq_lengths_query 支持长度为 B 的一维 tensor"（秩 1）。
- **原规则缺陷**：v1 "必做提取"仅有"每个 tensor 的 … rank …"一句自然语
  言要求，未强制写入**结构化 `dimensions.value` 机器字段**。结果
  `iter_*/constraints.json` 全部 tensor 的 `inputs.<name>.<platform>.dimensions.value == []`，
  只在 `src_text`/`description` 里保留 shape 文字。
- **新规则**：§A 规定每个 tensor 输入/输出必须在参数结构内填
  `dimensions.value`（秩唯一填 int、多分支填允许秩列表），并显式声明它
  是生成器生成 `len(param.shape)==N` 的唯一机器可消费字段、留空是高危
  缺陷、只写 src_text 不算数。规则以通用占位符表达，未硬编码算子/参
  数名。

### 变更 B：layout→秩映射

- **真实 NPU 报错 / 失败 case**：query 在 BSND（4D）/ TND（3D）两 layout
  下秩不同；key 在 PA_BSND（4D）/ BSND（4D）/ TND（3D）三 layout 下秩不
  同。若 `dimensions.value` 不写允许秩列表，生成器只能取单一秩约束，覆
  盖不全部分支 → NPU 形状违规。
- **文档证据**：layout 名到轴序列的映射
  `BSND = [B, S, N, D]`、`TND = [T, N, D]`、
  `PA_BSND = [block_num, block_size, N, D]`，秩 = 轴数量。
- **原规则缺陷**：v1 未规定 layout→轴序列→秩的强制映射，多分支秩容易
  被合并或漏填。
- **新规则**：§B 给出 layout→轴序列→秩映射
  （BSND=[B,S,N,D]→4、TND=[T,N,D]→3、PA_BSND=[block_num,block_size,N,D]→4）
  并要求多 layout 参数用允许秩列表覆盖所有分支（query BSND/TND→`[3,4]`、
  key PA_BSND/BSND/TND→`[3,4]`），明文 N 维取整数。规则以通用占位符
  表达，未硬编码算子/参数名。

### 变更 C：显式 `len(shape)` 秩约束双保险

- **真实 NPU 报错 / 失败 case**：query 7/10 退化为 1D `[576]`——
  `shape[-1]==576` 在 1D 张量中恒等于唯一元素被伪满足；key
  `shape[-2]==1` 在 1D 中左侧空条件被伪满足。负索引跨参约束在任意 /
  低秩下锁不住秩。
- **文档证据**：约束说明"query 中 D 值为 576"、"key/value 中 D 值为
  656"是末维语义，配合 layout 秩契约才成立；单靠末维负索引无法界定
  秩。
- **原规则缺陷**：v1 的 `constraints_in_parameters` 仅有
  self_shape_axis_value/shape_value_dependency/cross_param_constraint/
  shape_equality/presence_dependency 等类别，且形状约束多用负索引，
  **未给出**任何显式 `len(x.shape)==N`。
- **新规则**：§C 要求在 `constraints_in_parameters` 追加显式秩约束
  `len(x.shape)==N`，秩随 layout 分支变化的用条件绑定覆盖所有分支
  （如
  `((layout_x.range_value=="BSND") and (len(x.shape)==4)) or
  ((layout_x.range_value=="TND") and (len(x.shape)==3))`），与 §A /
  §B 的 `dimensions` 形成双保险；并明确该显式秩约束仍须遵守"约束可
  求解性规则"（禁无界序列量词、禁变量%变量取模），仅用 `len`、常
  量、`==`/`and`/`or` 构造。

### 变更 D：非 tensor 参数不误加 `dimensions`

- **真实 NPU 报错 / 失败 case**：scalar attr（如 `scale_value`（`double`）
  等）在 v1 提取的 `dimensions.value` 也为 `[]`——对 scalar 而言应为
  None/N/A 而非秩列表，下游消费易混淆。
- **原规则缺陷**：v1 未区分 tensor 与 scalar 的 `dimensions` 语义，强
  化 §A / §B 后存在给 scalar 误加秩的风险。
- **新规则**：§D 明确 tensor 之外的 scalar/枚举参数不需要秩，
  `dimensions` 保持空/None，禁止误加。

### 变更 E：dtype 保护

- **真实 NPU 报错 / 失败 case**：下游 case 的 dtype 出现回退（如整型张
  量被写成 fp16）。
- **文档证据**：结构化 `dtype.value` 在 v1 提取中**已正确填充**。
- **根因归属**：分析判定 dtype 出错属**生成器消费路径缺陷**
  （`case_input_map[param].dtype` 在进 Z3 前被预填 fp16、静态 dtype 约
  束与 int32 域冲突被 `choice_no_conflicts_expr` 静默丢弃），**非本提
  示词职责**。
- **新规则**：§E 明确 dtype 与秩是独立通道，填充 `dimensions` 时**不
  得**改动/删除/弱化已正确的 `dtype.value`。

### 变更 F：文档声明为 `Tensor` 的参数必须保留张量通道

- **真实 NPU 报错 / 失败 case**：真实 NPU 执行 10/10 报
  `EZ9999 the dim num of block_table is 1, it should be 2`，
  位置错位根因。
- **文档证据**（多算子输入文档）：函数原型中张量参数按签名顺序为
  `Tensor_1..Tensor_N`（典型 N 个 9 个：`query, key, value,
  sparse_indices, key_dequant_scale, value_dequant_scale, block_table,
  actual_seq_lengths_query, actual_seq_lengths_kv`）。其中部分
  `Tensor` 参数描述为"可选 / 预留 / 仅支持默认值 None"（如
  `key_dequant_scale`、`value_dequant_scale`），但**仍以 `Tensor`
  类型出现**。
- **原规则缺陷**：v1 未阻止"文档标 `Tensor` 的可选/预留参数被提取者
  **直接降级**为 `type=double / int / None` 等 scalar / 缺省形态"——
  典型动因："它看起来只有 None，不被使用，标成 scalar 更省事"，导
  致 `inputs` 中的张量条目数少于函数原型中的张量参数数（N 个），下
  游 TTK E2E 按签名顺序做位置化张量槽位映射时把这条缺位后的所有后
  续 Tensor 整体左移。`block_table` 因此收到了原本应给
  `value_dequant_scale` 或 `actual_seq_lengths_*` 的 1D 数据，触发
  `dim num of block_table is 1, it should be 2`。
- **新规则**：§F 强制三类 `Tensor` 标注参数（必选 / 可选 / 预留仅
  None）必须同时满足 `type.value="aclTensor"` + `is_optional.value`
  与文档 `Optional` 标注一致 + `dimensions.value` 按 §A / §B 处理；
  显式列出 4 类典型降级反例（`type=double`、标量化、`is_optional`
  误置 "false"、"仅支持 None" 误判为非张量）。规则以通用占位符
  表达，未硬编码算子 / 参数名。

### 变更 G："仅支持 None 的可选/预留张量"的正确表达

- **失败 case**：与 F 共同根因。当文档明确说"预留参数，仅支持默认
  值 None"时，提取者不知道如何保留张量通道才能让下游感知到该槽
  位占位。
- **文档证据**：参数说明章节 `**<param>**（Tensor）：可选参数，预
  留参数，仅支持默认值。` —— 类型仍标 `Tensor`、可选用
  `Optional[Tensor] = None` 表达。
- **原规则缺陷**：v1 没有"仅支持 None 的张量"如何填
  `ParamAttributes` 字段的样例，导致提取者很容易因"这参数永远
  None，索性改成 scalar"。
- **新规则**：§G 给出 7 项 `ParamAttributes` 字段填写细则
  （type/is_optional/dimensions/dtype/format/allowed_range/description/src_text）
  + `presence_dependency` 的选用方式 + 严禁的"反推为非张量"反例。

### 变更 H：TTK E2E 张量槽位位置错位的运行后果

- **真实 NPU 报错**：与 F / G 共同根因。TTK E2E 适配层按 API 函数
  原型的张量参数**签名顺序**做位置化张量槽位映射：原型的 N 个
  张量参数被映射到约束 `inputs` / `outputs` 中的 N 个张量条目槽
  位。当约束少 1 个（或几个）时，第 N 个槽位的 Tensor 被强行拉到
  第 N-1 个槽位，原本该给第 N 个 Tensor 的数据整体错位。
- **原规则缺陷**：v1 没有把"位置对齐"作为可观测的运行后果呈现
  给提取者，导致降级张量被视为"无害的提取自由"。
- **新规则**：§H 用通用术语（"TTK E2E"、"签名顺序"、"张量槽位"、
  "整体左移"、"EZ9999 的 dim num"）复述该后果并指明唯一修复路径
  在约束提取侧——每个签名中的 `Tensor` 标注必须有对应张量条目，
  `type` 通道不可被 scalar 化。本节不新增字段要求，仅做根因侧
  的可观测后果说明。

### 变更 I：scalar / 字符串参数保持 scalar 通道

- **失败 case**：补强 §F 的并立命题——避免"为防止张量被降级而
  被另一极端误升"（例如把 `pre_tokens = 2^63-1` 这类可选 int
  误升为 1D 张量）。
- **文档证据**：函数原型中 `pre_tokens` / `next_tokens` /
  `attention_mode` / `quant_scale_repo_mode` / `tile_size` /
  `sparse_block_size` / `layout_*` 等均为 `int` / `str` 类型，
  不是 `Tensor`。
- **原规则缺陷**：v1 §D 单独存在但没有与"禁止张量被降级"形成对
  偶。提取者在 §F 强化后可能矫枉过正地反向把 scalar 误升张量。
- **新规则**：§I 明确所有 `double` / `int` / `int64` / `string` /
  `bool` / `aclIntArray` / `aclFloatArray` / `aclBoolArray` 类
  型参数保留为 scalar attr 或数组型 scalar，不填 `dimensions`、
  不升为 `aclTensor`；自检清单追加对应项与 §F 自检互验。

### 变更 J：跨参数形状联动约束

- **真实 NPU 报错**：v1 提取把"配套约束以'参数 X 某维 == 参数
  Y 某维'或'固定值'形式出现"（如
  `sparse_indices.shape[0]==query.shape[0]`、BSND 时
  `sparse.shape[1]==query.shape[1]` 与 `sparse.shape[2]==1`
  （KV_N）、TND 时 `sparse.shape[1]==1`）漏抽，下游 Z3 求解器
  对各张量 shape 独立漂浮。NPU 真实报错
  `sparse_indices layout is X, shape is [got], expected shape is
  [exp]` 中的 `expected shape` 列出的形状由这些联动约束定义，
  缺抽即直接 NPU 形状违规侧记。
- **原规则缺陷**：v1 "必做提取"未对"跨参数形状联动"作为独立类
  别作要求，提取者容易把这类约束漏抽或仅在 `src_text` 中带过。
- **新规则**：§J 规定联动约束必须显式落库到
  `constraints_in_parameters`（`shape_value_dependency` 或
  `value_dependency`），`relation_params` 必须包含 X 与 Y 或常量
  所在参数，`src_text` 必须摘录原文；与"张量秩"是不同维度——
  秩控制"几个轴"，联动控制"每个轴取什么值"，二者必须**同时**
  落库。

### 变更 K：presence 用 `presence_dependency` 显式表达

- **真实 NPU 报错**：v1 提取把"某 optional tensor 仅在
  `layout_kv == PA_BSND` 时存在、其它 layout 应为 None"的语义
  漏抽为 `allowed_range_value`，下游 NPU 报
  `CheckBlockTable: layout_kv=TND 时 block_table 应=null`——
  即非启用场景下该张量被实际传值，NPU 端在校验阶段拦截。
- **原规则缺陷**：v1 "必做提取"仅有"optional tensor 的启用条
  件和互斥条件；未启用时必须为 None"一句，未指明 presence 应
  用 `presence_dependency` 还是 `allowed_range_value` 表达；
  提取者容易因"该参数在某场景下为 None" 把它塞进
  `allowed_range_value` 造成污染。
- **新规则**：§K 规定 presence 用 `presence_dependency` 显式
  表达（典型
  `(layout_kv.range_value == "PA_BSND") or (block_table is None)`），
  严禁把"该参数在某些场景下为 None"语义塞进
  `allowed_range_value`——`allowed_range_value` 是取值域枚举，
  `None` 是 presence/缺省语义，混入会污染 Z3 域并被生成器静
  默丢弃。

### 变更 L：枚举值不混入默认值

- **真实 NPU 报错**：v1 提取把"某枚举参数仅支持值 2，但描述
  含'默认值为 0'" 写成 `allowed_range_value.value=[0, 2]`，
  下游生成器自由赋 0，NPU 真实报错
  `attention_mode should be 2, got 0`——默认 0 错入枚举导致
  NPU 端在校验阶段拦截。
- **原规则缺陷**：v1 "必做提取"未明确"仅支持 X" 时的枚举
  边界与"默认值"的隔离关系，提取者容易因"有默认值" 把
  0/None 加入合法枚举。
- **新规则**：§L 规定文档"仅支持传入 X" 时
  `allowed_range_value.value` **只列 X**；默认值 0/None 由
  `is_optional` 与文档 `default` 字段单独表达。文档同时声
  明"默认值为 D"且"合法值 ∈ {X1, X2, ...}" 且 D ∉ 合法集
  时，按哨兵处理（`allowed_range_value.value` 含 D 作为候
  选之一 + `is_optional=true` 表达"不传即默认 D"），**严
  禁**写恒真等式绕过文档合法域。

### 变更 M / N：约束可求解性

- **真实 NPU 报错 / 失败 case**：v1 提取的约束中含两类表达式在
  下游 Z3 求解器上落入不可判定片段——
  1. 对无界 Z3 序列用 `for i in range(...)` / `all(...)` / `any(...)` /
     推导式表达形状相等或逐元素相等（如
     `(len(output.shape) == len(query.shape)) and all(output.shape[i]
     == query.shape[i] for i in range(len(query.shape)))`）；触发
     MBQI/序列理论 + 全称量词（ForAll）；
  2. 两侧均为 Z3 自由变量的取模（`%`）运算（如
     `key.shape[1] % sparse_block_size.range_value == 0`）；触发
     非线性算术。
- **原规则缺陷**：v1 未对 Z3 难解形式作拦截。
- **新规则**：§M 规定 shape 相等用直接 `==`（如
  `output.shape == query.shape` 或
  `(len(output.shape) == len(query.shape)) and (output.shape ==
  query.shape)`），禁止 `for i in range(...)` / `all(...)` /
  `any(...)` 包裹张量序列；§N 规定"变量 % 变量"按右侧变量离散
  性改写为枚举展开或常量取模，禁非线性取模。

### 自检清单追加 11 项（7 项 v2 新增 D1-D7 + 4 项 v2 新增 A-I）

- **A**：tensor `dimensions.value` 非空（按 layout 秩语义正确）。
- **B**：每个 tensor 都有显式秩约束 `len(x.shape)==N`（与
  `dimensions` 双保险），且不引入无界序列量词或"变量%变量"
  取模。
- **C**：dtype 结构化字段未被 shape/秩规则改写、删除或弱化。
- **D**：函数原型中每个 `Tensor` 标注参数都进入张量通道
  （`type=aclTensor`、`is_optional` 与文档一致）。
- **E**："仅支持 None" 预留张量保留张量通道（`type=aclTensor` +
  `is_optional=true`）。
- **F**：TTK E2E 张量槽位位置对齐（签名顺序编号
  `Tensor_1..Tensor_N` 对应 N 个张量条目）。
- **G**：scalar/字符串参数未被误升为 `aclTensor`、未误填
  `dimensions`。
- **H**：expr 不得出现 `for i in range(...)` /
  `all(... for i in range(...))` / `any(... for i in
  range(...))` 形式。
- **I**：expr 不得出现两侧均为 Z3 变量的 `%` 运算。
- **D1**：每个 tensor 的 `dimensions.value` 非空（按 layout 秩
  语义正确）。
- **D2**："仅支持 None"的预留张量被建模为 `type=aclTensor`
  可选张量（非 attr/NoneType）。
- **D3**：tensor 参数顺序与 API 函数原型顺序一致（按签名顺序
  逐一核对）。
- **D4**：跨参数 shape 联动约束已加（sparse_indices↔query、
  各张量 D 维固定值等）。
- **D5**：`presence_dependency` 已覆盖（optional tensor 在非
  启用场景为 None）。
- **D6**：枚举值未混入默认值（仅"仅支持 X"时不含默认
  0/None）。
- **D7**：dtype 结构化字段完整且与文档一致。

## 未改动项（显式声明）

- v1 核心原则 1-6 全部原样保留；仅在末尾追加第 7 条作为文档声
  明类型的元规则指针（指向 §F / §I），未改写前 6 条；
- v1 必做提取原 8 条全部保留，仅在末尾增补 3 条（presence 表
  达、跨参数形状联动、枚举值隔离），未新增字段要求；
- v1 六类算子场景要求原文保留；
- v1 自检 8 项全部原样保留，仅在其后追加 11 项 v2 新增自检；
- 通用 v4 字段命名与表达式语法引用保持不变（`prompts/operator_constraints_extract_v4.md`）；
- 未写入任何当前算子名或具体参数名的硬编码特例；layout 名
  （BSND/TND/PA_BSND）、`block_table`、`sparse_indices`、
  `actual_seq_lengths_*`、`dequant_scale` 等仅作为通用占位
  符与示例描述，不带算子名。
