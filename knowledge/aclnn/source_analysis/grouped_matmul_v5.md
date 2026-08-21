---
module: source_analysis_grouped_matmul_v5
scope: source_analysis
description: 基于锁定源码版本提炼的 aclnnGroupedMatmulV5GetWorkspaceSize 输入成功约束与危险构造
default_load: false
triggers:
  - kind: operator_name_eq
    value: "aclnnGroupedMatmulV5"
depends_on: [dimensions, allowed_range, implicit_parameters, platform_dtype, expression_language]
---

## 源码分析知识使用纪律

本模块只有在 `source_analysis_knowledge` 显式开启且算子名精确等于
`aclnnGroupedMatmulV5` 时才会装配。提取约束时必须遵守：

- 导入源：`aclnnGroupedMatmulV5GetWorkspaceSize-input-spec-20260811.md`
- 导入源 SHA256：`F168A9DA2F1E87DE8BB9706E8DA3EC671FD2F6D33877B6990A0091CCFFA9E1E4`

1. 下方报告锁定到其标题所列仓库 commit；所有采用的条目在
   `constraints_in_parameters[].origin` 中写 `source_analysis`，并在 `src_text`
   标明报告章节、源码 commit 与可信度，不得伪装成公开文档原文。
2. 第二、三节描述的成功必要条件可以作为有效输入约束；与公开文档冲突时保留双方来源，
   按锁定源码版本的实际入口行为表达，不静默改写公开文档。
3. 第四节仅将“经 V5 入口可达且高可信”的危险构造转成排除约束。标为低可信、
   `unknown`、`unknown-reachability`、经核实不可达或仅属提示/失败原因归并的条目，
   不得变成硬约束；应保留为分析备注。
4. “入口不校验”不等于推荐生成该输入；若公开 API 合同要求调用方自行保证，仍以合法调用
   合同为准。不得把仅警告放行、未定义行为或潜在静默错误当作正向有效用例。
5. 只提取当前算子签名中的真实参数；报告中的内部量、接口版本门控和派生量必须改写为
   当前表达式语言可表示的真实参数关系，无法可靠表达时不得臆造隐式参数。
6. 报告使用 `aclDataType` 全名；写入结构化字段和表达式时按 `platform_dtype` 的规范名
   映射（如 `ACL_FLOAT16` → `FLOAT16`、`ACL_BF16` → `BFLOAT16`），`src_text` 保留
   报告原始 token，禁止把 dtype token 当成参数取值。

# aclnnGroupedMatmulV5GetWorkspaceSize 输入规范设计文档（候选）

> **版本锁定：** 本报告基于 `ops-transformer` 仓库（远程：`https://gitcode.com/cann/ops-transformer.git`；commit 日期：`2026-07-02T14:10:40+08:00`）commit hash：`25a8a8064c06f75a510fdb85510ebdaf9806f2fd` 下的 `gmm/grouped_matmul` 路径运行；原始 CodeAnalysis 运行日期：`2026-07-24`。

## 一、概述

本文档依据代码分析结果（各函数的行为划分与契约）生成，是 GroupedMatmul 算子 V5 版本 aclnn 二段式接口第一段（完成入参校验、构图并计算 workspace 大小的入口）的输入规范候选文档。文档以创建侧可见的元素——张量与张量列表的形状（shape）、数据类型（dataType）、步长（strides）、数据格式（format）、是否传入空指针，以及各标量属性的取值——描述该入口的合法输入构造，以及会触发 Bug（未定义行为，或正常返回却结果可证伪地错误的语义计算错误）的输入构造。

旧接口文档仅作使用场景划分与创建侧参数词汇的格式提示，不作为正文内容的权威来源。正文所列全部约束与危险输入触发条件均取自代码分析结果；分析中标注的可信度与存疑标记原样保留。所列 Bug（危险输入）触发条件为分析所得、未经价值判断，供人工审阅确认。凡断言与旧接口文档存在实质冲突之处，均已回源核实并在文中标明。

文档中一律以创建侧的量表达约束，约定记号如下：

- `len(t)` 表示张量列表 `t` 的元素个数；`t[i]` 表示其第 `i` 个元素张量。
- `ndim(T)` 表示张量 `T` 的视图形状维数；`dim(T, k)` 表示其第 `k` 维大小；`dim(T, -1)` 表示末维。
- `dtype(T)` 表示张量的数据类型（取值用 `aclDataType` 全名，如 `ACL_FLOAT16`）。
- `format(T)` 表示张量的存储格式（`ACL_FORMAT_ND`、`ACL_FORMAT_FRACTAL_NZ` 等）。
- 「转置态」是由 strides 与 shape 共同决定的一个布尔量，其判定规则见 2.12，不是一个独立入参。

第二段执行接口 `aclnnGroupedMatmulV5` 的输入规范另见配套文档。

**关于「入口不校验」这句话的边界。** 本文档所称入口行为覆盖同一次 `aclnnGroupedMatmulV5GetWorkspaceSize` 调用内的完整可见规划链：① 参数校验；② 参数归一化与张量描述变换；③ 算子图构建及 shape/dtype 推导；④ 推导结果与用户 `out` 的比对；⑤ workspace 查询触发的分块规划与模板选择。只有这五层均未形成目标 guard 或其逻辑蕴含条件时，正文才写“未校验”。外部框架仅按已声明契约完成构图、调度、分配和对象操作，不假定其额外创造未在可见链中出现的语义检查。

本文档的成功终点是：正常资源条件下，全部可见参数、推导、输出比对和分块规划均完成，`workspaceSize` 与 `executor` 被写入并由入口返回成功。该结论不表示第二段设备计算已经执行，也不把“输入被规划阶段接受”扩大为错误数值、越界或设备故障。

---

## 二、公共（全局）约束

以下约束与具体使用场景无关，任意成功调用都必须满足。违反其中任一项，或落入第四节所列的危险输入构造，或被算子拒绝。

### 2.1 必选输入与输出的存在性

- `x`、`weight`、`out` 三个张量列表指针本身均不得为空指针。
- `len(x) ≥ 1`、`len(weight) ≥ 1`、`len(out) ≥ 1`。
- `x`、`weight`、`out` 三个列表内部的**每一个**元素张量指针均不得为空指针，即对任意 `i`，`x[i]`、`weight[i]`、`out[i]` 都不得为空。
- `workspaceSize`、`executor` 为调用方提供的合法可写输出位置。

  > 旧文档在返回值说明中把「传入参数 x 的元素为空指针，且传出参数 out 的元素不为空指针」与「传入参数 x 的元素不为空指针，且传出参数 out 的元素为空指针」列为两条独立触发条件，另有一行覆盖 `weight` 元素判空。已回源核实：入口最先执行的必选输入非空校验依次对三个列表执行「列表指针非空 → 列表长度非零 → 逐元素非空」三步，三个列表的检查彼此独立、不存在交叉配对判定。旧文档所列的两行本身都成立（`x` 任一元素为空即拒绝，`out` 任一元素为空即拒绝），只是**枚举不完整**——未列出 `x[i]` 与 `out[i]` 同时为空这一同样被拒绝的情形。这是旧文档的遗漏，不是接受/拒绝行为上的不一致，**不构成实质冲突**。

### 2.2 当前未启用的参数必须传入空指针

以下五个参数当前实现不支持任何非空取值，必须传入空指针：

- `activationInputOptional`
- `activationQuantScaleOptional`
- `activationQuantOffsetOptional`
- `activationFeatureOutOptional`
- `dynQuantScaleOutOptional`

### 2.3 可选张量列表的「语义空」归一化

在参数校验开始之前，以下 11 个可选字段会被统一归一化：满足下列任一条件的列表被当作「未提供」处理（等价于传入空指针），后续所有约束按「该可选输入不存在」判定：

- 列表长度为 0；
- 列表首元素为空指针（与列表长度无关，长度大于 1 时同样适用，整个列表被视为未提供）；
- 列表长度为 1，且该唯一元素为 1 维且 `dim(T, 0) == 0` 的占位空张量。

适用字段：`biasOptional`、`scaleOptional`、`offsetOptional`、`antiquantScaleOptional`、`antiquantOffsetOptional`、`perTokenScaleOptional`，以及 2.2 所列五个未启用参数。

由此得到一条对后续所有条款生效的推论：**凡后文称「某可选张量列表非空」，即蕴含该列表长度至少为 1 且其首元素非空**。但下标大于 0 的元素为空指针不在归一化范围内，该情形由后续的逐元素校验拒绝（见 3.10）。

### 2.4 groupType 与 splitItem 的组合

- `groupType` 只能取 `-1`（不分组，NO_SPLIT）、`0`（M 轴分组，SPLIT_M）、`2`（K 轴分组，SPLIT_K）三者之一。取值 `1`（N 轴分组）被显式拒绝，其余任何取值也被拒绝。
- 若 `splitItem` 取 `2` 或 `3`（输出为单张量），则 `groupType` 不得为 `-1`。
- 各 `groupType` 下 `splitItem` 的可取值进一步收窄，见 3.1。

### 2.5 groupListOptional（张量形式）

V5 接口的 `groupListOptional` 是 `aclTensor`；调用链内部同时存在一个数组形式的 group list 通道，但 V5 恒以空指针传入该通道。因此下列全部约束只针对张量形式的 `groupListOptional`。

不论是否分组，只要 `groupListOptional` 非空，均须满足：

- `ndim(groupListOptional) == 2`（当 `groupListType == 2` 时）或 `ndim(groupListOptional) == 1`（当 `groupListType` 为 `0` 或 `1` 时）。
- `dim(groupListOptional, 0) ≥ 1`。

在 `groupType` 为 `0` 或 `2` 的分组场景下（此时 group list 校验被真正触发，见 3.1），还须满足：

- `groupListType == 2` 时：`ndim == 2` 且 `dim(groupListOptional, 1) == 2`。
- `dim(groupListOptional, 0) ≤ 1024`。

  > **形式化强制（v3 新增）**：本条 `dim(groupListOptional, 0) ≤ 1024` **不得**只抄进
  > `src_text`，**必须**在 `constraints_in_parameters` 中产出 `shape_inequality`（或
  > `shape_value_dependency`）expr 把上界编码为可求解不等式
  > `groupListOptional.shape[0] <= 1024`，并在单 TensorList 场景同时产出
  > `weight.shape[0] <= 1024`（E=分组数=weight.shape[0]）。iter_002 闭环 10/50 用例因
  > E=weight.shape[0]=65534/1587301 > 1024 失败（NPU `AclNN_Parameter_Error(EZ1001): size
  > of groupList <E> should be less than or equal to 1024`），extractor 仅把「最大1024组」
  > 抄进 src_text 却未在 expr 编码上界，对 case 12 求值 `65534==65534 → True` 放行。
  > 文本记录 ≠ 形式化约束：src_text 摘录上界而 expr 不含 `<=1024` 即为漏抽。
- `dim(groupListOptional, 0) == groupNum` 且 `groupNum > 1`，或 `groupNum == 1`（此时长度不受约束）。`groupNum` 的取值随场景而定，见 3.1 各小节。
- `dtype(groupListOptional) == ACL_INT64`。

  > 旧文档就 `groupListOptional` 给出了一系列**元素取值**上的约束：`groupListType` 为 0 时须为非负单调非递减数列、末值不大于 `x` 的第一维；为 1 时须为非负数列、数值总和不大于 `x` 的第一维；为 2 时第二列数值总和不大于 `x` 的第一维；以及多张量场景下差值须与 `x` 各分组第一维一一对应。这些取值约束在 V5 路径上与分析结果实质冲突：分析报告显示，元素取值的非负性、单调性以及与 `x` 各分组 M 轴的累积/差分匹配，只在**数组形式**的 group list 通道上被校验，而 V5 恒以空指针传入该通道；V5 使用的张量形式 group list 只被校验形状维数、首维长度上限、首维与分组数的相等关系以及数据类型，其**元素取值从不被读取**（这些数值位于设备侧内存，主机侧校验环节无法访问）。已回源核实：分组场景下张量形式 group list 的校验函数只访问其视图形状与数据类型，无任何元素取值读取；数组形式 group list 的取值校验函数在 V5 版本标识下被整条分支跳过。因此旧文档所述的取值约束在 V5 上属于「调用方须自行保证、算子入口不做校验」的约定，不满足时不会得到任何错误信号，其后果落入下游计算。
  >
  > 关于第 1 维的上限：张量形式 group list 校验中只有一个常量上限 **1024**，与 `(len(x), len(weight), len(out))` 无关。旧文档「groupType 支持场景」表对 `groupType == 0` 的「单多单」与「多多单」两行写的是 128（只有「单单单」与 `groupType == 2` 的两行写 1024），旧文档「不同 groupType 约束」表对同两行写的又是 1024——旧文档自身在此矛盾。**但在接受/拒绝行为上，实现与「128」这一读法一致，不构成实质冲突**【已回源核实】：这两行传给该校验的 `groupNum` 分别是 `len(weight)` 与 `len(x)`，而上一条相等关系（`groupNum > 1` 时要求 `dim(groupListOptional, 0) == groupNum`）把首维钉死在 `groupNum` 上，`len(weight)`／`len(x)` 又受 2.9 的 128 上限约束（Ascend 950PR/Ascend 950DT 非量化同精度场景为 1024）。故这两种张量数组合下 `dim(groupListOptional, 0)` 实际取不到 129 至 1024，1024 这个常量在此不可达。
  >
  > 还有一处：**`groupType == -1` 时 `groupListOptional` 的数据类型不被校验**。旧文档参数表就 `groupListOptional` 写「数据类型：INT64」，返回值表亦把 groupList 的数据类型不在支持范围列为报错条件。实现中对该张量数据类型为 `ACL_INT64` 的判定只存在于分组场景（`groupType` 为 `0` 或 `2`）所用的那一个校验函数内，不分组路径既不调用它、也不在别处读取该张量的数据类型【已回源核实】。故 `groupType == -1` 且传入非 `ACL_INT64`（如 `ACL_INT32`）的 group list 张量会被放行。（其维数与「第 0 维不小于 1」两项则在通用参数校验中对所有 `groupType` 统一施加，与旧文档一致。）

### 2.6 groupListType

- 当 `groupListType == 2`（稀疏 M 轴模式）时，要求运行平台为 Atlas A2 训练系列产品/Atlas A2 推理系列产品、Atlas A3 训练系列产品/Atlas A3 推理系列产品对应架构，或 Ascend 950PR/Ascend 950DT 对应架构；**且** `groupType == 0`。
- 否则（非稀疏），`groupListType` 只能取 `0` 或 `1`。

  > 旧文档参数表将 `groupListType` 的取值范围笼统写为「取值范围 0-2」，各场景约束中亦多处写作「groupListType 为 0/1/2」而未附加限定。这与分析结果实质冲突：取值 `2` 并非普遍可用，而是同时受平台与 `groupType` 双重限制——在 Atlas 推理系列产品（310P）等其余平台上取值 `2` 一律被拒绝；在允许的平台上，`groupType` 不为 `0` 时取值 `2` 同样被拒绝。已回源核实：通用参数校验环节中，`groupListType == 2` 分支先校验 NPU 架构属于上述两类之一，再校验 `groupType` 等于 `0`，两条校验均为硬拒绝；非稀疏分支则把 `groupListType` 限定在 `{0, 1}`。旧文档 Ascend 950PR/Ascend 950DT 一节所述「仅全量化且 groupType 为 0 场景下支持 groupListType 为 2」中的「groupType 为 0」部分与实现一致，「仅全量化」这一附加限定则未在该校验环节体现。

### 2.7 actType

- `actType ≥ 0`。
- 若 `actType != 0`（非「无激活」），则 `actType != 3`（误差函数版 GELU）且 `actType < 6`。

即入口层面接受的取值集合为 `{0, 1, 2, 4, 5}`，分别对应 `GMM_ACT_TYPE_NONE`、`GMM_ACT_TYPE_RELU`、`GMM_ACT_TYPE_GELU_TANH`、`GMM_ACT_TYPE_FAST_GELU`、`GMM_ACT_TYPE_SILU`。

各场景对 `actType` 的进一步收窄：

- 非量化场景（任意平台）：`actType` 必须为 `0`。
- 伪量化（weight-only 反量化）场景：`actType` 必须为 `0`。
- Atlas 推理系列产品（310P）：`actType` 必须为 `0`。
- Ascend 950PR/Ascend 950DT 的全量化场景：`actType` 为 `0`，或同时满足 `dtype(x) == ACL_INT8` 且 `dtype(weight[0]) == ACL_INT8`、量化模式落在「静态 pertensor-perchannel」或「动态 pertoken-perchannel」之一（判定方式见 3.3.2）、且 `actType ∈ {1, 2, 4, 5}`。
- Atlas A2/A3 系列的 A8W8、A8W4、A4W4 全量化场景：入口层面**不**对 `actType` 做超出上述通用范围的拦截，仅在检测到核函数未实现该激活组合时记录警告日志并放行。其后果见 4.7。

  > 旧文档参数表写「取值范围为 0-5」并列出 6 个枚举值，同时在约束说明中对取值 `3` 附注「不支持」。二者对取值 `3` 不可用的结论一致，仅表达形式不同（旧文档以文字附注，实现以显式拒绝），不构成实质冲突。

### 2.8 tuningConfigOptional

- 若 `tuningConfigOptional` 非空且其元素个数大于 0，则要求 `0 ≤ tuningConfigOptional[0] ≤ maxM`，其中 `maxM = max_i dim(x[i], 0)`（`x` 为空列表时 `maxM` 取 0，但 2.1 已排除该情形）。
- 若 `tuningConfigOptional` 为空指针或元素个数为 0，该项校验被跳过。

  > 旧文档就 `tuningConfigOptional` 另行规定：数组维度（元素个数）为 3；第二个元素用于开启 A8W4 场景 weight 亲核格式（取值 0 或 1）；第三个元素为允许额外使用的最大 workspace 空间（`-1` 或不小于 `M × N × 4` 的数值）；A8W8 定轴算法要求第一个元素大于 128 且小于 512。这些约束与分析结果实质冲突：分析报告显示，入口校验只读取并校验首元素，且区间为 `[0, maxM]` 而非 `(128, 512)`；元素个数不被校验（元素个数为 0 时整条校验被跳过，元素个数为 1、2 或大于 3 时也不被拒绝）；第二、第三个元素的取值不被读取，也不被校验。已回源核实：该校验环节先遍历 `x` 求 `maxM`，随后仅在数组非空且元素个数大于 0 时校验下标 0 处元素落在 `[0, maxM]`，函数体内无对下标 1、2 的任何访问。旧文档所列其余数值要求属于「调用方须自行保证、算子入口不做校验」的性能调优约定。另需指出：旧文档返回值表写「传入参数 tuningConfigOptional 的元素为负数，或者大于 x 的行数 m」触发 `ACLNN_ERR_PARAM_INVALID`，这一条与实现的 `[0, maxM]` 区间**完全吻合**；旧文档 A8W8 一节所述「第一个元素设为大于 128 且小于 512」是**定轴算法的性能启用条件**（原文前置「如果需要启用定轴算法以优化性能，需同时满足以下输入形状与参数配置条件」），并非合法取值区间，不与 `[0, maxM]` 构成对立。
  >
  > 另有一处差异：旧文档 Ascend 950PR/Ascend 950DT 公共约束一节明写「tuningConfigOptional：**不支持此参数**」，而实现在该架构上**照常执行首元素区间校验并放行**——该校验的调用点无任何平台门控，Ascend 950PR/Ascend 950DT 的三个专属校验器亦全都不引用该参数【已回源核实】。即在该架构上传入合法 `tuningConfigOptional` 不会被拒绝，旧文档所述的「不支持」不由入口的返回值体现。

### 2.9 张量列表长度上限

- 在「运行平台为 Ascend 950PR/Ascend 950DT 对应架构，且 `dtype(x) ∈ {ACL_FLOAT16, ACL_BF16, ACL_FLOAT}`，且 `dtype(x) == dtype(weight[0])`」的非量化同精度场景下：`len(x) ≤ 1024` 且 `len(weight) ≤ 1024`。
- 其余全部场景：`len(x) ≤ 128` 且 `len(weight) ≤ 128`。
- `out` 与各可选张量列表的长度不受本条直接约束，而是通过与 `x`/`weight` 的长度相等关系间接约束（见 3.1、3.10）。

  > 旧文档参数表就 `x`/`weight`/`out` 写「tensorList 长度支持 [1, 128] 或者 [1, 1024]」，并在平台差异一节说明 Ascend 950PR/Ascend 950DT 在非量化场景支持最多 1024 个张量、伪量化最多 128 个、全量化最多 1 个，Atlas A2/A3 最多 128 个。分析结果与之部分吻合、部分冲突：1024 上限的生效条件与旧文档一致（Ascend 950PR/Ascend 950DT 的非量化同精度场景），128 上限的默认值也一致；但「全量化最多 1 个」并非由长度上限校验实现，而是由 Ascend 950PR/Ascend 950DT 全量化路径中「`len(x) == 1` 且 `len(weight) == 1` 且 `len(out) == 1`」这条独立的硬性要求实现（见 3.3.1）；「伪量化最多 128 个」则**确由**该长度上限校验实现——1024 上限分支的门控要求 `dtype(x) == dtype(weight[0])`，伪量化不满足该等式，直接落到 128 上限分支【已回源核实】。以上三点在接受/拒绝行为上与旧文档一致，不构成实质冲突。
  >
  > `out` 的列表长度不受独立的 128/1024 上限 guard。`groupType == -1` 与 `groupType == 0` 的列表拓扑会间接限制 `len(out)`；`groupType == 2` 且 `len(weight) > 1` 或 `len(out) > 1` 时，参数层不再把 `len(out)` 与 `len(weight)` 绑定。后续输出构造、shape 推导和逐项 shape 比对仍须满足，因此本条只表示不存在绝对 `len(out)` 上限，不表示任意长度与任意 shape 的组合均成功。

### 2.10 数据格式

- `x` 与 `out` 的每个元素：格式必须是非私有格式（即 `ACL_FORMAT_ND` 一类的公有格式），不接受任何私有格式。
- `weight` 的每个元素：格式必须是非私有格式，或属于 `ACL_FORMAT_FRACTAL_NZ` 系列（含 `FRACTAL_NZ`、`FRACTAL_NZ_C0_16`、`FRACTAL_NZ_C0_32` 三种）之一。
- 任一元素的格式枚举值越界（不小于格式枚举上界）一律被拒绝。
- 在 Ascend 950PR/Ascend 950DT 的全量化场景下，若 `format(weight[0])` 为 `ACL_FORMAT_FRACTAL_NZ`，V5 版本被显式拒绝（该 NZ 专属校验要求接口版本为 WeightNz 版本）。即：**Ascend 950PR/Ascend 950DT 全量化场景下，V5 接口只接受公有格式的 weight**。
- **格式校验的作用范围仅限 `x`、`weight`、`out` 三个必选张量列表**。`biasOptional`、`scaleOptional`、`offsetOptional`、`antiquantScaleOptional`、`antiquantOffsetOptional`、`perTokenScaleOptional` 六个可选张量列表，以及张量形式的 `groupListOptional`，其数据格式**在入口层面完全不被读取、不被校验**。

  > 旧文档参数表为上述六个可选输入与 `groupListOptional` 各写了一行「数据格式：ND」，返回值表亦把「biasOptional、scaleOptional、offsetOptional、antiquantScaleOptional、antiquantOffsetOptional、groupListOptional 的数据类型和数据格式不在支持的范围内」列为 `ACLNN_ERR_PARAM_INVALID` 的触发条件之一。**旧文档要求这七个参数为 ND 格式，入口实际不校验**【已回源核实：格式判定函数的全部调用点只传入 `x`、`weight`、`y` 三者，Ascend 950PR/Ascend 950DT 的三个专属校验器亦只对这三者做格式判定；六个可选列表与 `groupListOptional` 从不出现在任何格式判定中】。以非 ND 格式构造这些可选输入不会得到任何错误信号。
  >
  > 另一处方向相同的差异：旧文档参数表就 `x`、`weight`、`out` 也只写「ND」，而实现的判据是「非私有格式」——`ACL_FORMAT_ND` 之外的公有格式（如 `ACL_FORMAT_NCHW`、`ACL_FORMAT_NHWC`）同样被接受，`weight` 另可取 `ACL_FORMAT_FRACTAL_NZ` 系列。这是实现比旧文档更宽的一处，旧文档在各场景约束中反复出现的「仅支持 ND 进 ND 出」同样不由入口校验兑现。

### 2.11 int4 与 fp4 数据的 ACL_INT32 承载与就地解包

若 `dtype(weight[0]) == ACL_INT32`，或 `dtype(x[0]) == ACL_INT32`，该列表被就地重新解释为 4 bit 数据：

- 数据类型被改写为 `ACL_INT4`（原类型为 `ACL_INT32` 时）或 `ACL_FLOAT4_E2M1`（原类型为 `ACL_FLOAT` 时）。
- 承载 4 bit 语义的那一根轴的逻辑维度被放大为原值的 8 倍：非转置态张量为末轴，转置态张量为次末轴。
- 若该张量格式属于 `ACL_FORMAT_FRACTAL_NZ` 系列，其存储形状末轴额外放大 8 倍。
- 转置态张量的 strides 末维放大 8 倍，且下标位于 `[0, len(strides) - 3]` 区间的批量维 strides 各自放大 8 倍。

对创建侧的要求：

- 以 `ACL_INT32` 承载的张量，其底层数据必须确实按「每个 32 bit 元素打包 8 个 4 bit 值」的约定编码。该编码本身不被校验。
- 参与解包的张量必须至少为 1 维（`ndim ≥ 1`）。违反该要求的输入构造见 4.1。
- 该解包对调用方持有的张量对象是**就地改写**：调用返回后（无论成功或失败），传入的 `weight`/`x` 张量对象的数据类型、视图形状与 strides 已被改写为解包后的值，且失败时不回滚。
- 在 Ascend 950PR/Ascend 950DT 对应架构上，`x` 的解包按「不跳过转置判定」的方式执行；在其余平台上，`x` 的解包按「跳过转置判定」的方式执行（即在其余平台上，`x` 的解包恒按非转置态处理，只放大末轴、不改写 strides）。`weight` 在所有平台上都按「不跳过转置判定」的方式解包。

### 2.12 转置态的判定与互斥

`x` 与 `weight` 的转置态不是入参，而是由列表内张量的 shape 与 strides 推导出的布尔量。推导规则按 `groupType` 分为两类：

- `groupType == 2`（SPLIT_K）：
  - `transposeX` = 「`x[0]` 的末两维呈转置内存布局」。
  - `transposeWeight` = 「存在某个 `i`，使 `weight[i]` 的末两维呈转置内存布局，且该张量最后 1 或 2 个轴（`ndim > 1` 时取 2 个，否则取 1 个）的大小之积不等于 1」。
- `groupType ∈ {0, -1}`（SPLIT_M / NO_SPLIT）：
  - `transposeX` = 「存在某个 `i`，使 `x[i]` 的末两维呈转置内存布局，且该张量最后 1 或 2 个轴的大小之积不等于 1」。
  - `transposeWeight` = 「`weight[0]` 的末两维呈转置内存布局」。

由此得到的全局约束：

- `transposeX` 与 `transposeWeight` 不得同时为真。
- `groupType ∈ {0, -1}` 时，`transposeX` 必须为假，即 `x` 不得转置。
- `groupType == 2` 时，`transposeX` 必须为真，即 `x` 必须转置。
- `groupType ∈ {0, -1}` 时，`weight` 列表中**每一个**元素的转置态必须与 `transposeWeight`（由 `weight[0]` 推出）一致，不允许列表内部转置态不统一。
- 「末两维大小之积等于 1」的张量在转置态判定中被豁免（视为非转置），因此 `M`、`N`、`K` 任一为 1 的退化形状下，strides 层面的转置意图不会被识别。

### 2.13 空张量与退化形状（`groupType != 2` 时生效）

在 `groupType` 不为 `2` 的场景下：

- `ndim(x[i]) ∈ [2, 6]`（对每个 `i`）。
- `ndim(weight[i]) ≥ 2`（对每个 `i`）。
- 记 `zeroM` = 「对每个 `i`，`x[i]` 除末维外各维之积等于 0」，`zeroN` = 「对每个 `i`，`dim(weight[i], -1) == 0`」，`zeroK` = 「存在某个 `i` 使 `dim(x[i], -1) == 0`」。要求 `zeroM ∨ zeroN ∨ ¬zeroK`，即 K 轴为 0 只允许出现在整体计算已退化为空（M 全零或 N 全零）的场景。

在 `groupType == 2` 的场景下，上述三条校验被整体跳过，仅保留对 `x[0]` 与 `weight[0]` 的单点非空判定；由此产生的危险输入构造见 4.2 与 4.3。

此外，在算子图构建阶段还存在一条快速返回路径：

- `groupType != 2` 且（`x` 的所有元素都是空张量，或 `weight` 的所有元素都是空张量）时，`*workspaceSize` 被置为 0 并直接返回成功，不构建实际算子图。
- `groupType == 2` 且 `x`、`weight` 的所有元素均为非空且均为 2 维时，若「所有 `dim(x[i], 1) == 0`」或「所有 `dim(weight[i], -1) == 0`」，同样置 `*workspaceSize` 为 0 并直接返回成功。

### 2.14 Atlas 推理系列产品（310P）的受限场景

在该平台上，除本节其余条款外，还须同时满足：

- `len(x) == 1 ∧ len(out) == 1 ∧ len(weight) == 1 ∧ groupType == 0`。
- `dtype(x) == ACL_FLOAT16 ∧ dtype(weight[0]) == ACL_FLOAT16`，且 `biasOptional` 为空或 `dtype(bias[0]) == ACL_FLOAT16`。
- `actType == 0`。
- 若 `transposeWeight` 为真，另需 `groupType == 0` 且 `len(x) == len(weight) == len(out) == 1`（与上一条重合）。
- A8W8 全量化与伪量化（weight-only 反量化）在该平台上均被拒绝。

### 2.15 场景判定规则

算子按 `dtype(x)` 与 `dtype(weight[0])` 的组合（以及运行平台）判定进入哪一条校验路径。判定顺序如下，先命中者生效：

1. 若运行平台为 Atlas 推理系列产品（310P）：先施加 2.14 的全部限制。
2. 若运行平台为 Ascend 950PR/Ascend 950DT 对应架构：
   - 若判定为**全量化**（`dtype(x) ∈ {ACL_FLOAT4_E2M1, ACL_INT4, ACL_FLOAT4_E1M2}`，或 `dtype(x)` 与 `dtype(weight[0])` 的单元素字节宽度**均为 1**）→ 进入 3.3 所述的 Ascend 950 全量化路径。
   - 否则若判定为**伪量化**（`dtype(x)` 与 `dtype(weight[0])` 的单元素字节宽度**不相等**）→ 进入 3.4 所述的 Ascend 950 伪量化路径。
   - 否则（非量化）→ 要求 `actType == 0`，随后继续按下列第 3 条起的通用分派。
3. `dtype(x) == ACL_INT8 ∧ dtype(weight[0]) == ACL_INT4` → A8W4 伪量化路径（3.8）。
4. `dtype(x) == ACL_INT4 ∧ dtype(weight[0]) == ACL_INT4` → A4W4 全量化路径（3.7）。
5. `dtype(x) ∈ {ACL_BF16, ACL_FLOAT16, ACL_FLOAT} ∧ dtype(weight[0]) == dtype(x)` → 非量化路径（3.5）。
6. `dtype(x) == ACL_INT8 ∧ dtype(weight[0]) == ACL_INT8` → A8W8 全量化路径（3.6）。
7. `dtype(x) ∈ {ACL_BF16, ACL_FLOAT16} ∧ dtype(weight[0]) ∈ {ACL_INT8, ACL_INT4}` → A16W8 / A16W4 伪量化路径（3.9）。
8. 其余任何 `(dtype(x), dtype(weight[0]))` 组合一律被拒绝。

  > 上述分派是**白名单穷举**：不匹配任何一条分支的 dtype 组合落到末尾的统一拒绝出口。由此产生一处与旧文档的差异：旧文档参数表把 `INT16` 列入 `x` 与 `weight` 的受支持数据类型（角标标注仅 Ascend 950PR/Ascend 950DT 不支持，即在 Atlas A2/A3 系列上应当可用），而实现**没有任何针对 `INT16` 的分支**【已回源核实：全部入口实现文件中不出现 `INT16` 标识】，`ACL_INT16` 的 `x` 或 `weight` 在任何平台上都落到白名单末尾被拒绝。这是实现比旧文档更严的一处。

由第 2 条可直接推出两条对 Ascend 950PR/Ascend 950DT 生效的排除性结论：

- **`dtype(x) == ACL_INT8` 且 `dtype(weight[0]) == ACL_INT4`（A8W4）在 Ascend 950PR/Ascend 950DT 上经 V5 接口一律被拒绝。** 该组合按字节宽度判定要么落入全量化路径、要么落入伪量化路径：落入全量化路径时，该路径只接受 `ACL_INT8`×`ACL_INT8`、`ACL_HIFLOAT8`×`ACL_HIFLOAT8`、`ACL_FLOAT8_E4M3FN`/`ACL_FLOAT8_E5M2` 两两组合、`ACL_FLOAT4_E2M1`/`ACL_FLOAT4_E1M2` 两两组合共四类，`ACL_INT8`×`ACL_INT4` 不在其中而被拒绝；落入伪量化路径时，该组合被归为 int8-int4 数据流，该数据流要求接口版本为 WeightNz 版本，V5 被拒绝。两条分支都拒绝，故结论与字节宽度表的具体取值无关。
- **`dtype(x) == ACL_INT4` 且 `dtype(weight[0]) == ACL_INT4`（A4W4）在 Ascend 950PR/Ascend 950DT 上经 V5 接口一律被拒绝**：`dtype(x) == ACL_INT4` 必然被判定为全量化，而该路径的四类受支持组合不含 `ACL_INT4`×`ACL_INT4`。

---

## 三、各使用场景约束

本节按官方文档的组织顺序排列：3.1 给出各平台场景共同合取的分组形态约束；3.2 至 3.4 依次给出 Ascend 950PR/Ascend 950DT 的非量化、静态量化与动态量化、伪量化场景；3.5 至 3.9 依次给出 Atlas A3/A2 系列产品的非量化、全量化-A8W8、全量化-A4W4、伪量化-A8W4、伪量化-A16W8 与伪量化-A16W4 场景；3.10 给出可选张量列表与 `weight` 的通用结构一致性规则。一次成功调用须同时满足所处分组形态、平台场景、第二节公共约束及 3.10 中适用的全部条款。

### 3.1 分组形态约束

#### 3.1.1 groupType == -1（不分组）

- `splitItem ∈ {0, 1}`。
- `len(x) == len(out) == len(weight)`。
- `transposeX` 必须为假。
- 对每个 `i`：`ndim(x[i]) ∈ [2, 6]`；`ndim(weight[i]) == 2`；`ndim(x[i]) == ndim(out[i])`。
- 对每个 `i`：`weight[i]` 的转置态与 `transposeWeight` 一致（见 2.12）。
- 对每个 `i`，逐维形状关系（记 `d = ndim(x[i])`）：
  - 对每个 `k ∈ [0, d-2)`：`dim(x[i], k) == dim(out[i], k)`。**第 `d-2` 维（M 轴）不被本条校验**：该循环的上界是 `ndim(x[i]) - 2`，`d == 2` 时循环一次都不执行。（源码注释自述「除最后一维外每一维都须相等」，与循环上界不符；以循环上界为准。）
  - **但 M 轴的相等关系仍然成立，由本节之外的环节兜底**【已回源核实】：同一次调用在参数校验之后要做形状推导，推导出的 `out[i]` 是「`x[i]` 原样复制、仅末维改写为 `weight[i]` 的 N 轴」，因此其 M 轴恒等于 `dim(x[i], d-2)`；紧接着的输出形状比对会把与之不符的用户 `out[i]` 拒绝。故 `dim(x[i], d-2) != dim(out[i], d-2)` 的输入**会被拒绝**，只是错误来自形状比对而非本节的逐维校验。
  - `dim(x[i], d-1) == dim(weight[i], 0)`（K 轴对齐）。
  - `dim(out[i], d-1) == dim(weight[i], 1)`（N 轴对齐）。
  - 非 Ascend 950PR/Ascend 950DT 平台：`dim(x[i], d-1) ≤ 65535`。
  - 非 Ascend 950PR/Ascend 950DT 平台且 `transposeWeight` 为假：`dim(weight[i], 1) ≤ 65535`。
  - 若 `dtype(weight[0]) == ACL_INT4`：`dim(weight[i], 1)` 必须为偶数。
- `groupListOptional`（张量形式）可传空；若非空，`dim(groupListOptional, 0) == len(x)`。

  > 旧文档在「groupType 支持场景」表中就 `groupType == -1` 一行写明「groupListOptional 必须传空」，在「不同 groupType 约束」表中同样写作「groupListOptional 必须传空」。这与分析结果实质冲突：该限制只作用于**数组形式**的 group list 通道，而 V5 恒以空指针传入该通道，故对 V5 而言该限制自动满足、无从违反；V5 实际使用的**张量形式** `groupListOptional` 在不分组场景下不但允许非空，且非空时被要求 `dim(groupListOptional, 0) == len(x)`。已回源核实：该校验函数中「非 V1 接口时 group list 必须为空」一条判定的是数组形式通道的指针，紧随其后的另一条判定则是「张量形式 group list 非空时其第 0 维须等于 `len(x)`」，两者是并列的独立判定，作用于两个不同的参数。
  >
  > 同一行还有两条差异：
  >
  > 其一，旧文档「groupType 支持场景」表就该行写「**x 中 tensor 要求维度一致**」，「不同 groupType 约束」表亦写「伪量化场景 x 中 tensor 要求维度一致」。**旧文档要求跨元素维数一致，入口实际不校验**【已回源核实：该场景下的逐元素循环只对同一下标 `i` 做 `ndim(x[i]) ∈ [2,6]` 与 `ndim(x[i]) == ndim(out[i])` 两项比较，循环体内不存在跨下标的维数比较，也没有任何跨迭代保存维数的变量】。因此 `ndim(x[0]) == 2` 而 `ndim(x[1]) == 4` 这类输入会被放行。
  >
  > 其二，旧文档同两张表把本场景的 `x`、`out` 维数限定为 **2 维**（「支持 2 维」／「非量化 x，out 中 tensor 需为 2 维」），仅对伪量化子情形放宽到 2-6 维；实现的逐元素校验对所有 dtype 场景一律接受 `ndim(x[i]) ∈ [2, 6]`，不按量化类别收窄。这是实现比旧文档更宽的一处。

#### 3.1.2 groupType == 0（M 轴分组）

- `transposeX` 必须为假。
- `(len(x), len(weight), len(out))` 必须落在下列四种组合之一，其余组合一律被拒绝：

**组合 A：`(1, 1, 1)`**

- `splitItem ∈ {2, 3}`。
- `ndim(x[0]) == 2`、`ndim(weight[0]) == 3`、`ndim(out[0]) == 2`。语义形状为 `x(M, K)`、`weight(E, K, N)`、`out(M, N)`。
- `dim(x[0], 1) == dim(weight[0], 1)`（K 轴）。
- `dim(x[0], 0) == dim(out[0], 0)`（M 轴）。
- `dim(weight[0], 2) == dim(out[0], 1)`（N 轴）。
- 非 Ascend 950PR/Ascend 950DT 平台：`dim(x[0], 1) ≤ 65535`。
- 非 Ascend 950PR/Ascend 950DT 平台且 `transposeWeight` 为假：`dim(weight[0], 2) ≤ 65535`。
- 若 `dtype(weight[0]) == ACL_INT4`：`dim(weight[0], transposeWeight ? 1 : 2)` 必须为偶数。
- `groupListOptional` 必须非空；`groupNum = dim(weight[0], 0)`，按 2.5 的分组场景条款校验。

**组合 B：`(1, N, 1)`，`N > 1`**

- `splitItem ∈ {2, 3}`。
- `ndim(x[0]) == 2`；对每个 `i`，`ndim(weight[i]) == 2`；`ndim(out[0]) == 2`。语义形状为 `x(M, K)`、`weight[i](K, N)`、`out(M, N)`。
- 对每个 `i`：`dim(weight[i], 0) == dim(x[0], 1)`（K 轴）。
- `dim(x[0], 0) == dim(out[0], 0)`（M 轴）。
- 对每个 `i`：`dim(weight[i], 1) == dim(out[0], 1)`（N 轴）。
- 非 Ascend 950PR/Ascend 950DT 平台：`dim(x[0], 1) ≤ 65535`。
- 非 Ascend 950PR/Ascend 950DT 平台且 `transposeWeight` 为假：`dim(out[0], 1) ≤ 65535`。
- 若 `dtype(weight[0]) == ACL_INT4`：对每个 `i`，`dim(weight[i], transposeWeight ? 0 : 1)` 必须为偶数。
- `groupListOptional` 必须非空；`groupNum = len(weight)`。

**组合 C：`(N, N, 1)`，`N > 1`**

- `splitItem ∈ {2, 3}`。
- `len(x) == len(weight)`。
- 对每个 `i`：`ndim(x[i]) == 2`、`ndim(weight[i]) == 2`；`ndim(out[0]) == 2`。
- 对每个 `i`：`dim(weight[i], 0) == dim(x[i], 1)`（K 轴）。
- `Σ_i dim(x[i], 0) == dim(out[0], 0)`（M 轴为被拆分轴，各分组之和等于单一输出的 M 轴）。
- 对每个 `i`：`dim(weight[i], 1) == dim(out[0], 1)`（N 轴）。
- 非 Ascend 950PR/Ascend 950DT 平台：对每个 `i`，`dim(weight[i], 0) ≤ 65535`。
- 非 Ascend 950PR/Ascend 950DT 平台且 `transposeWeight` 为假：`dim(out[0], 1) ≤ 65535`。
- 若 `dtype(weight[0]) == ACL_INT4`：对每个 `i`，`dim(weight[i], transposeWeight ? 0 : 1)` 必须为偶数。
- `groupListOptional` 可传空；若非空，`groupNum = len(x)`。

**组合 D：`(1, N, M)`，`N > 1 ∧ M > 1`——V5 接口显式拒绝**

该组合（单 `x`、多 `weight`、多 `out`）在 V5 接口下被直接拒绝，理由是张量形式的 group list 与「输出按分组拆分为多张量」的语义冲突。

  > 旧文档「groupType 支持场景」表中列出 `groupType == 0` 的三种张量数组合（单单单、单多单、多多单），未列出单多多组合，与实现一致；但旧文档「不同 groupType 约束」表中 `groupType == 0` 的「多多单」一行写「仅支持 splitItem 为 2」，而实现接受 `splitItem ∈ {2, 3}`（`splitItem` 为 `3` 时随后会按 `len(x) == 1` 与否被归一化为 `2`，见 2.4 与下文归一化说明）。这属于旧文档比实现更严格的实质差异，实现层面不会因 `splitItem == 3` 而在组合 C 上报错。
  >
  > 另有一处差异：旧文档「不同 groupType 约束」表就 `groupType == 0` 的「单多单」与「多多单」两行均写「**仅支持非量化**」（「单单单」一行无此限定），而实现的分组形态校验**完全不读取数据类型**，各 dtype 场景的专属校验也不反过来限制张量数组合——A8W8（见 3.6）只禁 `groupType == 2`、A8W4（见 3.8）连 `groupType` 都不读，两者在组合 B、组合 C 上都不会被拒绝。因此旧文档禁止的「量化 + 单多单／多多单」输入会被实现放行。
  >
  > 关于 65535 上限（旧文档公共约束：「x 和 weight 中每一组 tensor 的最后一维大小都应小于 65536。`x_i` 的最后一维指当 x 不转置时 `x_i` 的 K 轴或当 x 转置时 `x_i` 的 M 轴。`weight_i` 的最后一维指当 weight 不转置时 `weight_i` 的 N 轴或当 weight 转置时 `weight_i` 的 K 轴」），回源结果如下：
  >
  > - **Ascend 950PR/Ascend 950DT 对应架构完全不施加该上限。**其它平台的 NO_SPLIT 与 SPLIT_M 组合 A/B/C 始终分别检查 `x[i][-1]`、`x[0][1]`、`x[0][1]`、`weight[i][0]` 所表示的 K 轴；各组合的 K 轴等式同时约束另一侧张量。
  > - 其它平台的 NO_SPLIT 与 SPLIT_M 组合 A/B/C 分别通过 `weight[i][1]`、`weight[0][2]`、`out[0][1]`、`out[0][1]` 检查 N 轴，且这些 N 轴上限仅在 `transposeWeight` 为假时生效。`transposeWeight` 为真时传入的「不校验」哨兵只跳过这一次后序 N 轴检查，不会撤销此前的 K 轴检查。
  > - SPLIT_K 的单 `weight`、单 `out` 分支在其它平台检查 `x[0][0]`（M 轴）与 `weight[0][1]`（N 轴）。多 `weight` 或多 `out` 分支虽在 API 层跳过跨张量一致性检查，但 infer 阶段仍检查每个 `weight[i][1] ≤ 65535`。

- **`splitItem` 的事后归一化**：在全部参数校验通过之后、算子图构建之前，`splitItem` 会被按 `x`/`out` 的实际长度关系重新推导：`splitItem ∈ {0, 1}` 时被改写为「`len(x) == len(out)` ? `0` : `1`」；`splitItem ∈ {2, 3}` 时被改写为「`len(x) == 1` ? `3` : `2`」。归一化后的值**不再经过任何参数校验**，直接进入算子图构建。因此调用方在 `{0, 1}` 之间、或在 `{2, 3}` 之间的选择对最终行为无影响，与旧文档「aclnn 接口不感知 0/1 的差异，2/3 同理」的说明一致。

#### 3.1.3 groupType == 2（K 轴分组）

- `len(x) == 1`（`x` 不得为按分组拆分的多张量列表）。
- `transposeX` 必须为真。
- `biasOptional` 必须为空。
- `ndim(x[0]) == 2`；对每个 `i`，`ndim(weight[i]) == 2`。
- `x`、`weight` 的每个元素均须格式合法（见 2.10），且每个 `weight` 元素的转置态与 `transposeWeight` 一致。
- 当且仅当 `len(weight) == 1 ∧ len(out) == 1` 时，还须满足下列跨张量约束（语义形状为 `x(K, M)`、`weight(K, N)`、`out(E, M, N)`）：
  - `ndim(out[0]) == 3`。
  - `dim(x[0], 1) == dim(weight[0], 0)`（K 轴）。
  - `dim(x[0], 0) == dim(out[0], 1)`（M 轴）。
  - `dim(weight[0], 1) == dim(out[0], 2)`（N 轴）。
  - 非 Ascend 950PR/Ascend 950DT 平台：`dim(x[0], 0) ≤ 65535` 且 `dim(weight[0], 1) ≤ 65535`。
  - `groupListOptional` 必须非空；`groupNum = dim(out[0], 0)`，按 2.5 的分组场景条款校验。
- 当 `len(weight) > 1` 或 `len(out) > 1` 时，上述整段跨张量形状一致性与 group list 校验被**整体跳过**。该情形下 `x`、`weight`、`out` 三者之间的 K/M/N 轴一致性不被校验，`groupListOptional` 也不被校验（可为空，非空时不校验其形状、长度与数据类型）；仍会执行前述逐元素维数/格式/转置态校验，并且在非 Ascend 950PR/Ascend 950DT 平台的 infer 阶段要求每个 `dim(weight[i], 1) ≤ 65535`。该校验缺口的其它后果见 4.4。

  > 旧文档「groupType 支持场景」表中 `groupType == 2` 的「单多多」一行给出了一整套约束（`x`/`weight`/`out` 均为 2 维；weight 长度最大 128；原始形状中 weight 各张量第一维之和不超过 `x` 第一维；bias 必须传空；groupListOptional 可传空但若传入则须满足取值关系）。这与分析结果实质冲突：实现在 `len(weight) > 1` 或 `len(out) > 1` 时跳过跨张量 K/M/N 一致性、`out` 维数与 group list 校验；但仍保留逐元素维数/格式/转置态校验及非 Ascend 950PR/Ascend 950DT 平台的 `weight[i][1] ≤ 65535` infer 检查。已回源核实：API 层跨张量校验整段被包裹在 `len(weight) == 1 ∧ len(out) == 1` 的条件内，infer 层另行执行上述内轴上限检查。「bias 必须传空」与「weight 长度最大 128」两条也由其它环节实现（分别见本小节与 2.9），与旧文档一致。
  >
  > **`splitItem` 在整个 `groupType == 2` 路径上从不被读取。** 旧文档「不同 groupType 约束」表就 `groupType == 2` 的「单单单」一行写「仅支持 splitItem 为 2/3」、「单多多」一行写「仅支持 splitItem 为 0/1」，「groupType 支持场景」表亦分别写 2/3 与 0/1；返回值表另写「splitItem 为 2、3 的场景，out 长度不等于 1」触发 `ACLNN_ERR_PARAM_INVALID`。**旧文档要求这些取值限制，入口实际不校验**【已回源核实：K 轴分组的校验函数及其全部传递被调函数中都不出现 `splitItem`，该标识符在本文件中的读取点全部落在 M 轴分组与不分组的分支内】。因此 `groupType == 2` 下 `splitItem` 取 `{0,1,2,3}` 中任意值、与 `len(out)` 的任意搭配都被放行；校验通过后 `splitItem` 才被按 `x`/`out` 的实际长度关系无条件改写（见 3.1.2 末尾的归一化说明），改写值不再复核。
  >
  > 另需指出：该分支下 `len(out)` 也不受任何长度上限约束（见 2.9 的对照注）。

### 3.2 Ascend 950PR/Ascend 950DT——非量化场景

适用条件：运行平台为该架构，且 `dtype(x) ∈ {ACL_FLOAT16, ACL_BF16, ACL_FLOAT}`、`dtype(weight[0]) == dtype(x)`。

- `actType == 0`。
- 其余约束与 3.5 相同（含该架构上 `bias` 数据类型的放宽），并按 2.9 使用 1024 的列表长度上限。

### 3.3 Ascend 950PR/Ascend 950DT——静态量化、动态量化（T-T/T-C/K-T/K-C）、动态量化（MX）与动态量化（G-B）场景

适用条件：运行平台为该架构，且 `dtype(x) ∈ {ACL_FLOAT4_E2M1, ACL_INT4, ACL_FLOAT4_E1M2}` 或 `dtype(x)` 与 `dtype(weight[0])` 的单元素字节宽度均为 1。

#### 3.3.1 静态量化与动态量化的公共约束

- `len(x) == 1 ∧ len(weight) == 1 ∧ len(out) == 1`。
- `groupListOptional` 必须非空。
- `offsetOptional` 必须为空。
- `groupType != 1`（即只能取 `0` 或 `2`；取 `-1` 亦不被本条拒绝，但 2.4 已限定取值集合，而下文各 dtype 分支普遍进一步要求 `groupType == 0`）。
- 若 `dtype(out[0]) != ACL_INT32`，`scaleOptional` 必须非空。
- `format(x[0])` 与 `format(out[0])` 必须为非私有格式；`format(weight[0])` 必须为非私有格式或 `ACL_FORMAT_FRACTAL_NZ`。
- 若 `format(weight[0]) == ACL_FORMAT_FRACTAL_NZ`：V5 接口被拒绝（该 NZ 专属校验只接受 WeightNz 版本接口）。
- 记 `groupNum = dim(groupListOptional, 0)`。逐元素形状校验：
  - 若 `dim(x[0], 0) == 0`（M 轴为 0）或 `dim(weight[0], -1) == 0`（N 轴为 0），该校验整段被提前放行（后果见 4.8）。此处的 N 轴恒取 `weight` 的**末维**，与 `transposeWeight` 无关。
  - 否则若 `groupType == 2`：`dim(out[0], 0) == groupNum`。
  - 否则：`dim(x[0], 1) > 0` 且 `dim(weight[0], -2) > 0`。
- `scaleOptional` 与 `perTokenScaleOptional`（各自非空时）的列表长度必须等于 `len(x)`；例外：`scaleOptional` 为空且 `dtype(out[0]) == ACL_INT32` 时该长度校验被豁免。
- 若 `dtype(x) == ACL_INT8 ∧ dtype(weight[0]) == ACL_INT8`，`actType` 可非零，条件见 3.3.2；其余 dtype 组合下 `actType` 必须为 `0`。
- 接口版本为 V3 时才会触发的额外校验对 V5 不适用（V5 不进入该分支，相应的危险输入构造在 V5 上不可达，见 4.9）。
- 按 `(dtype(x), dtype(weight[0]))` 分派至下列四类之一，不在其中的组合一律被拒绝：
  - `ACL_INT8` × `ACL_INT8` → 3.3.2。
  - `ACL_HIFLOAT8` × `ACL_HIFLOAT8` → 3.3.3（另要求 `dtype(scale[0]) ∈ {ACL_UINT64, ACL_FLOAT, ACL_INT64}`）。
  - `{ACL_FLOAT8_E4M3FN, ACL_FLOAT8_E5M2}` × `{ACL_FLOAT8_E4M3FN, ACL_FLOAT8_E5M2}` → 3.3.4。
  - `{ACL_FLOAT4_E2M1, ACL_FLOAT4_E1M2}` × `{ACL_FLOAT4_E2M1, ACL_FLOAT4_E1M2}` → 3.3.5。
- `antiquantScaleOptional` 与 `antiquantOffsetOptional` 在本路径的完整可见规划链上没有形成空性、长度、维数、数据类型或形状 guard；普通非空列表会保持非空并继续进入构图、推导和分块规划。

  > 旧文档 Ascend 950PR/Ascend 950DT 一节的四个全量化子场景——静态量化、动态量化（T-T && T-C && K-T && K-C）、动态量化（mx 量化）、动态量化（G-B 量化）——均要求 `antiquantScaleOptional`、`antiquantOffsetOptional` 为空。实现的专用参数检查、公共归一化、shape/dtype 推导和全量化分块规划均不以二者的空性形成失败条件；因此在其它条件成立时，非空 antiquant 输入不会因该字段本身收到错误信号。
  >
  > 同四节首条中的 `offsetOptional` 须为空、`activationInputOptional` 须为空两项则确由本节与 2.2 实现，与旧文档一致。

#### 3.3.2 静态量化与动态量化（T-T/T-C/K-T/K-C）：ACL_INT8 × ACL_INT8

- `groupType == 0`。
- `transposeX` 必须为假。
- `dtype(out[0]) ∈ {ACL_INT8, ACL_INT32, ACL_BF16, ACL_FLOAT16}`。
- 若 `biasOptional` 非空，`dtype(bias[0])` 须落在按 `dtype(out[0])` 确定的集合内：
  - `dtype(out[0]) == ACL_BF16` → `{ACL_INT32, ACL_BF16, ACL_FLOAT}`。
  - `dtype(out[0]) == ACL_FLOAT16` → `{ACL_INT32, ACL_FLOAT16, ACL_FLOAT}`。
  - `dtype(out[0]) ∈ {ACL_INT8, ACL_INT32}` → `{ACL_INT32}`。
- 若 `dtype(out[0]) == ACL_INT32` 且 `scaleOptional` 为空：本节其余校验（`scale`/`perTokenScale` 的数据类型、维数、形状）整体被跳过，直接通过。
- 否则 `dtype(scale[0])` 须落在按 `dtype(out[0])` 确定的集合内：
  - `ACL_BF16` → `{ACL_BF16, ACL_UINT64, ACL_INT64, ACL_FLOAT}`。
  - `ACL_FLOAT16` → `{ACL_UINT64, ACL_INT64, ACL_FLOAT}`。
  - `ACL_INT8` 或 `ACL_INT32` → `{ACL_UINT64, ACL_INT64}`。
- 若 `perTokenScaleOptional` 非空：
  - `dtype(out[0]) != ACL_INT8`。
  - `dtype(perTokenScale[0]) == ACL_FLOAT`。
  - `dtype(out[0]) == ACL_BF16` 时另要求 `dtype(scale[0]) ∈ {ACL_BF16, ACL_FLOAT}`；`dtype(out[0]) == ACL_FLOAT16` 时另要求 `dtype(scale[0]) == ACL_FLOAT`。
- 若 `dtype(out[0]) != ACL_INT32`，还须满足维数与形状约束（记 `groupNum = dim(groupListOptional, 0)`）：
  - `ndim(scale[0]) ∈ {1, 2}`；`dtype(out[0]) == ACL_INT8` 时须 `ndim(scale[0]) == 2`。
  - `perTokenScaleOptional` 非空时：`ndim(perTokenScale[0]) ∈ {1, 2}`。
  - `dim(scale[0], 0) == groupNum`。
  - `ndim(scale[0]) > 1` 时：`dim(scale[0], -1) ∈ {1, dim(weight[0], -1)}`；且 `dtype(out[0]) == ACL_INT8` 时须严格 `dim(scale[0], -1) == dim(weight[0], -1)`。
  - `perTokenScaleOptional` 非空时（`groupType == 0`）：`ndim(perTokenScale[0]) == 1` 时须 `dim(perTokenScale[0], 0) ∈ {dim(x[0], 0), groupNum}`；`ndim(perTokenScale[0]) != 1` 时须 `dim(perTokenScale[0], 0) == groupNum` 且 `dim(perTokenScale[0], 1) == 1`。
  - 在上述之后另有一道更强的约束：`perTokenScaleOptional` 非空时，`ndim(perTokenScale[0])` 必须恰为 `1`，且 `dim(perTokenScale[0], 0) == dim(x[0], 0)`。该约束与前一条中「`ndim != 1` 时的分支」互相矛盾——`ndim(perTokenScale[0]) == 2` 的输入即使通过前一条也会被本条拒绝，故 `ACL_INT8`×`ACL_INT8` 场景下 `perTokenScale` 实际只接受 1 维、长度等于 `dim(x[0], 0)` 的形状。
- 激活的额外条件：`actType != 0` 时，须同时满足 `dtype(x) == ACL_INT8`、`dtype(weight[0]) == ACL_INT8`、`actType ∈ {1, 2, 4, 5}`，且量化模式落在下列两者之一：
  - **静态 pertensor-perchannel**：`perTokenScaleOptional` 为空，且 `scaleOptional` 非空、`ndim(scale[0]) == 2`、`dim(scale[0], -1) == dim(weight[0], -1)`、`dim(scale[0], 0) == dim(weight[0], 0)`，且 `dtype(scale[0])` 与 `dtype(out[0])` 匹配（`out` 为 `ACL_BF16` 时 `scale ∈ {ACL_FLOAT, ACL_BF16}`；`out` 为 `ACL_FLOAT16` 时 `scale == ACL_FLOAT`）。
  - **动态 pertoken-perchannel**：`perTokenScaleOptional` 非空、`dtype(perTokenScale[0]) == ACL_FLOAT`、`ndim(perTokenScale[0]) == 1`、`dim(perTokenScale[0], 0) == dim(x[0], ndim(x[0]) - 2)`，且 `scaleOptional` 满足上一条中同样的 `scale` 形状与 dtype 要求。

  > 旧文档 Ascend 950PR/Ascend 950DT 一节的静态量化表列出 `groupType` 为 `0/2` 的多行组合（含 `HIFLOAT8`、`FLOAT8` 在 `groupType == 2` 下受支持），但 `ACL_INT8`×`ACL_INT8` 各行均只标 `groupType` 为 `0`，与实现「`ACL_INT8`×`ACL_INT8` 强制 `groupType == 0`」一致。旧文档就 `perTokenScaleOptional` 给出「`groupType` 为 0、x 单 tensor 时，pertoken 场景每个 tensor 1 维、shape 为 `(M,)`；pertensor 场景 2 维或 1 维、shape 为 `(g, 1)` 或 `(g,)`，**输入为 INT8 时不支持 pertensor 场景**」——该行末的限定语把 pertensor 形态排除在 `ACL_INT8` 输入之外，即旧文档对 `ACL_INT8`×`ACL_INT8` 同样只允许 1 维、shape `(M,)`。已回源核实：该路径在通用形状校验之后，另有一段无条件执行的 `perTokenScale` 维数与 M 轴校验，要求维数恰为 1 且首维等于 `dim(x[0], 0)`，与旧文档在该 dtype 组合上的最终接受集完全一致，**不构成实质冲突**。需要指出的是本节正文所述的源码内部矛盾（前一道规则允许 2 维、后一道强制 1 维）依然成立，只是它是实现内部的冗余，不是与旧文档的分歧。

#### 3.3.3 静态量化、动态量化（T-T/T-C/K-T/K-C）与动态量化（G-B）：ACL_HIFLOAT8 × ACL_HIFLOAT8

- `dtype(scale[0]) ∈ {ACL_UINT64, ACL_FLOAT, ACL_INT64}`。
- `biasOptional` 必须为空。
- 转置态：`groupType == 0` 时 `transposeX` 必须为假；`groupType == 2` 时 `transposeX` 必须为真且 `transposeWeight` 必须为假。
- `dtype(scale[0]) ∈ {ACL_UINT64, ACL_INT64}` 时：`groupType` 必须为 `0`。`dtype(scale[0]) == ACL_FLOAT` 时：`groupType` 可为 `0` 或 `2`。
- 若 `perTokenScaleOptional` 非空：`dtype(perTokenScale[0]) == ACL_FLOAT` 且 `dtype(scale[0]) == ACL_FLOAT`。
- `dtype(out[0]) ∈ {ACL_FLOAT, ACL_BF16, ACL_FLOAT16}`。
- 随后按是否为 per-tile 量化模式二选一：
  - 非 per-tile 模式：`scale` 与 `perTokenScale` 的维数与形状按 3.3.2 中「维数与形状约束」同一套规则校验（`ndim(scale[0]) ∈ {1, 2}`、`dim(scale[0], 0) == groupNum`、N 轴关系、以及按 `groupType` 区分的 `perTokenScale` 形状规则；其中 `groupType == 2` 时 `perTokenScale` 的规则为：`dim(perTokenScale[0], 0) == groupNum`，且 `ndim > 1` 时 `dim(perTokenScale[0], 1) ∈ {dim(x[0], 0), 1}`）。
  - per-tile 模式（G-B 量化）：改由 per-tile 专属规则校验，其内容见下。该模式的判定本身只反映最后一个分组的形状关系，见 4.10。

**per-tile（G-B 量化）模式的专属形状约束**（对每个分组下标 `i` 生效；`groupNum = dim(groupListOptional, 0)`）：

- `ndim(x[i]) == 2`。
- `groupType == 0` 时 `ndim(weight[i]) == 3`；`groupType == 2` 时 `ndim(weight[i]) == 2`。
- `groupType == 0` 且 `dtype(scale[0]) != ACL_FLOAT8_E8M0` 时：`ndim(scale[i]) == ndim(weight[i])`，且 `ndim(perTokenScale[i]) == ndim(x[i])`。
- `scale[i]` 的转置态必须等于 `transposeWeight`；例外：`scale[i]` 末两维大小均为 1 时豁免该要求。
- `perTokenScale[i]` 的转置态必须等于 `transposeX`；例外：`perTokenScale[i]` 末两维大小均为 1 时豁免，或满足「特殊 per-tile 场景」时豁免。**特殊 per-tile 场景**指同时满足：`groupNum > 1`、`groupType == 2`、`dim(weight[i], K 轴) < 128`、`dim(weight[i], N 轴) ≤ 128`、`dim(x[i], 0) > 1`、`dim(x[i], 0) == dim(perTokenScale[i], 0)`。
- `dim(x[i], 0) == dim(perTokenScale[i], 0)`（M 轴）。
- `dim(scale[i], N 轴) == ceil(dim(weight[i], N 轴) / 128)`。
- `dim(perTokenScale[i], 1) == dim(scale[i], K 轴)`，且该公共值须等于：`groupType == 0` 时 `ceil(dim(weight[i], -2) / 128)`；`groupType == 2` 时 `floor(dim(weight[i], -2) / 128) + groupNum`。注意该 K 轴基数取自 **`weight` 的次末维**而非 `x` 的 K 轴，且 `groupType == 2` 分支用的是截断除法而非向上取整。

  > 旧文档「动态量化（G-B量化）场景约束」一节给出 `gsN = gsK = 128`，与上述 128 一致；`groupType == 0` 的 `scale` 形状 `(g, ceil(K/gsK), ceil(N/gsN))`、`perTokenScale` 形状 `(M, ceil(K/gsK))` 也与上述结论一致。但旧文档就 `groupType == 2` 的 `perTokenScale` 写「每个 tensor 2 维，shape 为 `(K/gsK + g, M)`」，与实现的轴序**相反**：实现要求 `dim(perTokenScale, 0)` 等于 `x` 的 M 轴、`dim(perTokenScale, 1)` 等于 `dim(weight, -2) / 128 + groupNum`，即形状为 `(M, K/128 + g)`（此处的 `K` 取自 **`weight` 的次末维**，与上文一致）。已回源核实：该校验的 M 轴比较与 K 轴比较分别取 `perTokenScale` 的第 0 维与第 1 维，无按 `groupType` 交换轴序的分支。按旧文档构造 `(K/gsK + g, M)` 形状的 `perTokenScale`，除非 `M == K/128 + g` 恰好相等，否则会被入口拒绝。
  >
  > 另：旧文档同节写「以下入参为空：biasOptional、offsetOptional、antiquantScaleOptional、antiquantOffsetOptional、activationInputOptional」，其中 `antiquantScaleOptional`、`antiquantOffsetOptional` 两项**入口实际不校验**【已回源核实】——Ascend 950PR/Ascend 950DT 全量化校验器全文不出现这两个字段，且该架构下全量化路径在通用校验中被提前分派、绕过通用的 antiquant 置空校验。

#### 3.3.4 静态量化、动态量化（T-T/T-C/K-T/K-C）、动态量化（MX）与动态量化（G-B）：ACL_FLOAT8_E4M3FN / ACL_FLOAT8_E5M2 两两组合

按 `dtype(scale[0])` 分派：

- `dtype(scale[0]) == ACL_FLOAT8_E8M0` → mx（block-scale）量化路径：
  - `perTokenScaleOptional` 必须非空。
  - `dtype(perTokenScale[0]) == ACL_FLOAT8_E8M0`。
  - `biasOptional` 必须为空（该条为 `ACL_FLOAT8_E4M3FN`/`ACL_FLOAT8_E5M2` 的 mx 路径入口处的硬性前置条件；`bias` 非空时按 `ACL_FLOAT` 校验其数据类型与形状的规则只在 3.3.5 的 fp4 mx 路径上真正生效）。
  - `dtype(out[0]) ∈ {ACL_FLOAT16, ACL_BF16, ACL_FLOAT}`。
  - 维数：`ndim(x[0]) == 2`；`groupType == 0` 时 `ndim(weight[0]) == 3`，`groupType == 2` 时 `ndim(weight[0]) == 2`；`groupType == 0` 时另要求 `ndim(scale[0]) == 4` 且 `ndim(perTokenScale[0]) == 3`。
  - **mx（block-scale）形状等式**，按 `groupType` 分派，对每个分组下标 `i` 生效（`groupNum = dim(groupListOptional, 0)`，block size 为 **64**）：

    **`groupType == 0`（M 轴分组）**

    - `dim(x[i], 0) == dim(perTokenScale[i], 0)`（M 轴）。
    - `dim(weight[i], -1) == dim(scale[i], ndim(weight[i]) - 1)`（N 轴；`scale` 的 N 轴按 `weight` 的 N 轴下标索引）。
    - `dim(scale[i], 0) == groupNum`。
    - `dim(scale[i], 1) == ceil(dim(x[i], 1) / 64)`（K 轴）。
    - `dim(perTokenScale[i], 1) == ceil(dim(x[i], 1) / 64)`（K 轴）。
    - `dim(scale[i], -1) == 2`；`dim(perTokenScale[i], -1) == 2`。
    - 若 `biasOptional` 非空：`ndim(bias[i]) == 2`、`dim(bias[i], 0) == groupNum`、`dim(bias[i], 1) == dim(weight[i], -1)`。

    **`groupType == 2`（K 轴分组）**

    - 维数：`ndim(x[i]) == 2`、`ndim(weight[i]) == 2`、`ndim(scale[i]) == 3`、`ndim(perTokenScale[i]) == 3`。
    - `dim(x[i], 0) == dim(perTokenScale[i], 0)`（M 轴）。
    - `dim(weight[i], 1) == dim(scale[i], 1)`（N 轴）。
    - `dim(perTokenScale[i], 1) == floor(dim(x[i], 1) / 64) + groupNum`。
    - `dim(scale[i], 0) == floor(dim(x[i], 1) / 64) + groupNum`。
    - `dim(scale[i], 2) == 2`；`dim(perTokenScale[i], 2) == 2`。

    这些等式在读取各维取值前不重新校验维数下界与下标有效性，由此继承的越界风险见 4.11。

  > 旧文档「动态量化（mx量化）场景约束」一节与上述结论有三处实质差异，均已回源核实：
  >
  > 其一，旧文档写「计算公式中量化 block size 为：gsM = gsN = 1，**gsK = 32**」，而实现使用的除数为 **64**；旧文档自身的形状表用的也是 `ceil(K / 64)`，与该句自相矛盾。按 `gsK = 32` 构造 `scale`/`perTokenScale` 的 K 轴长度会被入口拒绝。
  >
  > 其二，旧文档就 `groupType == 0` 的 `scale` 给出**两种**布局（`weight` 转置时 `(g, N, ceil(K/64), 2)`、不转置时 `(g, ceil(K/64), N, 2)`），而实现的形状校验**不按 `transposeWeight` 分支**：它恒以 `weight` 的 N 轴下标索引 `scale` 的 N 轴、以固定下标 1 索引 `scale` 的 K 轴，即只承认非转置布局。`weight` 转置时按旧文档构造 `(g, N, ceil(K/64), 2)` 的 `scale` 会被拒绝（`N == ceil(K/64)` 巧合相等时除外）。
  >
  > 其三，旧文档就 `groupType == 2` 的 `perTokenScale` 写「3 维，shape 为 `((K/64) + g, M, 2)`」，与实现的轴序**相反**：实现要求 `dim(perTokenScale, 0)` 等于 `x` 的 M 轴、`dim(perTokenScale, 1)` 等于 `K/64 + groupNum`，即 `(M, K/64 + g, 2)`。
  >
  > 另：旧文档同节写「以下入参为空：offsetOptional、antiquantScaleOptional、antiquantOffsetOptional、activationInputOptional」，其中 `antiquantScaleOptional`、`antiquantOffsetOptional` 两项**入口实际不校验**【已回源核实，理由同 3.3.3 的对照注】。
- `dtype(scale[0]) ∈ {ACL_FLOAT, ACL_UINT64, ACL_INT64}` → 转入 3.3.3 所述的非 mx 通用校验。
- 其余 `dtype(scale[0])` 一律被拒绝。

#### 3.3.5 动态量化（MX）：ACL_FLOAT4_E2M1 / ACL_FLOAT4_E1M2 两两组合

- `groupType == 0`。
- 若 `dtype(x) == ACL_FLOAT4_E1M2` 或 `dtype(weight[0]) == ACL_FLOAT4_E1M2`：`format(weight[0])` 不得为 `ACL_FORMAT_ND`（要求 `ACL_FORMAT_FRACTAL_NZ`）。但按 3.3.1，`format(weight[0]) == ACL_FORMAT_FRACTAL_NZ` 在 V5 上被拒绝。二者合并的结论是：**任一侧数据类型为 `ACL_FLOAT4_E1M2` 的组合在 V5 接口上不可用**——weight 为 ND 格式时被本节拒绝，weight 为 NZ 格式时被 3.3.1 的版本校验拒绝。
- `dtype(scale[0]) == ACL_FLOAT8_E8M0`；其余取值一律被拒绝。
- `perTokenScaleOptional` 必须非空，`dtype(perTokenScale[0]) == ACL_FLOAT8_E8M0`。
- `transposeX` 必须为假。
- 若 `biasOptional` 非空：`dtype(bias[0]) == ACL_FLOAT`。
- `dtype(out[0]) ∈ {ACL_FLOAT16, ACL_BF16, ACL_FLOAT}`。
- 维数：`ndim(x[0]) == 2`、`ndim(weight[0]) == 3`、`ndim(scale[0]) == 4`、`ndim(perTokenScale[0]) == 3`。
- **mx 形状等式**：本路径 `groupType` 被强制为 `0`，故 3.3.4 中 `groupType == 0` 一栏的全部 mx 形状等式（含 `biasOptional` 非空时的 `ndim(bias[i]) == 2`、`dim(bias[i], 0) == groupNum`、`dim(bias[i], 1) == dim(weight[i], -1)`）在本路径上同样生效。
- **fp4 专属的轴取值约束**（对每个分组下标 `i` 生效）：
  - 若 `transposeWeight` 为假：`dim(weight[i], -1) % 2 == 0`（N 轴为偶数）。`transposeWeight` 为真时该条整体跳过。
  - `dim(x[i], 1) % 2 == 0`（K 轴为偶数），无条件生效。
  - `dim(x[i], 1) != 2` 且 `dim(weight[i], -2) != 2`（`x` 与 `weight` 的 K 轴均不得为 `2`）。**不存在「N 轴不得为 2」的规则**：N 轴只受上述偶数约束，且仅在 `transposeWeight` 为假时生效。
- 上述等式与取值约束在读取各维取值前不重新校验维数下界与下标有效性，由此继承的越界风险见 4.11。

  > x 与 weight 的 K 是同一矩阵乘收缩维；后续 shape 推导要求两者相等。因此分别检查 `dim(x, 1) != 2` 与 `dim(weight, -2) != 2` 不会形成两个独立限制：满足公共 K 相等关系时，任意一侧的 `K != 2` 已经蕴含另一侧同一条件。

### 3.4 Ascend 950PR/Ascend 950DT——伪量化场景

适用条件：运行平台为该架构，且 `dtype(x)` 与 `dtype(weight[0])` 的单元素字节宽度不相等，且不满足 3.3 的全量化判定。

#### 3.4.1 数据流与接口版本

按 `(dtype(x), dtype(weight[0]))` 划分数据流，各数据流对接口版本的要求如下（V5 可用与否直接决定该组合在本接口上是否可达）：

| 数据流 | `dtype(x)` | `dtype(weight[0])` | 接口版本要求 | V5 是否可用 |
|---|---|---|---|---|
| A16W8-ND | `ACL_FLOAT16` / `ACL_BF16` | `ACL_INT8` | `groupType == -1` 时版本非 WeightNz；否则版本非 WeightNz 且非 V1 | 可用 |
| A16F8-ND | `ACL_FLOAT16` / `ACL_BF16` | `ACL_FLOAT8_E4M3FN` / `ACL_FLOAT8_E5M2` / `ACL_HIFLOAT8` | 版本 ∈ {V4, V5} | 可用 |
| A16W4 | `ACL_FLOAT16` / `ACL_BF16` | `ACL_INT4` | 版本 ∈ {V4, V5} | 可用 |
| A16MxFp4-NZ | `ACL_FLOAT16` / `ACL_BF16` | `ACL_FLOAT4_E2M1` | 版本 == WeightNz | **不可用** |
| MxA8W4-NZ | `ACL_FLOAT8_E4M3FN` | `ACL_FLOAT4_E2M1` | 版本 == WeightNz | **不可用** |
| S8S4-NZ | `ACL_INT8` | `ACL_INT4` | 版本 == WeightNz | **不可用** |
| 其余组合 | — | — | 一律拒绝 | 不可用 |

因此在 Ascend 950PR/Ascend 950DT 上经 V5 接口可达的伪量化数据流只有 A16W8-ND、A16F8-ND、A16W4 三类，下文条款按这三类展开。

#### 3.4.2 分组形态与转置

- `actType == 0`。
- `groupType ∈ {-1, 0}`（A16W8-ND 与 A16W4 均允许这两种；A16F8-ND 由 3.4.1 的版本表进入后，`groupType` 只允许 `0`，因为该数据流不属于「A16W8-ND 或 A16W4」这一放宽集合）。
- `groupType == -1` 时：`len(x) == len(weight) == len(out)`；`splitItem ∈ {0, 1}`；`groupListOptional` 必须为空。
- `groupType == 0` 时：`len(x) == len(weight) == len(out) == 1`；`splitItem ∈ {2, 3}`；`groupListOptional` 必须非空。
- `transposeX` 必须为假。
- A16F8-ND 数据流：`transposeWeight` 必须为真。
- A16W8-ND 与 A16W4 数据流：`transposeWeight` 不受本节额外约束。
- `format(x[i])` 与 `format(out[i])` 必须为非私有格式；A16W8-ND / A16F8-ND / A16W4 三类数据流要求 `format(weight[i])` 为非私有格式（不接受 NZ）。

#### 3.4.3 维数、数据类型与形状

按 `wIdx ∈ [0, len(weight))` 逐分组校验，其中 `xIdx = min(wIdx, len(x) - 1)`、`yIdx = min(wIdx, len(out) - 1)`：

- `x[xIdx]`、`weight[wIdx]`、`out[yIdx]` 非空；非空的 `antiquantScale`/`antiquantOffset`/`bias` 在下标 `wIdx` 处非空。
- `dtype(x[xIdx]) == dtype(x[0])`、`dtype(weight[wIdx]) == dtype(weight[0])`、`dtype(out[yIdx]) == dtype(out[0])`。
- `dtype(out[0]) == dtype(x)`。
- 维数：
  - `groupType == -1`：`ndim(weight[wIdx]) == 2`；若判定为 A16W4 per-group（即 A16W4 数据流且 `ndim(antiquantScale[wIdx])` 等于 per-channel 维数加一，`groupType == -1` 时 per-channel 维数为 1，故 per-group 维数为 2）则 `ndim(x[xIdx]) == 2`，否则 `ndim(x[xIdx]) ∈ [2, 6]`。
  - `groupType == 0`：`ndim(x[xIdx]) == 2`；`ndim(weight[wIdx]) == 3`。
  - `ndim(x[xIdx]) == ndim(out[yIdx])`。
- 形状：
  - 对每个 `k ∈ [0, ndim(x[xIdx]) - 1)`：`dim(x[xIdx], k) == dim(out[yIdx], k)`。
  - `dim(x[xIdx], -1) == dim(weight[wIdx], -2)`（K 轴）。
  - `dim(out[yIdx], ndim(x[xIdx]) - 1) == dim(weight[wIdx], -1)`（N 轴）。
- `weight[wIdx]` 的转置态必须与 `transposeWeight` 一致。
- 若 `dtype(weight[0]) == ACL_INT4`：`dim(weight[wIdx], weight[wIdx] 处于转置态 ? -2 : -1)` 必须为偶数。
- 若 `biasOptional` 非空：`dtype(bias[0])` 须为 `{ACL_BF16, ACL_FLOAT}` 之一（`dtype(x) == ACL_BF16` 时）或 `ACL_FLOAT16`（`dtype(x) == ACL_FLOAT16` 时）；其列表长度须等于 `len(weight)`；其形状按 3.4.5 的通用规则校验。
- `scaleOptional`、`offsetOptional`、`perTokenScaleOptional` 必须全部为空（A16W8-ND / A16F8-ND / A16W4 三类数据流均不属于允许 `scale`/`perTokenScale` 非空的 S8S4-NZ、MxA8W4-NZ 数据流）。
- `antiquantScaleOptional` 必须非空；例外：`groupType == -1` 且 `len(weight) == 1` 且 `dim(weight[0], -1) == 0` 时可为空——但该「合法」输入组合在 A16W4 数据流下会触发 4.12 所述的危险输入构造。
- `antiquantOffsetOptional` 在 A16F8-ND 数据流下必须为空；在 A16W8-ND、A16W4 数据流下不受本条约束。
- `dtype(antiquantScale[wIdx]) == dtype(x)`（A16W8-ND / A16F8-ND / A16W4 三类均如此）；`antiquantOffsetOptional` 非空时 `dtype(antiquantOffset[wIdx]) == dtype(x)`。

#### 3.4.4 A16W4 数据流的 per-group 附加约束

记 per-channel 维数 `pc = (groupType == 0) ? 2 : 1`，per-group 维数 `pg = pc + 1`。

- `ndim(antiquantScale[wIdx]) ∈ {pc, pg}`。
- `ndim(antiquantScale[wIdx]) > pc`（per-group 模式）时：
  - `antiquantScale[wIdx]` 的各维大小不得全部为 1。
  - `antiquantScale[wIdx]` 不得处于转置态。
  - `antiquantOffsetOptional` 非空时，`antiquantOffset[wIdx]` 同样不得各维全 1、不得处于转置态。
- 分组粒度约束（对 A16W4 per-group 生效）：记 `kSize = dim(weight[wIdx], -2)`、`groupNum = dim(antiquantScale[wIdx], -2)`，要求：
  - `groupNum > 0`。
  - `kSize % groupNum == 0`。
  - `groupSize = kSize / groupNum` 必须落在 `{32, 64, 128, 256}` 之内。
- 参数校验先按下标独立要求每个 `groupSize` 落在 `{32, 64, 128, 256}`；分块规划随后以第 0 项为基准，要求所有后续项的 `groupSize` 与其相等。因此多 weight 的完整 workspace 成功条件还包括 `groupSize[i] == groupSize[0]`。
- 转置 weight 时不需要另一条独立奇偶 guard：上述固定集合的每个成员均为偶数，故完整成功条件已经蕴含 `groupSize % 2 == 0`。

#### 3.4.5 可选张量列表长度与形状（本架构伪量化路径专属）

- `len(antiquantScaleOptional) == len(weight)`（非空时）；`len(antiquantOffsetOptional) == len(weight)`（非空时）；`len(biasOptional) == len(weight)`（非空时）；`len(perTokenScaleOptional) == len(x)`（非空时）；`len(scaleOptional) == len(weight)`（非空时）。
- 对 `antiquantScale`/`antiquantOffset`/`bias` 的每个下标 `wIdx`：
  - 期望维数 `expectedDimNum`：A16W4 数据流的 antiquant 参数按上文 `{pc, pg}` 判定；其余情形为 `(groupType == 0) ? 2 : 1`。
  - 实际维数须等于 `expectedDimNum`。
  - `groupType == 0` 且 `weight` 不是「多张量形态」（`ndim(weight[0]) != 2`）时：该张量第 0 维（批大小）须等于 `dim(weight[wIdx], 0)`。
  - 该张量末维须等于 `dim(weight[wIdx], -1)`。

### 3.5 Atlas A3/A2 系列产品——非量化场景（亦作为其它平台的通用非量化约束）

适用条件：`dtype(x) ∈ {ACL_FLOAT, ACL_FLOAT16, ACL_BF16}` 且 `dtype(weight[0]) == dtype(x)`。

- `actType == 0`。
- `dtype(x[i]) == dtype(x)`（对每个 `i`）；`dtype(weight[i]) == dtype(x)`（对每个 `i`）；`dtype(out[i]) == dtype(x)`（对每个 `i`）。列表内为空指针的元素在 dtype 比对时被跳过，但 2.1 已要求 `x`/`weight`/`out` 的元素全部非空。
- `biasOptional` 的数据类型要求（记 `biasDtype` 为其期望值）：
  - 一般情形：`dtype(x) == ACL_BF16` 时 `biasDtype = ACL_FLOAT`，否则 `biasDtype = dtype(x)`。
  - 在 Ascend 950PR/Ascend 950DT 对应架构上且 `biasOptional` 非空时：`dtype(bias[0])` 必须等于 `dtype(x)` 或 `ACL_FLOAT`，且该实际值即为 `biasDtype`；此时另要求 `scaleOptional`、`offsetOptional`、`antiquantScaleOptional`、`antiquantOffsetOptional`、`perTokenScaleOptional` 五者全部为空。
  - 若 `biasOptional` 非空，其列表内每个非空元素的 dtype 均须等于 `biasDtype`。
- `scaleOptional`、`offsetOptional`、`perTokenScaleOptional` 必须全部为空。
- `antiquantScaleOptional`、`antiquantOffsetOptional` 必须全部为空。

  > 旧文档非量化场景表给出的三档 dtype 组合（`FLOAT`×`FLOAT`→`FLOAT`、`FLOAT16`×`FLOAT16`→`FLOAT16`、`BFLOAT16`×`BFLOAT16`→`BFLOAT16`，bias 为对应 dtype 或 `FLOAT`/空）与上述结论一致。旧文档 Ascend 950PR/Ascend 950DT 一节额外给出 `BFLOAT16` 场景 bias 可为 `BFLOAT16`，也与该架构下的放宽规则一致。

### 3.6 Atlas A3/A2 系列产品——全量化-A8W8 场景

适用条件：`dtype(x) == ACL_INT8` 且 `dtype(weight[0]) == ACL_INT8`，运行平台非 Ascend 950PR/Ascend 950DT 对应架构（该架构上的 `ACL_INT8`×`ACL_INT8` 见 3.3.2）。

- `out` 列表内每个非空元素的 dtype 须与 `dtype(out[0])` 相同，且 `dtype(out[0]) ∈ {ACL_INT8, ACL_BF16, ACL_FLOAT16, ACL_INT32}`。
- 若 `biasOptional` 非空：`dtype(bias[0]) ∈ {ACL_INT32, ACL_BF16}`。
- 若 `dtype(out[0]) == ACL_INT32`：视为「仅需要原始整型累加结果」，本节其余量化参数校验被**整体跳过**（`scaleOptional`、`offsetOptional`、`perTokenScaleOptional` 的存在性、数据类型与形状均不再被校验，`antiquantScaleOptional`/`antiquantOffsetOptional` 的互斥性也不再被校验）。该跳过是入口层面确定的行为，本文档如实记录，不代其展开在该分支下的下游要求。
- 若 `dtype(out[0]) != ACL_INT32`，须同时满足：
  - 运行平台不是 Atlas 推理系列产品（310P）。（该条只是本环节的重复保险：2.14 的「`dtype(x)` 与 `dtype(weight[0])` 均须为 `ACL_FLOAT16`」在该平台上无条件生效、且位于场景分派之前，`ACL_INT8` 输入在那里就已被拒。故 `dtype(out[0]) == ACL_INT32` 使本条被跳过，并不会让 A8W8 在 310P 上变得可用。）
  - `groupType != 2`。
  - `offsetOptional` 必须为空。
  - `scaleOptional` 必须非空。
  - `antiquantScaleOptional`、`antiquantOffsetOptional` 必须全部为空。
  - `scaleOptional` 与 `weight` 的结构一致性按 3.10 判定（`tensorType` 为 `scale`，非伪量化 int4 场景）。展开后为：
    - `len(scaleOptional) == len(weight)`。
    - 记 `isSingleWeight = (len(weight) == 1 ∧ groupType != -1)`：
      - `isSingleWeight` 为真时：`ndim(scale[0]) == 2`；`dim(scale[0], 0) == groupNum`；`dim(scale[0], ndim(scale[0]) == 4 ? -2 : -1) == dim(weight[0], -1)`。此处 `groupNum` 按下列优先级取值：`len(x) > 1` 时取 `len(x)`；否则 `len(weight) > 1` 时取 `len(weight)`；否则 `len(out) > 1` 时取 `len(out)`；否则 `groupListOptional` 非空时取 `dim(groupListOptional, 0)`；否则取 1。
      - `isSingleWeight` 为假时：对每个 `i`，`scale[i]` 非空、`ndim(scale[i]) == 1`、`dim(scale[i], -1) == dim(weight[i], -1)`。
  - `scale` 与 `perTokenScale` 的数据类型组合，须按 `perTokenScaleOptional` 是否非空分为两套受支持集合，且逐元素成立：
    - `perTokenScaleOptional` 为空（per-channel / per-tensor 静态量化）：对每个 `i`，`(dtype(scale[i]), dtype(out[0]))` 必须落在
      `{(ACL_INT64, ACL_INT8), (ACL_UINT64, ACL_INT8), (ACL_BF16, ACL_BF16), (ACL_FLOAT, ACL_FLOAT16)}`
      四个组合之一。
    - `perTokenScaleOptional` 非空（per-token 动态量化）：对每个 `i`，`(dtype(scale[i]), dtype(out[0]))` 必须落在
      `{(ACL_BF16, ACL_BF16), (ACL_FLOAT, ACL_FLOAT16)}`
      两个组合之一；且对每个 `i`，`dtype(perTokenScale[i]) == ACL_FLOAT`。
  - 若 `perTokenScaleOptional` 非空，另须满足：
    - `len(x) == 1 ∧ len(out) == 1`（`x`、`out` 必须是单张量；否则该量化模式不受支持）。
    - `len(perTokenScaleOptional) == 1`。
    - `ndim(perTokenScale[0]) == 1`。
    - `dim(perTokenScale[0], 0) == dim(x[0], 0)`（长度等于 `x` 的 M 轴）。

  > 旧文档 A8W8 场景数据类型表中，`scale` 为 `ACL_UINT64` 对应 `out` 为 `ACL_INT8`、`scale` 为 `ACL_BF16` 对应 `out` 为 `ACL_BF16`、`scale` 为 `ACL_FLOAT` 对应 `out` 为 `ACL_FLOAT16`、`scale` 为空对应 `out` 为 `ACL_INT32` 四行，与上述受支持组合一致；分析结果另给出旧文档在 Atlas A2/A3 一节未列出的 `(ACL_INT64, ACL_INT8)` 组合（旧文档在平台差异说明中写「输入参数 scaleOptional 不支持 INT64 类型」，仅在 Ascend 950PR/Ascend 950DT 一节列出 `INT64`）。这一点上分析结果与旧文档实质冲突：入口层面的 dtype 组合表对所有平台一视同仁地接受 `ACL_INT64`，且在参数组装阶段所有 `ACL_INT64` 的 `scale` 元素会被**就地改写**为 `ACL_UINT64`（该改写作用于调用方传入的张量对象本身，调用返回后可观察到 dtype 已变），并不因平台而异。已回源核实：`scale` 元素的 `ACL_INT64` 到 `ACL_UINT64` 就地改写是一段无平台条件的循环，dtype 组合表中的 `(ACL_INT64, ACL_INT8)` 也无平台限定。
  >
  > 本场景另有五处差异，方向均为实现比旧文档更宽，全部已回源核实：
  >
  > 其一，**`groupType` 只被要求 `!= 2`，而非 `== 0`**。旧文档 A8W8 一节写「仅支持 GroupType=0（M 轴分组）」，「groupType 支持场景」表亦写「A8W8、A8W4、A4W4 场景仅支持 groupType 为 0 场景中 x tensor 数为单」。实现在 A8W8 专属校验中只有一条 `groupType != SPLIT_K` 的拒绝，`groupType == -1`（不分组）的 A8W8 输入被放行。更进一步：这条拒绝位于 `dtype(out[0]) == ACL_INT32` 的提前返回**之后**，故 `out` 为 `ACL_INT32` 时连 `groupType != 2` 都不再校验。
  >
  > 其二，**三个列表长度均为 1 不被校验**。旧文档写「当前仅支持 x、weight、out 均为长度为 1 的 TensorList」，实现的 A8W8 专属校验中不存在任何 `len(...) == 1` 判定；多张量 A8W8 输入按 3.1.2 的分组形态组合被放行。
  >
  > 其三，**`bias` 的 `ACL_BF16` 在所有平台上被接受**。旧文档 A8W8 数据类型表的 `bias` 列只写 `INT32/null`，平台差异一节另明写「Atlas A2/A3：输入参数 biasOptional 不支持 BFLOAT16」。实现的 A8W8 `bias` dtype 校验接受 `{ACL_INT32, ACL_BF16}`，该判定**无任何平台门控**，且不随 `dtype(out[0])` 收窄——`out` 为 `ACL_INT8` 或 `ACL_INT32` 时 `ACL_BF16` 的 `bias` 同样被放行。
  >
  > 其四，**`dtype(out[0]) == ACL_INT32` 时被跳过的不止量化参数的 dtype 与形状**。旧文档该行明写 `offset`、`antiquantScale`、`antiquantOffset`、`perTokenScale` 四者均须为 `null`，而正文所述的整体跳过同时越过了「`offsetOptional` 必须为空」「`antiquantScaleOptional`/`antiquantOffsetOptional` 必须为空」「`scaleOptional` 必须非空」三条存在性校验。故 `out` 为 `ACL_INT32` 时，非空的 `offsetOptional`、`antiquantScaleOptional`、`antiquantOffsetOptional` 一律被放行。
  >
  > 其五，**`perTokenScaleOptional` 非空时不要求 `len(weight) == 1`**。旧文档公共约束写「perTokenScaleOptional……仅支持 x、weight、out 均为单 tensor（TensorList 长度为 1）场景」，实现的该项校验只判 `len(x) == 1 ∧ len(out) == 1`，`len(weight)` 虽被取出却只用于错误提示文本、不参与判定；即「单 x／多 weight／单 out」组合下的 per-token 动态量化被放行，而该组合的错误提示文本恰恰声称它不受支持。
  >
  > 此外，旧文档写「x 仅支持 2 维 Tensor，Shape 为（M，K）」「weight 仅支持 3 维 Tensor」，实现不设 A8W8 专属的维数限制，`x`/`weight` 的维数完全由所处分组形态决定（`groupType == -1` 下 `ndim(x[i]) ∈ [2,6]`、`ndim(weight[i]) == 2`；组合 B/C 下 `weight` 为 2 维）。

### 3.7 Atlas A3/A2 系列产品——全量化-A4W4 场景

适用条件：`dtype(x) == ACL_INT4` 且 `dtype(weight[0]) == ACL_INT4`。在 Ascend 950PR/Ascend 950DT 上该组合被拒绝（见 2.15）。

- `groupType == 0`（M 轴分组）且 `len(x) == 1 ∧ len(weight) == 1 ∧ len(out) == 1`。这是本场景专属校验末尾的一条硬性复合约束，`groupType` 为 `-1` 或 `2`、或三个列表中任一长度大于 1 的 A4W4 输入均被本节直接拒绝，与 3.1.2 组合 A 的分组形态校验并列、互不替代。
- `groupListType ∈ {0, 1, 2}`（受 2.6 进一步限制：取值 2 还须 `groupType == 0` 与平台条件）。
- `dtype(out[0]) ∈ {ACL_FLOAT16, ACL_BF16}`。
- `offsetOptional` 必须为空。
- `biasOptional` 必须为空。
- `scaleOptional` 必须非空，且 `dtype(scale[0]) == ACL_UINT64`。
- `scale` 的形状：
  - `ndim(scale[0]) ∈ {2, 3}`。
  - `dim(scale[0], -1) % 8 == 0`（N 轴按 8 对齐）。
  - `ndim(scale[0]) == 3`（per-group 模式）时：记 `G = dim(scale[0], 1)`、`K = dim(x[0], 1)`，要求 `G != 0`、`K % G == 0`，且 `(K / G) % 2 == 0`（每组长度为偶数）。
- 若 `perTokenScaleOptional` 非空：
  - `dtype(perTokenScale[0]) == ACL_FLOAT`。
  - `len(x) == 1 ∧ len(out) == 1`。
  - `len(perTokenScaleOptional) == 1`；`ndim(perTokenScale[0]) == 1`；`dim(perTokenScale[0], 0) == dim(x[0], 0)`。
- `antiquantScaleOptional`、`antiquantOffsetOptional` 必须全部为空。
- `actType` 在入口层面不被本场景额外拦截（见 2.7 与 4.7）。

  > 旧文档 A4W4 场景约束写「perchannel 场景的 scale 的 shape 需为 `[E, N]`，pergroup 场景需为 `[E, G, N]`」「pergroup 场景下 G 必须能整除 K，且 `K/G` 需为偶数」，与上述结论方向一致。旧文档另写「仅支持 GroupType=0（M 轴分组）」「x、weight、out 均为长度为 1 的 TensorList」，与本节首条硬性复合约束逐字对应【已回源核实：该复合约束就在 A4W4 专属校验函数的末尾】；「x 仅支持 2 维」「weight 仅支持 3 维」则由 3.1.2 组合 A 的分组形态校验承担。以下三点与旧文档不一致：
  >
  > 其一，N 轴 8 对齐校验作用于 `scale` 的末维，代码本身不以 `format(weight)` 为前提。但合法的未转置 INT4 `ACL_FORMAT_FRACTAL_NZ` weight 已由格式 shape 要求保证 N 为 64 的倍数，因此必然同时满足 N 为 8 的倍数；该无格式前提的 N/8 guard 不会在合法 NZ 输入域内增加新的拒绝集合。
  >
  > 其二，旧文档写本场景「actType=0」，实现在入口层面**不**对 A4W4 的 `actType` 做超出 2.7 通用范围的拦截，仅在检测到核函数未实现该激活组合时记录警告日志并放行【已回源核实】，见 4.7。
  >
  > 其三，旧文档写「x 不支持转置，weight 为 NZ 格式时支持转置，ND 格式仅支持非转置」，实现只有 2.12 的通用转置态规则（`groupType == 0` 时 `transposeX` 必须为假），**没有任何按 `weight` 格式区分转置态的 A4W4 专属规则**【已回源核实：全部 A4W4 专属校验函数中不出现按 `format(weight[0])` 分派的转置判定】；旧文档另写「开启右矩阵 NZ 转置后，`K/G` 必须按 64 对齐、K 按 64 对齐、N 按 16 对齐」——这三条中**只有 `K/G` 那条在入口校验中不存在**，K 与 N 两条确由 NZ 对齐校验强制、与旧文档一致：`format(weight[i])` 属 `ACL_FORMAT_FRACTAL_NZ` 系列时，`weight` 转 NZ 的流程对每个元素施加按数据类型分档的 K/N 对齐硬性拒绝，`ACL_INT4` 且 `transposeWeight` 为真时正是「K 按 64 对齐、N 按 16 对齐」【已回源核实】；该流程在非 Ascend 950PR/Ascend 950DT 平台上对 A4W4 可达（其 950 专属豁免同时要求该架构与全量化判定）。详见 4.16。故本条真正无人拦截的只有「ND 格式仅支持非转置」与「`K/G` 按 64 对齐」两项。
  >
  > 其四，旧文档就 `scale` 写「perchannel 场景的 shape 需为 `[E, N]`，pergroup 场景需为 `[E, G, N]`」，其中的 **`E` 轴（第 0 维）从不被校验**【已回源核实：A4W4 的 `scale` 形状校验只读取其维数、末维与第 1 维，第 0 维一次都不被读取，既不与 `dim(weight[0], 0)` 比较也不与分组数比较；A4W4 的 `scale` 也不经过 3.10 的通用可选张量结构一致性校验——该通用校验的调用点只有 A8W8 的 `scale`、`antiquantScale`、`antiquantOffset` 与 `bias` 四处】。因此 `dim(scale[0], 0)` 与 `weight` 的 `E` 轴不一致的输入会被放行。同理，`scale` 的列表长度与 `len(weight)` 的相等关系在 A4W4 场景下也不被校验。

### 3.8 Atlas A3/A2 系列产品——伪量化-A8W4 场景

适用条件：`dtype(x) == ACL_INT8` 且 `dtype(weight[0]) == ACL_INT4`。在 Ascend 950PR/Ascend 950DT 上该组合被拒绝（见 2.15）。

本场景按 `offsetOptional` 的存在性与形状分为两个子场景，判定规则为：

记 `E = dim(weight[0], 0)`、`N = dim(weight[0], 2)`。当且仅当 `offsetOptional` 非空、`ndim(offset[0]) == 3`、`dim(offset[0], 0) == E`、`dim(offset[0], 1) == 1`、`dim(offset[0], 2) == N` 时，判定为**非对称量化子场景**；其余情形（含 `offsetOptional` 为空）判定为**对称量化子场景**。

#### 3.8.1 非对称量化子场景

- `dtype(out[0]) == ACL_FLOAT16`。
- `dtype(offset[0]) == ACL_FLOAT`。
- `biasOptional`、`scaleOptional`、`perTokenScaleOptional` 三者均须非空。
- `dtype(bias[0]) == ACL_FLOAT`、`dtype(scale[0]) == ACL_UINT64`、`dtype(perTokenScale[0]) == ACL_FLOAT`。
- `antiquantScaleOptional`、`antiquantOffsetOptional` 必须全部为空。
- 形状关系（记 `M = dim(x[0], 0)`，`E`、`N` 同上）：
  - `ndim(bias[0]) == 2` 且 `bias[0]` 形状为 `(E, N)`。
  - `ndim(scale[0]) == 3` 且 `scale[0]` 形状为 `(E, 1, N)`。
  - `ndim(perTokenScale[0]) == 2` 且 `perTokenScale[0]` 形状为 `(M, 1)`。
- 上述形状关系是真实生效的强校验，任一不满足即被拒绝。
- `groupListType` 不等于 `1` 时仅记录警告日志，不构成拒绝。

#### 3.8.2 对称量化子场景

本子场景的**专属**校验环节形同虚设：负责 `scale`/`perTokenScale`/`bias` 存在性、数据类型与形状一致性的那个函数，对每一条不满足的条件都只记录警告日志、从不返回失败，其调用方也不检查该环节的返回结果。经该环节放行的违规包括：

- `scaleOptional` 为空、`perTokenScaleOptional` 为空；
- `dtype(scale[0]) != ACL_UINT64`、`dtype(perTokenScale[0]) != ACL_FLOAT`；
- `ndim(scale[0]) != 3`、`scale[0]` 形状与 `(E, ·, N)` 不符；
- `dim(x[0], 1) != dim(weight[0], 1)`（K 轴不匹配）；
- `dim(perTokenScale[0], 0) != M`；
- `dtype(bias[0])` 不是 `ACL_FLOAT`，以及 `biasOptional` 为空（旧文档要求本场景 `bias` 必选）。

以上条件均不会使本子场景的专属参数检查返回失败；完整 WorkspaceSize 成功仍须满足并列的结构检查、shape/dtype 推导、输出比对与分块规划。

**但 `bias` 的形状不在此列。** `biasOptional` 非空时，其**列表长度、维数、批大小与末维**由一个与本场景校验**并列执行**的通用可选张量校验环节硬性强制（即 3.10 所述规则；该环节在参数校验总入口下与场景分派并列，不受本场景提前返回的影响），展开为：`len(biasOptional) == len(weight)`、维数须等于按 `isSingleWeight` 确定的期望值、`dim(bias[0], 0) == groupNum`、`dim(bias[0], -1) == dim(weight[0], -1)`。因此对称量化子场景下，`bias` 真正无人拦截的只有**数据类型**与**「必须非空」**两项，形状相关的四项照常生效。

综上，本子场景的参数阶段不为上述 `scale`、`perTokenScale`、`bias` 条件提供拒绝保证；完整成功集合还须合取后续规划条件。相应的危险性见 4.5。

  > 旧文档 A8W4 场景约束给出了完整的数据类型与形状要求表，含 offset 为空与不为空两类子场景各自的 `scale` 形状规则（`[E, quantGroupNum, N]` 与 `[E, 1, N]`）、bias 形状 `[E, N]`、`k` 须为 `quantGroupSize` 的整数倍且不超过 18432、`n` 须为 8 的整数倍等，措辞上暗示这些约束在两类子场景下都会被强制执行。已回源核实：这些约束在非对称量化子场景（offset 不为空且形状匹配）下确为强校验，而在对称量化子场景（offset 为空的默认情形，即旧文档「offset 为空时」一整段所描述的场景）下，除 `bias` 形状一项外全部不构成拦截，与旧文档措辞存在实质冲突，见 3.8.2 与 4.5。**唯一的例外是 `bias` 的形状**：旧文档所写的 `bias` shape `[E, N]` 由一个与本场景校验并列执行的通用可选张量校验环节（见 3.10）硬性强制，在两个子场景下都生效，与旧文档一致；对称量化子场景下 `bias` 真正无人拦截的只有其**数据类型**与**「必须非空」**两项。旧文档所述的 `k ≤ 18432`、`k` 为 `quantGroupSize` 整数倍、`n` 为 8 的整数倍、`quantGroupSize == 256` 等数值约束，在两个子场景的校验环节中均未出现【已回源核实：入口实现全部文件中不出现 `18432`；`quantGroupSize` 仅作为 WeightNz 版本入口的形参出现并被显式丢弃，从不参与任何校验】。
  >
  > 本场景另有四处差异，方向均为实现比旧文档更宽，全部已回源核实：
  >
  > 其一，**`groupType` 在 A8W4 专属校验中完全不被读取**。旧文档写「仅支持 GroupType=0（M 轴分组），actType=0」，实现的 A8W4 专属校验函数体内不出现 `groupType`，`actType` 亦只在检测到核函数未实现该激活组合时记录警告日志（见 4.7）。`groupType == -1` 的 A8W4 输入按 3.1.2 之外的不分组形态校验通过后即被放行。
  >
  > 其二，**`x`/`weight` 的转置态无 A8W4 专属禁令**。旧文档写「x 不支持转置、weight 不支持转置」，实现只有 2.12 的通用规则：`groupType ∈ {0, -1}` 时 `transposeX` 必须为假、`transposeX` 与 `transposeWeight` 不得同时为真。`transposeWeight` 为真的 A8W4 输入不被拒绝。
  >
  > 其三，**三个列表长度均为 1 不被校验**。旧文档写「当前支持 x、out 均为长度为 1 的 TensorList」，「groupType 支持场景」表另写「A8W8、A8W4、A4W4 场景仅支持 groupType 为 0 场景中 x tensor 数为单」。实现的 A8W4 专属校验中不存在任何 `len(...)` 比较，全部代码只索引下标 `0` 处的元素，下标大于 0 的元素既不参与校验也不影响判定。
  >
  > 其四，**对称量化子场景下 `dtype(out[0])` 完全不被校验**。旧文档 A8W4 数据类型表把 `out` 限定为 `BFLOAT16`（offset 为空行）或 `FLOAT16`（offset 不为空行）。实现只在非对称量化子场景中校验 `dtype(out[0]) == ACL_FLOAT16`；对称量化子场景的校验环节从不读取 `out` 的数据类型，任意 `out` dtype 都被放行。
  > 完整规划链对这一字段还有确定的后续行为：`out` 的公开 dtype 被保留到分块规划；A8W4 分块只区分 `ACL_BF16` 与“其它”，前者选择 BFLOAT16 模板，所有其它 dtype 均选择 FLOAT16 模板。因而满足其它规划条件的 `ACL_INT8 out` 不因 dtype 被拒绝，并按 FLOAT16 模板编码进入分块键。该结论只描述接受与模板选择，不表示已经执行设备计算或证明错误数值、越界。
  >
  > `offsetOptional` 没有独立的 `len(offsetOptional) == len(weight)` guard；非对称量化分类与形状校验只读取其第 0 项。但这项局部缺失不能单独推出“多 weight 且 offset 长度不等”的完整成功场景：采用 A8W4 专属三维 weight 时，多 weight 拓扑要求每个 weight 为二维；采用二维 weight 时，非对称分类又会在维数 guard 前读取第三维。可确定的入口行为仅为：`ndim(offset[0]) != 3` 会被当作子场景分派条件，输入转入对称量化路径，而不是在该分类点被拒绝。

### 3.9 Atlas A3/A2 系列产品——伪量化-A16W8 与伪量化-A16W4 场景

适用条件：`dtype(x) ∈ {ACL_BF16, ACL_FLOAT16}` 且 `dtype(weight[0]) ∈ {ACL_INT8, ACL_INT4}`，运行平台非 Ascend 950PR/Ascend 950DT 对应架构（该架构上的伪量化见 3.4）。

- 运行平台不是 Atlas 推理系列产品（310P）。
- `groupType != 2`。
- `actType == 0`。
- 数据类型：`dtype(x[i]) == dtype(x)`（每个 `i`）；`dtype(weight[i]) == dtype(weight[0])`（每个 `i`）；`dtype(out[i]) == dtype(x)`（每个 `i`）；若 `biasOptional` 非空，其每个非空元素的 dtype 须为 `ACL_FLOAT`（`dtype(x) == ACL_BF16` 时）或 `ACL_FLOAT16`（`dtype(x) == ACL_FLOAT16` 时）。
- `antiquantScaleOptional` 必须非空。
- `antiquantOffsetOptional` 的必需性：记 `isAntiquantInt4 = (dtype(weight[0]) == ACL_INT4)`、`isSingleWeight = (len(weight) == 1 ∧ groupType != -1)`、`isA16W4Pergroup = (isAntiquantInt4 ∧ ¬isSingleWeight ∧ ndim(antiquantScale[0]) == 2)`。当 `(isAntiquantInt4 ∧ isSingleWeight)` 为真，或 `isA16W4Pergroup` 为真时，`antiquantOffsetOptional` 可为空；否则 `antiquantOffsetOptional` 必须非空。
- `antiquantScaleOptional`（以及非空的 `antiquantOffsetOptional`）须满足 3.10 的结构一致性；其中伪量化 int4 场景下维数范围放宽（见 3.10）。
- 数据类型：`dtype(antiquantScale[i]) == dtype(x)`（每个 `i`）；`antiquantOffsetOptional` 非空时同理 `dtype(antiquantOffset[i]) == dtype(x)`。
- 若 `isAntiquantInt4` 为真，另须满足 per-group 分组粒度的一致性：
  - 记 `pergroupSize(S, W)` 为由某个 antiquant 参数张量 `S` 与权重张量 `W` 推出的分组粒度，其计算规则为：当 `isSingleWeight` 为真且 `ndim(W) > 2` 且 `ndim(S) > 2` 时取 `dim(W, 1) / dim(S, ndim(S) - 2)`；当 `isSingleWeight` 为假或 `ndim(W) ≤ 2`，且 `ndim(S) > 1` 时取 `dim(W, 0) / dim(S, ndim(S) - 2)`；其余情形取 0（表示 per-group 概念不适用）。
  - 基准值 `base = pergroupSize(antiquantScale[0], weight[0])`。
  - 对每个 `i`：`ndim(antiquantScale[i]) == ndim(antiquantScale[0])`，且 `pergroupSize(antiquantScale[i], weight[0]) == base`。
  - `antiquantOffsetOptional` 非空时，对每个 `i` 同样要求维数与基准一致、推出的分组粒度等于 `base`。
  - 若 `transposeWeight` 为真：`base` 必须为偶数。
  - `pergroupSize` 计算中的除数 `dim(S, ndim(S) - 2)` 必须非 0，否则触发 4.6 所述的危险输入构造。
- `scaleOptional`、`offsetOptional`、`perTokenScaleOptional` 必须全部为空。

  > 旧文档 A16W4 / A16W8 场景约束给出了按「weight 单/多张量」与「perchannel/pergroup」四象限区分的 `antiquantScaleOptional`/`antiquantOffsetOptional` 形状表（`[E, N]` / `[n_i]` / `[E, G, N]` / `[G_i, n_i]`），以及「pergroup 数 G 必须整除对应的 k」「多 tensor 时各 `s_i = k_i / G_i` 都相等」「weight 转置时 pergroup 长度须为偶数」「groupSize 取值仅支持 32、64、128、256」等约束。分析结果与其中前三条方向一致（分别对应上文的维数一致性、分组粒度跨分组一致性、转置时 `base` 为偶数）。关于「groupSize 取值仅支持 32、64、128、256」：该句在旧文档中只出现于 Ascend 950PR/Ascend 950DT 伪量化一节，而非通用的 A16W4 场景约束一节；实现同样只在 Ascend 950PR/Ascend 950DT 的伪量化路径施加该离散集合校验（见 3.4.4），Atlas A2/A3 路径不施加。两者作用域一致，**不构成实质冲突**。
  >
  > 但同一节存在一处差异：旧文档 A16W4 场景约束写「对称量化支持 perchannel 和 pergroup 量化模式……**非对称量化仅支持 perchannel 模式**」，而实现**不存在任何禁止「pergroup + 非空 antiquantOffset」的判定**【已回源核实】。恰恰相反，实现的分组粒度一致性校验专门为非空的 `antiquantOffsetOptional` 计算并比对其 per-group 粒度（要求等于由 `antiquantScale` 推出的基准值），即 pergroup 与非空 offset 的组合是被显式支持的路径。旧文档禁止的「非对称 + pergroup」输入会被实现放行。

### 3.10 可选张量列表与 weight 的通用结构一致性（非 Ascend 950 路径）

本小节的适用范围**恰为下列四项，无其余**【已回源核实：该通用校验函数在全部实现文件中只有四个调用点】：

- A8W8 全量化场景的 `scaleOptional`（该场景要求其非空）；
- 伪量化场景的 `antiquantScaleOptional` 与 `antiquantOffsetOptional`（在各自被要求非空时）；
- `biasOptional`——**只要它非空即适用，与所处场景是否要求它非空无关**。该项与按数据类型分派的场景校验**并列执行**，因此在 3.8.2 那种场景专属校验提前返回的情形下同样生效。

`scaleOptional` 在 A4W4（3.7）与 A8W4（3.8）场景下**不**经过本小节，`offsetOptional` 与 `perTokenScaleOptional` 在任何场景下都不经过本小节。

平台范围：前三项只出现在非 Ascend 950PR/Ascend 950DT 的场景校验里；`biasOptional` 那一项挂在按 `groupType` 分派的那条支路上，故在**该架构的全量化场景下同样生效**，仅在该架构的**伪量化**场景下被整条支路的提前返回绕过（该架构的伪量化另有自己的一套可选列表规则，见 3.4.5）。

以下规则只对上述四项成立。记 `tensorType` 为该参数的角色名，`isAntiquantInt4 = (dtype(weight[0]) == ACL_INT4 ∧ tensorType 含 "antiquant")`，`isSingleWeight = (len(weight) == 1 ∧ groupType != -1)`，`groupNum` 按 3.6 所述优先级规则取值。

- `len(tensorList) == len(weight)`。
- `isSingleWeight` 为真时：
  - `tensorList[0]` 非空。
  - 维数：`isAntiquantInt4` 为假时须 `ndim(tensorList[0]) == 2`；`isAntiquantInt4` 为真时须 `ndim(tensorList[0]) ∈ {2, 3}`，且取 `3` 时另要求 `dim(tensorList[0], 1) > 0` 且 `dim(weight[0], 1) % dim(tensorList[0], 1) == 0`。
  - `dim(tensorList[0], 0) == groupNum`。
  - `dim(tensorList[0], ndim(tensorList[0]) == 4 ? -2 : -1) == dim(weight[0], -1)`。
- `isSingleWeight` 为假时，对每个 `i ∈ [0, groupNum)`：
  - `tensorList[i]` 非空。
  - 维数：`isAntiquantInt4` 为假时须 `ndim(tensorList[i]) == 1`；`isAntiquantInt4` 为真时须 `ndim(tensorList[i]) ∈ {1, 2}`，且取 `2` 时另要求 `dim(tensorList[i], 0) > 0` 且 `dim(weight[i], 0) % dim(tensorList[i], 0) == 0`。
  - `dim(tensorList[i], ndim(tensorList[i]) == 4 ? -2 : -1) == dim(weight[i], -1)`。
- 由 2.3 的归一化可知 `tensorList[0]` 恒非空，故上述「首元素非空」在归一化之后自动成立；下标大于 0 的元素为空指针的情形由本小节的逐元素判空拒绝。

---

## 四、Bug（危险输入）触发条件——UB 与语义计算错误

以下条目为分析所得的危险输入触发条件，一律照实记录，不做「是否为真实缺陷」的价值判断；具体处置由人工审阅后决定。可信度与存疑标记原样保留。可达性经与本入口的前置校验组合核实之后给出结论；凡结论涉及与旧接口文档不一致或涉及强断言之处，均已回源核实并注明。

### 4.1 参与就地解包的张量维数为 0，导致越界访问（未定义行为；可信度：低）

**触发构造**：`dtype(weight[0]) == ACL_INT32` 且 `ndim(weight[i]) == 0`（某个 `i`）；或 `dtype(x[0]) == ACL_INT32` 且 `ndim(x[i]) == 0`（某个 `i`）。

**表现**：解包过程以「视图维数减一」计算待改写的轴下标，维数为 0 时该无符号减法下溢为极大值，随后以该值索引形状数组，构成越界访问。

**可达性**：该解包发生在入口最先的必选输入非空校验之后、通用参数校验之前，此时尚无任何维数校验，故构造可达。0 维张量能否由创建侧构造出来未回源核实，故按分析所标低可信度记录。

### 4.2 K 轴分组场景下 `weight` 非首元素为空指针（未定义行为；可信度：高；经核实在本入口不可达）

**触发构造**（分析给出的原始条件）：`groupType == 2` 且 `len(weight) > 1` 且存在下标 `i ≥ 1` 使 `weight[i]` 为空指针。

**根因**：`groupType == 2` 时，对 `x`/`weight` 全列表逐元素判空的环节被跳过（见 2.13）；随后的转置态推导环节只显式判空 `weight[0]`，即对下标大于 0 的元素未判空就对整个列表做转置形态判定，该判定对每个下标无条件访问其形状信息，从而对空指针元素解引用。

**可达性核实**：本入口在进入上述流程之前，最先执行的必选输入非空校验已要求 `weight` 列表的**每一个**元素非空（见 2.1），与触发构造「存在某元素为空」互斥，故经 `aclnnGroupedMatmulV5GetWorkspaceSize` 无法构造出该输入。已回源核实：入口第一条校验对 `x`/`weight`/`out` 三个列表逐元素判空，该校验不受 `groupType` 影响、无任何跳过分支；转置态推导环节确实只判空两个列表的首元素。该条目按纪律保留记录，其可达性限定为「经本入口不可达，须由跳过全列表判空的其它调用路径触达」。

### 4.3 K 轴分组场景下 `x` 维数小于 2，导致越界访问（未定义行为；可信度：低）

**触发构造**：运行平台为 Ascend 950PR/Ascend 950DT 对应架构；`groupType == 2`；`dtype(x) == ACL_INT8` 且 `dtype(weight[0]) == ACL_INT8`；`actType != 0`；`perTokenScaleOptional` 非空且 `dtype(perTokenScale[0]) == ACL_FLOAT`；`ndim(x[0]) < 2`。

**表现**：判定「动态 pertoken-perchannel 量化模式」的环节以 `ndim(x[0]) - 2` 作为维度下标读取 M 轴，`ndim(x[0])` 小于 2 时该无符号减法下溢为极大值，构成越界索引。

**可达性**：`groupType == 2` 时对 `x` 的「维数落在 2 至 6 之间」校验被整体跳过（见 2.13），而该架构的全量化路径显式允许 `groupType ∈ {0, 2}`；该判定环节在 `groupType == 2` 场景下会先于任何对 `x` 维数的校验被执行（`x` 维数校验位于分组形态校验中，晚于场景分派）。分析标注可信度低。

### 4.4 K 轴分组、多 `weight` 或多 `out` 时跨张量形状校验被整体跳过（校验缺口；存疑标注：`self_consistency` 为 unknown）

**触发构造**：`groupType == 2`；`len(x) == 1`；`len(weight) > 1` 或 `len(out) > 1`。

**表现**：该组合下，`x`/`weight`/`out` 之间的 K/M/N 轴一致性、`out` 的维数、以及 `groupListOptional` 的形状/长度/数据类型校验被整体跳过（见 3.1.3），仅逐元素维数、格式与转置态校验生效。形状互不匹配的输入会通过入口校验并进入下游计算。

**存疑标注**：分析报告将该项标注为「是否属校验覆盖缺口取决于该参数组合是否在更上层已被排除为非法」，未确证其在下游是否被拦截，故标注为存疑（`self_consistency` 为 unknown），未定性为已确认缺陷。

### 4.5 A8W4 对称量化参数检查局部放行（高可信行为；完整后果须合取后续规划条件）

**触发构造**：运行平台非 Ascend 950PR/Ascend 950DT 对应架构；`dtype(x) == ACL_INT8` 且 `dtype(weight[0]) == ACL_INT4`；落入 3.8.2 的对称量化子场景（`offsetOptional` 为空，或非空但不满足 3.8 所述的 `(E, 1, N)` 形状匹配）；且 `scaleOptional`/`perTokenScaleOptional` 中至少一项不满足本应成立的存在性、数据类型或形状约束，或 `biasOptional` 为空、或其数据类型不是 `ACL_FLOAT`（`bias` 的形状不在触发构造之列，理由见下）。

**表现**：负责该子场景一致性校验的环节，对下列每一条不满足的条件都只记录警告日志，从不返回失败：`scaleOptional` 为空、`perTokenScaleOptional` 为空、`dtype(scale[0]) != ACL_UINT64`、`dtype(perTokenScale[0]) != ACL_FLOAT`、`ndim(scale[0]) != 3`、`dim(scale[0], 0) != dim(weight[0], 0)`、`dim(scale[0], -1) != dim(weight[0], 2)`、`dim(weight[0], 1) != dim(x[0], 1)`、`dim(perTokenScale[0], 0) != dim(x[0], 0)`；`bias` 方面则是 `dtype(bias[0]) != ACL_FLOAT` 与 `biasOptional` 为空两项。其调用方也不检查该局部结果。因而这些条件本身不会在参数阶段产生错误信号；完整 WorkspaceSize 是否成功仍由其余结构检查、推导、输出比对与分块规划决定。本条不单独宣称设备计算结果错误。

**不属于本条的部分**：`bias[0]` 的**形状**（以及 `biasOptional` 的列表长度）并不随之失效。该环节内确实也对 `bias` 形状做过一次只警告的比对，但 `biasOptional` 非空时另有一个**并列执行**的通用可选张量校验环节对其列表长度、维数、批大小与末维施加硬性拒绝（见 3.10 与 3.8.2），不受本场景提前返回的影响。因此 `bias` 形状不匹配的输入仍会被拒绝，不构成本条的触发构造。

**分类说明**：本条保留的是高可信的参数阶段控制流缺口。只有具体输入同时闭合后续规划成功并证明错误结果时，才可进一步定性为静默算错。

**与旧文档的关系**：旧文档 A8W4 场景约束表把对称量化（offset 为空）与非对称量化（offset 不为空）两类子场景的形状/数据类型要求并列给出，措辞上暗示两类都会被强制执行；实际在对称量化子场景下，除 `bias` 形状（由 3.10 的通用可选张量校验并列强制）之外，该表所列约束一条都不生效。该冲突已在 3.8 与 3.8.2 的对照说明中记录，并已回源核实。

### 4.6 A16W4 per-group 分组粒度计算中的整数除零（未定义行为；可信度：中；标注 unknown-reachability）

**触发构造**：运行平台非 Ascend 950PR/Ascend 950DT 对应架构；落入 3.9 的 A16W4 场景（`dtype(x) ∈ {ACL_BF16, ACL_FLOAT16}` 且 `dtype(weight[0]) == ACL_INT4`）；`antiquantScaleOptional` 非空且 `ndim(antiquantScale[i])` 达到触发除法的维数阈值（`isSingleWeight` 且 `ndim(weight[0]) > 2` 时阈值为 `ndim > 2`，否则为 `ndim > 1`）；且 `dim(antiquantScale[i], ndim(antiquantScale[i]) - 2) == 0`。

**表现**：分组粒度按 `dim(weight, K 轴) / dim(antiquantScale, -2)` 计算，除数为 0 时构成整数除零。

**可达性**：3.10 的结构一致性校验只检查维数与末维取值，不检查倒数第二维是否大于 0，故该除数为 0 的输入在到达分组粒度计算前未被拦截。分析标注为存疑（unknown-reachability），未确证 0 长维度张量能否由创建侧构造。

### 4.7 全量化场景激活组合的「仅警告不拦截」放行（表面成功；下游后果标注 unknown）

**触发构造**：运行平台非 Ascend 950PR/Ascend 950DT 对应架构；场景为 A8W4（`ACL_INT8`×`ACL_INT4`）、A4W4（`ACL_INT4`×`ACL_INT4`），或 A8W8（`ACL_INT8`×`ACL_INT8`）且 `dtype(out[0]) ∈ {ACL_INT8, ACL_INT32}`；`actType ∈ {1, 2, 4, 5}`。

**表现**：参数阶段判定该激活组合未被核函数实现支持，但只记录警告日志并继续后续规划，不因 `actType` 本身返回失败。完整 WorkspaceSize 成功仍须满足后续条件；即使成功，也不能由此推断第二段设备实现支持该激活组合。

**存疑标注**：核函数对该 `actType` 取值的实际处理路径未在分析范围内验证，其后果（是否忽略激活、是否产生错误结果、是否另有下游拦截）标注为 unknown。本条按「返回成功但下游行为不受本层保证」的元发现单列，不并入其它 Bug 条目。

### 4.8 Ascend 950 全量化路径中，退化形状导致逐元素形状校验被提前终止（语义计算错误；可信度：高）

**触发构造**：运行平台为 Ascend 950PR/Ascend 950DT 对应架构；进入 3.3 的全量化路径；`dim(x[0], 0) == 0`（M 轴为 0），或 `dim(weight[0], -1) == 0`（N 轴为 0）。**N 轴恒取 `weight` 的末维，与 `transposeWeight` 无关**——该处的轴下标由 `ndim(weight) - 1` 直接算出，无按转置态分支。

**表现**：逐元素形状校验循环在遇到上述退化条件时直接以成功返回整个校验函数（而非跳过该元素继续检查下一个），导致同一轮内后续的 K 轴正数校验、以及 `groupType == 2` 时的「`dim(out[0], 0) == groupNum`」校验被整体跳过：M 轴为 0 或 N 轴为 0 的输入，其 K 轴取值（含小于等于 0 的取值）与 `out` 首维和分组数的相等关系都不再被检查，直接进入下游。

**可达性限定**：本条不涉及「多子图之间互相掩盖」——3.3.1 已要求本路径 `len(x) == len(weight) == len(out) == 1`，该循环恒只执行一轮，不存在「靠前子图命中退化条件、后续子图被放行」的情形。本条的实际含义仅为：单一子图在 M 或 N 退化为 0 时，其余形状校验全部失效。

**附带观察**：该循环中 M 轴取值恒读取 `x[0]` 而非 `x[i]`，与 N 轴按 `weight[i]` 取值形成不对称；该不对称是否为「M 轴按设计跨子图共享同一取值」的有意简化，分析报告标注为需与维护者核实。

### 4.9 接口版本相关校验中的空指针解引用（未定义行为；可信度：高；经核实在 V5 上不可达）

**触发构造**（分析给出的原始条件）：运行平台为 Ascend 950PR/Ascend 950DT 对应架构；接口版本为 V3；进入全量化路径；`dtype(out[0]) == ACL_INT32` 且 `scaleOptional == nullptr`。

**根因**：全量化路径的「`scaleOptional` 非空」校验被 `dtype(out[0]) != ACL_INT32` 这一条件门控，故 `out` 为 `ACL_INT32` 时允许 `scaleOptional` 为空通过；而 V3 专属校验在校验 `dtype(out[0]) == ACL_INT8`（本应拒绝 `ACL_INT32`）之前，就已无条件读取 `scaleOptional` 的数据类型，构成空指针解引用。

**可达性核实**：该分支以「接口版本等于 V3」为门控条件，`aclnnGroupedMatmulV5GetWorkspaceSize` 恒以 V5 版本标识调用下游，故经本入口不可达。已回源核实：入口向共享实现传入的版本标识为 V5 常量，V3 专属校验的入口条件为版本等于 V3。该条目按纪律保留记录，其可达性限定为「须经 `aclnnGroupedMatmulV3GetWorkspaceSize` 触达」。

同理，Ascend 950 全量化路径中「weight 为 `ACL_FORMAT_FRACTAL_NZ` 时的专属校验」所继承的两类危险构造——其一是 `dtype(out[0])` 校验强度弱于其自身错误提示所声明的范围（实际只排除 `ACL_INT8`，而非限定在 `{ACL_FLOAT16, ACL_BF16, ACL_FLOAT, ACL_INT32}` 之内），其二是 `groupType == 2` 且 `ndim(weight[0]) < 2` 时的无符号下溢越界索引——同样以「接口版本等于 WeightNz 版本」为前置门控，经 V5 不可达。

### 4.10 Ascend 950 全量化 per-tile 模式判定只反映最后一个分组（分派误判；可信度：低）

**触发构造**：运行平台为 Ascend 950PR/Ascend 950DT 对应架构；进入 3.3.3 或 3.3.4 的非 mx 路径；`perTokenScaleOptional` 非空；分组数大于 1（`len(weight) > 1`）；各分组的 N/K 轴 per-tile 形状关系不一致。

**表现**：per-tile 量化模式的判定结果只反映最后一个分组是否满足 N/K 轴形状关系，而非全部分组的合取，可能把本应判为非 per-tile 的组合误判为 per-tile（或反之），从而分派到错误的一套形状校验，跳过本应执行的那一套。进入 per-tile 专属校验之后，其内部对 `perTokenScale` 的转置一致性豁免判定同样恒读取下标 0 处的张量而非按循环下标取值，可能误豁免或误拒绝某些分组。

**存疑标注**：分析报告将本条标注为继承性风险、可信度低，未确证具体可复现的张量构造。**可达性限定**：3.3.1 的「`len(x) == len(weight) == len(out) == 1`」是本路径上的硬性拒绝【已回源核实】，与本条触发构造中的「分组数大于 1」互斥，故**经本入口不可达**。该条目按纪律保留记录，其可达性限定为「须由不施加该长度约束的其它调用路径触达」。

### 4.11 Ascend 950 mx 量化形状校验中的越界访问与空指针解引用（未定义行为；可信度：低）

**触发构造**：运行平台为 Ascend 950PR/Ascend 950DT 对应架构；进入 3.3.4 或 3.3.5 的 mx 量化路径；某个分组下标处 `x`/`weight`/`scale`/`perTokenScale`（或非空的 `bias`）的实际维数低于该路径假设的下界（`ndim(x) != 2`、`ndim(weight) != 3`、`ndim(scale) < 3`、`ndim(perTokenScale) < 2`），或对应张量列表在该下标处越界/元素为空。

**表现**：mx 形状校验在读取各维取值前不重新校验维数下界与下标有效性，构成越界读取或对空指针解引用。

**存疑标注**：分析报告将本条标注为继承性风险、可信度低。需注意 3.3.4 与 3.3.5 已在其前置环节要求 `ndim(x[0]) == 2`、`ndim(weight[0]) == 3`、`ndim(scale[0]) == 4`、`ndim(perTokenScale[0]) == 3`，故触发构造需绕过这些前置校验；其可达性未回源核实。

### 4.12 Ascend 950 A16W4 场景下 `antiquantScaleOptional` 为空导致崩溃（未定义行为；可信度：高）

**触发构造**：运行平台为 Ascend 950PR/Ascend 950DT 对应架构；`dtype(x) ∈ {ACL_FLOAT16, ACL_BF16}` 且 `dtype(weight[0]) == ACL_INT4`（A16W4 数据流，V5 可用，见 3.4.1）；`groupType == -1`；`len(weight) == 1`（由 `groupType == -1` 的 `len(x) == len(weight) == len(out)` 推出三者均为 1）；`dim(weight[0], -1) == 0`；`antiquantScaleOptional == nullptr`。

**表现**：按 3.4.3 的规则，这一组合是被**显式允许**的合法输入（`antiquantScaleOptional` 在「不分组 + 单权重 + 权重末维为 0」的退化场景下可以为空）；但随后的维数与格式校验环节在判定是否为 A16W4 per-group 模式时，会对 `antiquantScaleOptional` 按下标解引用而不先判空，导致这个「上层认定合法」的输入触发崩溃，而非返回成功或返回错误信号。

**同根因的其它触点**：per-group 转置一致性校验与分组粒度校验也在同一条件下无判空解引用 `antiquantScaleOptional`；由于维数与格式校验在逐分组循环中位置更靠前，同一分组下标会先在该处崩溃，另两处在当前执行顺序下不构成独立触发点。

### 4.13 Ascend 950 伪量化场景下 `bias` 首元素判空滞后于数据类型读取（未定义行为；可信度：中；经归一化核实在本入口不可达）

**触发构造**（分析给出的原始条件）：运行平台为 Ascend 950PR/Ascend 950DT 对应架构；进入伪量化路径；`biasOptional` 非空；`bias[0]` 为空指针。

**根因**：`bias` 数据类型的读取位于逐分组循环之外、先于循环内的逐元素判空执行。

**可达性核实**：按 2.3，进入参数校验之前已执行可选张量列表的「语义空」归一化——首元素为空指针的可选列表会被整体置为空指针，因此「`biasOptional` 非空且 `bias[0]` 为空」这一状态在参数校验阶段不可能成立。已回源核实：归一化函数对 `biasOptional` 在内的 11 个可选字段逐一处理，其中「首元素为空指针即整体置空」一支不附加任何列表长度条件；该归一化在全部参数校验之前执行。该条目按纪律保留记录，其可达性限定为「须由跳过该归一化的其它调用路径触达」。

### 4.14 不分组 V1 接口 group list 越界下标访问（未定义行为；可信度：中；经核实在 V5 上不可达）

**触发构造**（分析给出的原始条件）：运行平台为 Ascend 950PR/Ascend 950DT 对应架构；进入伪量化路径；接口版本为 V1；`groupType == -1`；`ndim(x[idx]) == 2`；数组形式的 group list 非空且其元素个数不大于当前分组下标。

**可达性核实**：该分支以「接口版本等于 V1」为门控，且访问的是数组形式的 group list，而 V5 恒以 V5 版本标识调用下游、并恒以空指针传入数组形式 group list 通道，故经本入口不可达。该条目按纪律保留记录。

### 4.15 权重转 FRACTAL_NZ 流程与算子图底层构建的继承性风险（未定义行为；可信度：中至低）

**触发构造**：全部参数校验通过之后，`weight` 需要转换为 `ACL_FORMAT_FRACTAL_NZ` 的场景（接口版本为 WeightNz 版本，或 `format(weight[0]) == ACL_FORMAT_FRACTAL_NZ`）。该流程中存在三类继承性风险：

- 逐张量执行格式改写时，对内部创建的视图对象未判空即写入其格式与形状，创建失败时构成对空指针的写入。
- 非 Atlas 推理系列产品（310P）分支中，遍历 `weight` 每个元素读取其存储格式时未判空（该分支只显式判空 `x[0]`）。
- 该分支在遇到第一个非 `ACL_FORMAT_FRACTAL_NZ` 格式的元素时直接跳出循环且不报错，隐含「列表内格式全部一致」的假设；若列表内格式不一致且非 NZ 元素排在 NZ 元素之前，本应执行的 K/N 轴对齐校验会被整体跳过而放行。

**可达性**：接口版本为 WeightNz 版本这一条件经 V5 不可达；`format(weight[0]) == ACL_FORMAT_FRACTAL_NZ` 这一条件在 Ascend 950 全量化路径上被 3.3.1 拒绝，但在非 Ascend 950 路径与 Ascend 950 非量化路径上未被拒绝，故仍可达。第一类与第二类风险涉及的 `weight` 元素为空指针的前提，与 2.1 的逐元素判空互斥，经本入口不可达。

**存疑标注**：分析报告将本条整体标注为继承性归纳、可信度中至低，未逐一回源核实可复现的张量构造。

### 4.16 权重 NZ 对齐校验对未覆盖数据类型一律判失败（语义错误：失败原因归并；可信度：高）

**触发构造**：`weight` 需要按 `ACL_FORMAT_FRACTAL_NZ` 做 K/N 轴对齐校验；且 `dtype(weight[0])` 不属于 `{ACL_INT8, ACL_BF16, ACL_FLOAT16, ACL_INT4}`，或等于 `ACL_FLOAT4_E2M1` 但 `dtype(x)` 不属于 `{ACL_FLOAT16, ACL_BF16, ACL_FLOAT8_E4M3FN}`。

**表现**：该校验按数据类型分四类计算对齐要求（`ACL_INT8` 按 32/16 组合、`ACL_BF16`/`ACL_FLOAT16` 按 16/16、`ACL_INT4` 按 64/16 组合、`ACL_FLOAT4_E2M1` 配指定 `x` 类型时按 64/64），未命中任何一类时两个对齐标志保持初值 false，必然判定失败。其后果是把「该数据类型组合不受支持」与「受支持但未对齐」两种性质不同的失败原因归并为同一错误码，且错误提示文本只列举对齐规则、不指出数据类型不受支持，可能误导定位。本条不产生越界或崩溃，属失败原因归并的语义问题。

**参考数值**：`ACL_INT8` 的对齐要求为——`transposeWeight` 为真时 K 轴按 32 对齐、N 轴按 16 对齐，为假时 K 轴按 16 对齐、N 轴按 32 对齐；`ACL_BF16`/`ACL_FLOAT16` 为 K、N 轴均按 16 对齐；`ACL_INT4` 为 `transposeWeight` 为真时 K 轴按 64、N 轴按 16 对齐，为假时 K 轴按 16、N 轴按 64 对齐；`ACL_FLOAT4_E2M1` 为 K 轴按 64、N 轴按 64 对齐。

### 4.17 权重存储形状写回环节的返回值未被检查（静默失败风险；可信度：低）

**表现**：权重转 `ACL_FORMAT_FRACTAL_NZ` 流程中，一处存储形状写回环节存在失败通道（内部形状列表与新权重列表长度不一致），但其返回值未被调用方检查，失败时流程继续向下执行。分析认为该两个列表在构造上天然等长、该失败分支在实践中不可达，未确证为可实际触发的危险输入条件，按低可信度记录，供人工留意。

### 4.18 参数归一化后的 `splitItem` 不再复核（时序缺口；非未定义行为）

**表现**：`splitItem` 在全部参数校验通过之后被按 `x`/`out` 的实际长度关系重新推导（见 3.1.2 末尾），归一化后的取值不再经过任何参数校验就进入算子图构建。分析认为该归一化规则是确定性的、不产生非法取值，故未定性为缺陷，仅如实记录该时序关系。

### 4.19 workspace 查询与分块规划的失败边界

**表现**：入口参数校验和算子图构建通过后，workspace 查询会执行该节点登记的分块规划。分块规划中的 shape、对齐、跨张量一致性或模板选择条件失败时，完整 WorkspaceSize 调用不能归入成功输入；不能把“已到达 workspace 查询”解释为“入口已经成功”。

**含义**：本文档的成功场景已经合取可见分块规划条件。例如 Ascend 950 多 weight A16W4 per-group 的 `k_i/G_i` 相等关系虽然不在 API 参数检查中比较，却在 workspace 查询触发的分块规划中比较，仍属于本入口的成功必要条件。

### 4.20 A8W4 对称量化输出 dtype 的模板投影（规划语义风险）

**触发构造**：Atlas A2/A3 A8W4 对称量化；其它公开输入满足该场景的参数、shape、dtype 与分块条件；`dtype(out[0])` 取 `ACL_BF16`、`ACL_FLOAT16` 之外的值，例如 `ACL_INT8`。

**表现**：该公开输出 dtype 不在 A8W4 对称参数阶段被拒绝，并保持到分块规划。分块规划把 `ACL_BF16` 映射为 BFLOAT16 模板，把所有其它 dtype 统一映射为 FLOAT16 模板；因此 `ACL_INT8 out` 使用 FLOAT16 模板标识参与分块键生成。本条确认的是 dtype 未拒绝与模板投影不一致，不扩大为已经发生错误数值、越界或设备故障。
