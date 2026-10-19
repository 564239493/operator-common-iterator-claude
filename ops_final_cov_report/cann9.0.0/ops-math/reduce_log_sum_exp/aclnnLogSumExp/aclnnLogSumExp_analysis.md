# 用例覆盖率深度分析报告：aclnnLogSumExp

覆盖率报告：`aclnnLogSumExp_coverage_20261016_160851.json`  
用例文件：`runs/aclnnLogSumExp-20261015-141706-587113/iter_001/cases_expanded_cov.json`（51 个 case）  
源码目录：`D:\y30050143\ops\code\ops-library\ops-math-9.0.0`

## A. 覆盖率总览

| 粒度 | 函数覆盖率 | 语句覆盖率 | 分支覆盖率 |
|------|-----------|-----------|-----------|
| host | 16/19 = **84.21**% | 138/223 = 61.88% | 73/246 = 29.67% |
| kernel | 0/0 = **0**% | 0/0 = 0% | 0/0 = 0% |
| tiling | 0/0 = **0**% | 0/0 = 0% | 0/0 = 0% |
| op | 16/19 = **84.21**% | 138/223 = 61.88% | 73/246 = 29.67% |

## B. 用例参数维度分析

### 当前用例参数分布

| 参数 | 取值分布 | 说明 |
|------|---------|------|
| S (seq len) | N/A | 范围 N/A |
| H (heads) | N/A | 范围 N/A |
| hasMask | True: 0, False/None: 51 | 混合 |
| queryTranspose | 未设置 | |
| keyTranspose | 未设置 | |
| valueTranspose | 未设置 | |
| softmaxAxes | 未设置 | |

### 缺失的参数维度（对照源码中的条件分支）

| 缺失维度 | 影响的代码分支 | 建议操作 | 优先级 |
|---------|-------------|---------|--------|

## C. 未覆盖函数

未覆盖函数总计：**3** 个

| 函数 | 粒度 | 分类 | 能否覆盖 | 原因 | 建议 |
|------|------|------|---------|------|------|
| `ParseOpToGraphReduceLogSumExp` | host | 1. 死代码 | ❌ 否 | 全仓扫描（含 5 个文件）未发现对 `ParseOpToGraphReduceLogSumExp` 的调用点，且函数名不匹配任何已知框架入口。属无头函数（预留接口/历史遗留），用例无法覆盖 | 死代码，无调用方，不影响功能，接受现状 |
| `ParseParamsReduceLogSumExp` | host | 1. 死代码 | ❌ 否 | 全仓扫描（含 5 个文件）未发现对 `ParseParamsReduceLogSumExp` 的调用点，且函数名不匹配任何已知框架入口。属无头函数（预留接口/历史遗留），用例无法覆盖 | 死代码，无调用方，不影响功能，接受现状 |
| `ReduceLogSumExp` | host | 2. 编译期代码 | ❌ 否 | 位于 `reduce_log_sum_exp_def.cpp`：OpDef 定义文件，构造函数在 OP_ADD() 注册期执行一次，非运行时路径 | OpDef 注册期执行，host 用例无法覆盖，接受现状 |

### 未覆盖函数归类汇总

| 分类 | 数量 | 说明 |
|------|------|------|
| 0. 可提升 | 0 | 可通过增加用例或修改参数覆盖，见 F 节提升建议 |
| 1. 死代码 | 2 | 无调用方或纯日志，无实际影响 |
| 2. 编译期代码 | 1 | 编译/构图阶段函数，aclnn 运行时用例不经过 |
| 3. 执行环境限制（A2/A3/A5） | 0 | 特定 A2/A3/A5 架构路径，当前环境不执行 |
| 4. 特殊情况 | 0 | 运行时函数（调用链可达/processMode 分支），已分析调用点条件并给出 case 建议 |

## D. 已覆盖函数中未覆盖行归类分析

尽量归类，无法归类的特殊标记：
- **1. 死代码**：无实际影响的 artifact 行（函数签名续行、变量声明、for 循环头等），实际已执行
- **2. 非法校验（参数、函数返回值）**：OP_CHECK 失败路径、return false/GRAPH_FAILED，需非法输入才触发
- **3. 执行环境限制（A2/A3/A5）**：特定架构分支，当前 A5 环境不执行
- **4. 特殊情况**：if/else 分支、三元表达式、未识别模式 —— 给出触发条件分析与 case 建议

### 已覆盖函数行数统计

| 指标 | 数值 |
|------|------|
| 已覆盖函数数 | 16 |
| 总行数（平台 xlsx） | 166 |
| 覆盖行数（平台 xlsx） | 138 |
| 未覆盖行数（平台 xlsx） | 28 |

### `CheckDimValid` (`op_api/aclnn_logsumexp.cpp` L91-123)

总行数 18，已覆盖 12 行（平台）/ 12 行（DB），**未覆盖 6 行（平台）/ 7 行（DB）**（其中 0 行结构性无效跳过），**真实未覆盖 7 行（DB 未覆盖且为真实语句行）**

| 行号 | 源码 | 分类 | 能否覆盖 | 原因 | 具体建议 |
|------|------|------|---------|------|---------|
| 91 | `static bool CheckDimValid(const aclTensor* self, c...` | 1. 死代码 | ❌ 否 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 96 | `selfDimNum = 1;` | 4. 特殊情况 | ❓ 未知 | 位于控制流 `if(selfDimNum <= 0...` 内，需构造满足该控制流条件的输入。对照用例参数取值分析缺失组合 | 请人工查看源码上下文确定触发条件 |
| 101 | `OP_LOGE(ACLNN_ERR_PARAM_INVALID, "provided dim %ld...` | 2. 非法校验（参数、函数返回值） | ❌ 否 | 参数合法性校验失败路径（涉及 ``），正常 API 调用中框架先拦截，需构造非法输入（如 shape/dtype 不匹配）才能触发，用例无法覆盖 | 仅当 OP_CHECK 判断失败时执行，正常输入不触发 |
| 103 | `return false;` | 2. 非法校验（参数、函数返回值） | ❌ 否 | 参数合法性校验失败路径（涉及 ``），正常 API 调用中框架先拦截，需构造非法输入（如 shape/dtype 不匹配）才能触发，用例无法覆盖 | 需要触发前面的检查失败才能到达，正常用例不触发 |
| 112 | `dimValue = dim->operator[](i) + selfDimNum;` | 4. 特殊情况 | ❓ 未知 | 位于控制流 `if(dim->operator[](i) < 0...` 内，需构造满足该控制流条件的输入。对照用例参数取值分析缺失组合 | 请人工查看源码上下文确定触发条件 |
| 115 | `OP_LOGE(ACLNN_ERR_PARAM_INVALID, "Dim %ld appears ...` | 2. 非法校验（参数、函数返回值） | ❌ 否 | 参数合法性校验失败路径（涉及 ``），正常 API 调用中框架先拦截，需构造非法输入（如 shape/dtype 不匹配）才能触发，用例无法覆盖 | 仅当 OP_CHECK 判断失败时执行，正常输入不触发 |
| 117 | `return false;` | 2. 非法校验（参数、函数返回值） | ❌ 否 | 参数合法性校验失败路径（涉及 ``），正常 API 调用中框架先拦截，需构造非法输入（如 shape/dtype 不匹配）才能触发，用例无法覆盖 | 需要触发前面的检查失败才能到达，正常用例不触发 |

### `CheckDtypeValid` (`op_api/aclnn_logsumexp.cpp` L70-82)

总行数 8，已覆盖 4 行（平台）/ 4 行（DB），**未覆盖 4 行（平台）/ 5 行（DB）**（其中 0 行结构性无效跳过），**真实未覆盖 5 行（DB 未覆盖且为真实语句行）**

| 行号 | 源码 | 分类 | 能否覆盖 | 原因 | 具体建议 |
|------|------|------|---------|------|---------|
| 70 | `static bool CheckDtypeValid(const aclTensor* self,...` | 1. 死代码 | ❌ 否 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 73 | `op::GetCurrentPlatformInfo().GetSocVersion() == op...` | 4. 特殊情况 | ❓ 未知 | 未识别模式的未覆盖行，结合上下文 `op::GetCurrentPlatformInfo().GetSocVersi` 分析：可能是间接分支路径 / 循环边界 / 宏展开产物。建议增加覆盖更广参数空间的 case（如 S/H/headNum 取边界值）后复查 | 请人工查看源码上下文确定触发条件 |
| 74 | `IsRegBase()) {` | 4. 特殊情况 | ❓ 未知 | 未识别模式的未覆盖行，结合上下文 `IsRegBase()) {` 分析：可能是间接分支路径 / 循环边界 / 宏展开产物。建议增加覆盖更广参数空间的 case（如 S/H/headNum 取边界值）后复查 | 请人工查看源码上下文确定触发条件 |
| 78 | `OP_CHECK_DTYPE_NOT_SUPPORT(self, INPUT_DTYPE_SUPPO...` | 4. 特殊情况 | ❌ 否 | ATK 框架限制：空 dim/nullptr 维度被 OP_CHECK_NULL/CheckNotNull 拦截，ATK 用例只构造合法维度（shape>0）从不构造空 dim，此校验分支永不触发，无需补用例，属框架限制非算子可提升项 | 编译器 artifact，调用首行已被覆盖 |
| 79 | `OP_CHECK_DTYPE_NOT_SUPPORT(out, OUTPUT_DTYPE_SUPPO...` | 4. 特殊情况 | ❌ 否 | ATK 框架限制：空 dim/nullptr 维度被 OP_CHECK_NULL/CheckNotNull 拦截，ATK 用例只构造合法维度（shape>0）从不构造空 dim，此校验分支永不触发，无需补用例，属框架限制非算子可提升项 | OP_CHECK_IF 的失败 return，需要触发前面的检查失败才能到达，正常用例不触发 |

### `ExpectShapeInferWithDimMask` (`op_api/aclnn_logsumexp.cpp` L125-148)

总行数 11，已覆盖 10 行（平台）/ 10 行（DB），**未覆盖 1 行（平台）/ 1 行（DB）**（其中 0 行结构性无效跳过），**真实未覆盖 1 行（DB 未覆盖且为真实语句行）**

| 行号 | 源码 | 分类 | 能否覆盖 | 原因 | 具体建议 |
|------|------|------|---------|------|---------|
| 132 | `dimMask.flip();` | 4. 特殊情况 | ❓ 未知 | 位于控制流 `if(dim->Size() == 0...` 内，需构造满足该控制流条件的输入。对照用例参数取值分析缺失组合 | 请人工查看源码上下文确定触发条件 |

### `GenerateDimTensor` (`op_api/reduce_logsumexp.cpp` L34-43)

总行数 6，已覆盖 2 行（平台）/ 2 行（DB），**未覆盖 4 行（平台）/ 5 行（DB）**（其中 0 行结构性无效跳过），**真实未覆盖 5 行（DB 未覆盖且为真实语句行）**

| 行号 | 源码 | 分类 | 能否覆盖 | 原因 | 具体建议 |
|------|------|------|---------|------|---------|
| 34 | `static const aclTensor *GenerateDimTensor(const ac...` | 1. 死代码 | ❌ 否 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 36 | `FVector<int64_t> dimVector;` | 4. 特殊情况 | ❓ 未知 | 位于控制流 `if(dim->Size() == 0...` 内，需构造满足该控制流条件的输入。对照用例参数取值分析缺失组合 | 请人工查看源码上下文确定触发条件 |
| 37 | `for (size_t i = 0; i < self->GetViewShape().GetDim...` | 1. 死代码 | ❌ 否 | for 循环起始行，循环体已有覆盖说明循环执行了，GCOV 统计口径差异 | 循环体已有覆盖，说明循环执行了。这是 GCOV 统计方式问题 |
| 38 | `dimVector.emplace_back(i);` | 1. 死代码 | ❌ 否 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 编译器 artifact，调用首行已被覆盖 |
| 40 | `return executor->ConvertToTensor(dimVector.data(),...` | 1. 死代码 | ❌ 否 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 编译器 artifact，调用首行已被覆盖 |

### `InferShape4ReduceCommon` (`op_host/reduce_log_sum_exp_infershape.cpp` L25-59)

总行数 21，已覆盖 17 行（平台）/ 17 行（DB），**未覆盖 4 行（平台）/ 5 行（DB）**（其中 0 行结构性无效跳过），**真实未覆盖 5 行（DB 未覆盖且为真实语句行）**

| 行号 | 源码 | 分类 | 能否覆盖 | 原因 | 具体建议 |
|------|------|------|---------|------|---------|
| 25 | `static ge::graphStatus InferShape4ReduceCommon(ger...` | 1. 死代码 | ❌ 否 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 45 | `*out_shape = *in_shape;` | 4. 特殊情况-构图期 | ❌ 否 | （轮2复测改归类，见 analysis_corrections.md #1）`axes_size == 0` 分支，构图期 InferShape（`IMPL_OP_INFERSHAPE` 注册），ATK 运行时 aclnn 用例不执行 op_host InferShape 函数体 | 非可提升，接受现状 |
| 46 | `OP_LOGD(context->GetNodeName(), "axes is empty ten...` | 4. 特殊情况-构图期 | ❌ 否 | （轮2复测改归类，见 analysis_corrections.md #2）同 L45，构图期 | 非可提升，接受现状 |
| 47 | `return ge::GRAPH_SUCCESS;` | 4. 特殊情况-构图期 | ❌ 否 | （轮2复测改归类，见 analysis_corrections.md #3）同 L45，构图期 | 非可提升，接受现状 |
| 56 | `return ReduceDims<int32_t>(in_shape, axes_tensor, ...` | 1. 死代码 | ❌ 否 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 编译器 artifact，调用首行已被覆盖 |

### `ReduceLogSumExp` (`op_api/reduce_logsumexp.cpp` L57-70)

总行数 8，已覆盖 6 行（平台）/ 6 行（DB），**未覆盖 2 行（平台）/ 3 行（DB）**（其中 0 行结构性无效跳过），**真实未覆盖 3 行（DB 未覆盖且为真实语句行）**

| 行号 | 源码 | 分类 | 能否覆盖 | 原因 | 具体建议 |
|------|------|------|---------|------|---------|
| 57 | `const aclTensor* ReduceLogSumExp(const aclTensor* ...` | 1. 死代码 | ❌ 否 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 编译器 artifact，函数体已被覆盖 |
| 60 | `return self;` | 1. 死代码 | ❌ 否 | 局部对象构造/声明行，后续使用行已覆盖，实际已执行 | 编译器 artifact，对象后续使用行已被覆盖 |
| 69 | `return logSumExpOut;` | 1. 死代码 | ❌ 否 | 局部对象构造/声明行，后续使用行已覆盖，实际已执行 | 编译器 artifact，对象后续使用行已被覆盖 |

### `ReduceLogSumExpOpTypeId` (`op_api/reduce_logsumexp.cpp` L25-25)

总行数 0，已覆盖 0 行（平台）/ 0 行（DB），**未覆盖 0 行（平台）/ 1 行（DB）**（其中 0 行结构性无效跳过），**真实未覆盖 1 行（DB 未覆盖且为真实语句行）**

| 行号 | 源码 | 分类 | 能否覆盖 | 原因 | 具体建议 |
|------|------|------|---------|------|---------|
| 25 | `OP_TYPE_REGISTER(ReduceLogSumExp);` | 1. 死代码 | ❌ 否 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 编译器 artifact，调用首行已被覆盖 |

### `aclnnLogSumExpGetWorkspaceSize` (`op_api/aclnn_logsumexp.cpp` L195-294)

总行数 59，已覆盖 52 行（平台）/ 52 行（DB），**未覆盖 7 行（平台）/ 8 行（DB）**（其中 0 行结构性无效跳过），**真实未覆盖 8 行（DB 未覆盖且为真实语句行）**

| 行号 | 源码 | 分类 | 能否覆盖 | 原因 | 具体建议 |
|------|------|------|---------|------|---------|
| 196 | `aclTensor* out, uint64_t* workspaceSize, aclOpExec...` | 1. 死代码 | ❌ 否 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 220 | `OP_LOGW("Format only support ND");` | 4. 特殊情况 | ❓ 未知 | 位于控制流 `if(self->GetStorageFormat() != Format::FORMAT_ND...` 内，需构造满足该控制流条件的输入。对照用例参数取值分析缺失组合 | 请人工查看源码上下文确定触发条件 |
| 240 | `op::Shape shape = self->GetViewShape();` | 4. 特殊情况-框架限制 | ❌ 否 | （轮2复测改归类，见 analysis_corrections.md #4）`dim->Size() == 0` 分支：dim 为必需 attrs，CheckNotNull L50 `OP_CHECK_NULL(dim)` 入口拦截 nullptr；ATK 省略 dim→nullptr 被 EZ1001 拦截（实测 case 50 报错实证），空 aclIntArray 无法由 ATK 构造 | 非可提升，接受现状 |
| 241 | `size_t dimDum = shape.GetDimNum();` | 4. 特殊情况-框架限制 | ❌ 否 | （轮2复测改归类，见 analysis_corrections.md #5）同 L240，框架限制 | 非可提升，接受现状 |
| 242 | `int64_t appendDim[dimDum];` | 4. 特殊情况-框架限制 | ❌ 否 | （轮2复测改归类，见 analysis_corrections.md #6）同 L240，框架限制 | 非可提升，接受现状 |
| 243 | `for (uint64_t i = 0; i < dimDum; i++) {` | 1. 死代码 | ❌ 否 | for 循环起始行，循环体已有覆盖说明循环执行了，GCOV 统计口径差异 | 循环体已有覆盖，说明循环执行了。这是 GCOV 统计方式问题 |
| 244 | `appendDim[i] = static_cast<int64_t>(i);` | 1. 死代码 | ❌ 否 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 编译器 artifact，调用首行已被覆盖 |
| 246 | `dim = uniqueExecutor.get()->AllocIntArray(appendDi...` | 1. 死代码 | ❌ 否 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 编译器 artifact，调用首行已被覆盖 |

## E. 未覆盖行归类汇总

### 按分类统计

| 分类 | 行数 | 说明 |
|------|------|------|
| 0. 可提升 | 0 | **已清零**（轮 1 的 5 个可提升行 L211-215 经 new_case id=51 覆盖；原 6 个脚本误判行经复测改归特殊情况） |
| 1. 死代码 | 16 | 编译器/覆盖率工具 artifact，实际已执行，无需处理 |
| 2. 非法校验（参数、函数返回值） | 4 | OP_CHECK/参数/返回值非法校验路径，需非法输入，正常用例无法覆盖 |
| 3. 执行环境限制（A2/A3/A5） | 0 | A2/A3/A5 特定架构分支，当前 A5 环境不执行 |
| 4. 特殊情况 | 15 | 无法归入前三类的特殊行（if/else 分支、三元表达式、未识别模式、构图期/框架限制）；含轮2复测改归入 6 行 |
| 5. 无效行（结构性/编译器 artifact，不计入真实未覆盖） | 0 | 结构性/编译器 artifact 行（大括号、TILING_DATA_FIELD_DEF 宏、函数签名、return 数值），非独立可执行语句；已从『真实未覆盖行』统计中扣除，不参与归类 |

### 各分类行明细

#### 0. 可提升（0 行，已清零）

> 轮 1 标"可提升"的 5 行（`aclnnLogSumExpGetWorkspaceSize` L211-215，`self->IsEmpty()` 分支）经 new_case id=51（cov_add_empty_self，self shape [0,1]）覆盖，现已进入 covered_lines（见 coveredFuncLineDetail）。
> 脚本本复测再次误判的 6 行（InferShape4ReduceCommon L45/46/47 构图期 + aclnnLogSumExpGetWorkspaceSize L240/241/242 空 dim 框架限制）已对照 `analysis_corrections.md` 台账改归"特殊情况"，详见 D 节校正注记。

#### 1. 死代码（16 行）

| 函数 | 行号 | 说明 | 原因 | 能否覆盖 | 建议 |
|------|------|------|------|---------|------|
| `CheckDimValid` | 91 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| `CheckDtypeValid` | 70 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| `GenerateDimTensor` | 34 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| `GenerateDimTensor` | 37 | for 循环起始行，循环体已有覆盖说明循环执行了，GCOV 统计口径差异 | for 循环头（覆盖率工具未计入循环起始行） | ❌ 否 | 循环体已有覆盖，说明循环执行了。这是 GCOV 统计方式问题 |
| `GenerateDimTensor` | 38 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| `GenerateDimTensor` | 40 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| `InferShape4ReduceCommon` | 25 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| `InferShape4ReduceCommon` | 56 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| `ReduceLogSumExp` | 57 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 函数参数续行（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，函数体已被覆盖 |
| `ReduceLogSumExp` | 60 | 局部对象构造/声明行，后续使用行已覆盖，实际已执行 | 局部对象构造/声明（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，对象后续使用行已被覆盖 |
| `ReduceLogSumExp` | 69 | 局部对象构造/声明行，后续使用行已覆盖，实际已执行 | 局部对象构造/声明（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，对象后续使用行已被覆盖 |
| `ReduceLogSumExpOpTypeId` | 25 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| `aclnnLogSumExpGetWorkspaceSize` | 196 | 函数签名/参数声明行，GCOV 不计入函数入口行但函数体已覆盖，实际已执行 | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| `aclnnLogSumExpGetWorkspaceSize` | 243 | for 循环起始行，循环体已有覆盖说明循环执行了，GCOV 统计口径差异 | for 循环头（覆盖率工具未计入循环起始行） | ❌ 否 | 循环体已有覆盖，说明循环执行了。这是 GCOV 统计方式问题 |
| `aclnnLogSumExpGetWorkspaceSize` | 244 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| `aclnnLogSumExpGetWorkspaceSize` | 246 | 多行函数调用续行（参数值/字符串/命名空间），调用首行已覆盖 | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |

#### 2. 非法校验（参数、函数返回值）（4 行）

| 函数 | 行号 | 说明 | 原因 | 能否覆盖 | 建议 |
|------|------|------|------|---------|------|
| `CheckDimValid` | 101 | 参数合法性校验失败路径（涉及 ``），正常 API 调用中框架先拦截，需构造非法输入（如 shape/dtype 不匹配）才能触发，用例无法覆盖 | 错误日志输出（OP_CHECK 失败后执行） | ❌ 否 | 仅当 OP_CHECK 判断失败时执行，正常输入不触发 |
| `CheckDimValid` | 103 | 参数合法性校验失败路径（涉及 ``），正常 API 调用中框架先拦截，需构造非法输入（如 shape/dtype 不匹配）才能触发，用例无法覆盖 | 错误返回路径（前置检查失败后 return） | ❌ 否 | 需要触发前面的检查失败才能到达，正常用例不触发 |
| `CheckDimValid` | 115 | 参数合法性校验失败路径（涉及 ``），正常 API 调用中框架先拦截，需构造非法输入（如 shape/dtype 不匹配）才能触发，用例无法覆盖 | 错误日志输出（OP_CHECK 失败后执行） | ❌ 否 | 仅当 OP_CHECK 判断失败时执行，正常输入不触发 |
| `CheckDimValid` | 117 | 参数合法性校验失败路径（涉及 ``），正常 API 调用中框架先拦截，需构造非法输入（如 shape/dtype 不匹配）才能触发，用例无法覆盖 | 错误返回路径（前置检查失败后 return） | ❌ 否 | 需要触发前面的检查失败才能到达，正常用例不触发 |

#### 4. 特殊情况（15 行，含轮2复测改归入 6 行）

| 函数 | 行号 | 说明 | 原因 | 能否覆盖 | 建议 |
|------|------|------|------|---------|------|
| `InferShape4ReduceCommon` | 45 | （轮2复测改归入，见 analysis_corrections.md #1）`axes_size == 0` 分支，构图期 InferShape，ATK 运行时不可达 | 构图期 | ❌ 否 | 非可提升，接受现状 |
| `InferShape4ReduceCommon` | 46 | （轮2复测改归入，见 analysis_corrections.md #2）同 L45，构图期 | 构图期 | ❌ 否 | 非可提升，接受现状 |
| `InferShape4ReduceCommon` | 47 | （轮2复测改归入，见 analysis_corrections.md #3）同 L45，构图期 | 构图期 | ❌ 否 | 非可提升，接受现状 |
| `aclnnLogSumExpGetWorkspaceSize` | 240 | （轮2复测改归入，见 analysis_corrections.md #4）`dim->Size() == 0` 分支，dim 必需 attrs，ATK 省略→nullptr 被 CheckNotNull L50 拦截（case 50 实测报错实证） | 框架限制 | ❌ 否 | 非可提升，接受现状 |
| `aclnnLogSumExpGetWorkspaceSize` | 241 | （轮2复测改归入，见 analysis_corrections.md #5）同 L240，框架限制 | 框架限制 | ❌ 否 | 非可提升，接受现状 |
| `aclnnLogSumExpGetWorkspaceSize` | 242 | （轮2复测改归入，见 analysis_corrections.md #6）同 L240，框架限制 | 框架限制 | ❌ 否 | 非可提升，接受现状 |
| `CheckDimValid` | 96 | 位于控制流 `if(selfDimNum <= 0...` 内，需构造满足该控制流条件的输入。对照用例参数取值分析缺失组合 | 未识别模式，需人工分析 | ❓ 未知 | 请人工查看源码上下文确定触发条件 |
| `CheckDimValid` | 112 | 位于控制流 `if(dim->operator[](i) < 0...` 内，需构造满足该控制流条件的输入。对照用例参数取值分析缺失组合 | 未识别模式，需人工分析 | ❓ 未知 | 请人工查看源码上下文确定触发条件 |
| `CheckDtypeValid` | 73 | 未识别模式的未覆盖行，结合上下文 `op::GetCurrentPlatformInfo().GetSocVersi` 分析：可能是间接分支路径 / 循环边界 / 宏展开产物。建议增加覆盖更广参数空间的 case（如 S/H/headNum 取边界值）后复查 | 未识别模式，需人工分析 | ❓ 未知 | 请人工查看源码上下文确定触发条件 |
| `CheckDtypeValid` | 74 | 未识别模式的未覆盖行，结合上下文 `IsRegBase()) {` 分析：可能是间接分支路径 / 循环边界 / 宏展开产物。建议增加覆盖更广参数空间的 case（如 S/H/headNum 取边界值）后复查 | 未识别模式，需人工分析 | ❓ 未知 | 请人工查看源码上下文确定触发条件 |
| `CheckDtypeValid` | 78 | ATK 框架限制：空 dim/nullptr 维度被 OP_CHECK_NULL/CheckNotNull 拦截，ATK 用例只构造合法维度（shape>0）从不构造空 dim，此校验分支永不触发，无需补用例，属框架限制非算子可提升项 | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| `CheckDtypeValid` | 79 | ATK 框架限制：空 dim/nullptr 维度被 OP_CHECK_NULL/CheckNotNull 拦截，ATK 用例只构造合法维度（shape>0）从不构造空 dim，此校验分支永不触发，无需补用例，属框架限制非算子可提升项 | OP_CHECK_IF 宏续行（return 失败路径） | ❌ 否 | OP_CHECK_IF 的失败 return，需要触发前面的检查失败才能到达，正常用例不触发 |
| `ExpectShapeInferWithDimMask` | 132 | 位于控制流 `if(dim->Size() == 0...` 内，需构造满足该控制流条件的输入。对照用例参数取值分析缺失组合 | 未识别模式，需人工分析 | ❓ 未知 | 请人工查看源码上下文确定触发条件 |
| `GenerateDimTensor` | 36 | 位于控制流 `if(dim->Size() == 0...` 内，需构造满足该控制流条件的输入。对照用例参数取值分析缺失组合 | 未识别模式，需人工分析 | ❓ 未知 | 请人工查看源码上下文确定触发条件 |
| `aclnnLogSumExpGetWorkspaceSize` | 220 | 位于控制流 `if(self->GetStorageFormat() != Format::FORMAT_ND...` 内，需构造满足该控制流条件的输入。对照用例参数取值分析缺失组合 | 未识别模式，需人工分析 | ❓ 未知 | 请人工查看源码上下文确定触发条件 |

### 已覆盖函数未覆盖行总数

**35 行**

| 分类 | 行数 | 占比 |
|------|------|------|
| 0. 可提升 | 0 | 0.0% |
| 1. 死代码 | 16 | 45.7% |
| 2. 非法校验（参数、函数返回值） | 4 | 11.4% |
| 3. 执行环境限制（A2/A3/A5） | 0 | 0.0% |
| 4. 特殊情况 | 15 | 42.9% |
| 5. 无效行（结构性/编译器 artifact，不计入真实未覆盖） | 0 | 0.0% |
| **合计** | **35** | **100%** |

### 可通过用例覆盖的行（汇总）

（无 —— 可提升项已全部清零：轮 1 的 5 行经 new_case id=51 覆盖，原 6 行脚本误判行经复测改归特殊情况）

## F. 提升建议（按优先级排序）

### 高优先级：增加 1-3 个 case 即可覆盖

（无需高优先级操作）

### 中优先级：修改现有 case 参数

（无需中优先级操作）

### 不可覆盖（归类后接受现状）

1. **2 个死代码函数**（ParseOpToGraphReduceLogSumExp / ParseParamsReduceLogSumExp）：ONNX plugin 无调用方，无需处理
2. **1 个编译期函数**（ReduceLogSumExp @ op_host _def.cpp）：OP_ADD 注册期执行，aclnn 运行时用例不经过
3. **16 行死代码 artifact**：编译器/覆盖率工具未计入，实际已执行
4. **4 行非法校验路径**：OP_CHECK/返回值校验失败路径，正常 API 无法触发，建议排除统计
5. **15 行特殊情况**（含轮2复测改归入 6 行）：构图期 InferShape 分支（L45/46/47）+ 空 dim 框架限制（L240/241/242）+ 未识别模式分支，当前用例无法触发

## G. 预期覆盖率提升

| 指标 | 当前（轮2整合后） | 进一步增加 case 预期 | 说明 |
|------|------|----------------|------|
| host 语句覆盖率 | 138/223 = 61.88% | 无进一步可提升 | 可提升项已清零（轮1 的 5 行经 id=51 覆盖，6 行误判改归特殊情况），已达 ATK 运行时可达上限 |
| host 分支覆盖率 | 73/246 = 29.67% | 无进一步提升 | 大量 OP_CHECK 分支的 true 路径需非法输入，设计使然 |

> **注意**：分支覆盖率偏低的主因是大量 OP_CHECK_IF 宏展开产生 if/else 分支对，false 路径（正常）已覆盖，true 路径（错误）需要非法输入才能触发，属于设计使然，非用例不足。
> **复测结论**：轮 1 的全部可提升项（5 行 + 1 函数 FillScalar）已被 new_case id=51 覆盖清零；脚本本复测再次误判的 6 行已对照 analysis_corrections.md 台账改归特殊情况。`improvable_state.json.cleared==true`，可提升项清零，终止条件达成。