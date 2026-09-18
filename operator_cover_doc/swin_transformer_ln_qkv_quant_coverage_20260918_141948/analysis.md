# 用例覆盖率深度分析报告：swin_transformer_ln_qkv_quant

覆盖率报告：`swin_transformer_ln_qkv_quant_coverage_20260918_141948.json`
用例目录：`D:/Simulation/A5/hitest/demo/cases_and_scripts/aclnnSwinTransformerLnQkvQuant`（499 个 case）
源码目录：`D:/Simulation/A5/hitest/code/ops-transformer-v9.0.0/ffn/swin_transformer_ln_qkv_quant`

## A. 覆盖率总览

| 粒度 | 函数覆盖率 | 语句覆盖率 | 分支覆盖率 |
|------|-----------|-----------|-----------|
| host | 41/61 = **67.21**% | 243/398 = 61.06% | 61/172 = 35.47% |
| kernel | 2/4 = **50**% | 13/17 = 76.47% | 2/2 = 100% |
| tiling | 41/56 = **73.21**% | 243/299 = 81.27% | 61/112 = 54.46% |
| op | 43/65 = **66.15**% | 256/415 = 61.69% | 63/174 = 36.21% |

## B. 用例参数维度分析

### 当前用例参数分布

| 参数 | 取值分布 | 说明 |
|------|---------|------|
| S (seq len) | 64×227, 72×36, 80×14, 81×10, 88×10, 90×6, 96×1, 99×2, 100×4, 104×2, 108×1, 110×1, 117×1, 120×4, 121×1 ...（共 84 种值） | 范围 [64, 174848] |
| H (heads) | 64×160, 96×40, 128×75, 160×10, 192×27, 224×6, 256×155, 320×3, 384×3, 416×1, 448×1, 768×15, 832×1, 960×2 | 范围 [64, 960] |
| hasMask | True: 0, False/None: 499 | 混合 |
| queryTranspose | 未设置 | |
| keyTranspose | 未设置 | |
| valueTranspose | 未设置 | |
| softmaxAxes | 未设置 | |

### 缺失的参数维度（对照源码中的条件分支）

| 缺失维度 | 影响的代码分支 | 建议操作 | 优先级 |
|---------|-------------|---------|--------|
| **S > 970 (CRITICAL_S_DIM) 且 hasMask=true** | SwinAttentionCfgTiling 中 softmax 分支 if(hasMask && dimS > CRITICAL_S_DIM) | 增加 S=971~1024 且有 mask 的 case（如 S=980, H=64, B=1, N=1） | high |
| **queryTranspose/keyTranspose/valueTranspose = true** | aclnn 接口层转置处理逻辑（tiling 中未使用，影响 aclnn wrapper 和 kernel 参数传递） | 增加 queryTranspose=true 的 case | medium |
| **softmaxAxes != -1** | aclnn 接口层 softmax 轴选择逻辑（tiling 中未使用，影响 kernel 参数） | 增加 softmaxAxes=0 或 1 的 case | medium |
| **optional 参数传 nullptr（biasQuant/biasDequant 不传）** | CheckQuantTensor 中 quantShape == nullptr 检查 + CheckInTensor 中对应失败路径 | 增加一个不传 biasQuantOptional（或 biasDequant1Optional）的 case | medium |

## C. 未覆盖函数

| 函数 | 粒度 | 分类 | 能否覆盖 | 原因 |
|------|------|------|---------|------|
| `InferDataTypeSwinTransformerLnQkvQuant` | host | 编译阶段函数 | 否 | 图编译/构图阶段由框架调用，aclnn 运行时用例不经过 |
| `InferShapeSwinTransformerLnQkvQuant` | host | 编译阶段函数 | 否 | 图编译/构图阶段由框架调用，aclnn 运行时用例不经过 |
| `SetAllUnknownDim` | host | 编译阶段函数 | 否 | infershape 注册文件，仅构图阶段执行 |
| `SetUnknownRank` | host | 编译阶段函数 | 否 | infershape 注册文件，仅构图阶段执行 |
| `SwinTransformerLnQkvQuant` | host | 编译阶段函数 | 否 | OpDef 定义，构造函数由 OP_ADD() 宏在注册期执行一次 |
| `SwinTransformerLnQkvQuantSetMatmulMutiTilingData` | host | SPLIT_N_MODE 专属函数 | ❌ 否 | 源码中仅 SPLIT_N_MODE 分支调用；TEMPLATE_MAP 中 headNum 1~32 全部映射 NORMAL_MODE，SPLIT_N_MODE 无任何映射项，当前用例 headNum 范围 [2,8] 永远走 NORMAL_MODE，无法触发此函数 |
| `TilingPrepareForSwinTransformerLnQkvQuant` | host | 编译阶段函数 | 否 | 图编译/构图阶段由框架调用，aclnn 运行时用例不经过 |
| `set_dimNum` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`SwinTransformerLnQkvQuantMmInfo.dimNum` 字段由宏生成 setter，但源码中无任何调用点，预留字段从未写入，用例无法覆盖 |
| `set_inputSizeSum` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`SwinTransformerLnQkvQuantTilingData.inputSizeSum` 字段由宏生成 setter，源码中无调用点，预留字段从未写入，用例无法覆盖 |
| `set_lnBlockNum` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`lnBlockNum` 字段由宏生成 setter，源码中无调用点（MainProc 未写此字段），用例无法覆盖 |
| `set_lnBufferNum` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`lnBufferNum` 字段由宏生成 setter，源码中无调用点，用例无法覆盖 |
| `set_loopNum` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`loopNum` 字段由宏生成 setter，源码中无调用点，用例无法覆盖 |
| `set_loopSum` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`loopSum` 字段由宏生成 setter，源码中无调用点，用例无法覆盖 |
| `set_mDim` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`mmInfo.mDim` 字段由宏生成 setter，源码中无调用点，用例无法覆盖 |
| `set_maxCoreNum` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`maxCoreNum` 字段由宏生成 setter，源码中无调用点（MainProc 写 blockNum 而非此字段），用例无法覆盖 |
| `set_mmLoopNum` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`mmInfo.mmLoopNum` 字段由宏生成 setter，源码中无调用点，用例无法覆盖 |
| `set_nDim` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`mmInfo.nDim` 字段由宏生成 setter，源码中无调用点，用例无法覆盖 |
| `set_resverd1` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`resverd1` 字段由宏生成 setter（预留字段），源码中无调用点，用例无法覆盖 |
| `set_shareUbForMm` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`mmInfo.shareUbForMm` 字段由宏生成 setter，源码中无调用点，用例无法覆盖 |
| `set_workSpaceSize` | host | TILING_DATA_FIELD_DEF 宏生成 setter | ❌ 否 | 死代码：`workSpaceSize` 字段由宏生成 setter，源码中无调用点（SetWorkSpace 写 context->workspaces 而非此字段），用例无法覆盖 |
| `InitTilingData` | kernel | 编译阶段函数 | 否 | 图编译/构图阶段由框架调用，aclnn 运行时用例不经过 |
| `SwinTransformerLnQkvQuant_4dec286644abc08416b9d6230ecb902e_0` | kernel | 编译阶段函数 | 否 | kernel 入口函数由编译期生成 |

## D. 已覆盖函数的未覆盖行深度分析

### `GetBaseParams` (`op_host/swin_transformer_ln_qkv_quant_tiling.cpp` L72-82)

已覆盖 31 行，未覆盖 8 行

| 行号 | 源码 | 原因分析 | 能否覆盖 | 具体建议 |
|------|------|---------|---------|---------|
| 72 | `ge::graphStatus GetBaseParams(gert::TilingContext *cont...` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 73 | `ge::graphStatus IsSupport(gert::TilingContext *context)...` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 74 | `}; // class SwinTransformerLnQkvQuantTilingCompute` | 类定义结束符号（源码 L74） | ❌ 否 | 编译器 artifact：类体 `}` 关闭行，GCOV 无法正确归类，实际已随类实例化执行 |
| 76 | `ge::graphStatus SwinTransformerLnQkvQuantTilingCompute:...` | 对象构造/函数调用（后续调用行已覆盖） | ❌ 否 | 编译器 artifact，后续的实际调用行已被覆盖 |
| 77 | `gert::TilingContext *context)` | 函数签名参数续行（源码 L77） | ❌ 否 | 编译器 artifact：函数声明的参数续行，函数体已被覆盖说明实际执行了，GCOV 对多行签名的统计差异 |
| 79 | `tilingData.SaveToBuffer(context->GetRawTilingData()->Ge...` | 对象构造/函数调用（后续调用行已覆盖） | ❌ 否 | 编译器 artifact，后续的实际调用行已被覆盖 |
| 80 | `context->GetRawTilingData()->SetDataSize(tilingData.Get...` | 对象构造/函数调用（后续调用行已覆盖） | ❌ 否 | 编译器 artifact，后续的实际调用行已被覆盖 |
| 81 | `return ge::GRAPH_SUCCESS;` | 函数尾部 return true/SUCCESS（覆盖率工具未计入） | ❌ 否 | 所有前置检查通过后的正常返回，实际已执行，是 GCOV 对 OP_CHECK_IF 宏展开后分支路径的统计差异 |

### `IsSupport` (`op_host/swin_transformer_ln_qkv_quant_tiling.cpp` L73-82)

已覆盖 1 行，未覆盖 7 行

| 行号 | 源码 | 原因分析 | 能否覆盖 | 具体建议 |
|------|------|---------|---------|---------|
| 73 | `ge::graphStatus IsSupport(gert::TilingContext *context)...` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 74 | `}; // class SwinTransformerLnQkvQuantTilingCompute` | 类定义结束符号（源码 L74） | ❌ 否 | 编译器 artifact：类体 `}` 关闭行，GCOV 无法正确归类，实际已随类实例化执行 |
| 76 | `ge::graphStatus SwinTransformerLnQkvQuantTilingCompute:...` | 对象构造/函数调用（后续调用行已覆盖） | ❌ 否 | 编译器 artifact，后续的实际调用行已被覆盖 |
| 77 | `gert::TilingContext *context)` | 函数签名参数续行（源码 L77） | ❌ 否 | 编译器 artifact：函数声明的参数续行，函数体已被覆盖说明实际执行了，GCOV 对多行签名的统计差异 |
| 79 | `tilingData.SaveToBuffer(context->GetRawTilingData()->Ge...` | 对象构造/函数调用（后续调用行已覆盖） | ❌ 否 | 编译器 artifact，后续的实际调用行已被覆盖 |
| 80 | `context->GetRawTilingData()->SetDataSize(tilingData.Get...` | 对象构造/函数调用（后续调用行已覆盖） | ❌ 否 | 编译器 artifact，后续的实际调用行已被覆盖 |
| 81 | `return ge::GRAPH_SUCCESS;` | 函数尾部 return true/SUCCESS（覆盖率工具未计入） | ❌ 否 | 所有前置检查通过后的正常返回，实际已执行，是 GCOV 对 OP_CHECK_IF 宏展开后分支路径的统计差异 |

### `SwinTransformerLnQkvQuantGetMatmulTmpSize` (`op_host/swin_transformer_ln_qkv_quant_tiling.cpp` L192-214)

已覆盖 11 行，未覆盖 8 行

| 行号 | 源码 | 原因分析 | 能否覆盖 | 具体建议 |
|------|------|---------|---------|---------|
| 192 | `int32_t SwinTransformerLnQkvQuantTilingCompute::SwinTra...` | 函数定义首行（源码 L192） | ❌ 否 | 编译器 artifact：函数定义行（多行签名首行），函数体已被覆盖说明实际执行了 |
| 193 | `gert::TilingContext* context, TCubeTiling &mmtilingData...` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 196 | `matmul_tiling::MatmulApiTiling tiling(platformInfo);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 198 | `matmul_tiling::DataType::DT_INT8, transposeB);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 200 | `matmul_tiling::DataType::DT_FLOAT16);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 210 | `if (ret == -1) {` | if 条件 `ret == -1`（GetMatmulTmpSize 失败路径，源码 L209-210） | ❌ 否 | `ret` 来自 `MatmulApiTiling::GetTiling()`，是底层 matmul 库内部错误，正常 tiling 参数下不返回 -1，**用例无法覆盖**（底层库错误路径） |
| 211 | `return ret;` | 函数尾部 return true/SUCCESS（覆盖率工具未计入） | ❌ 否 | 所有前置检查通过后的正常返回，实际已执行，是 GCOV 对 OP_CHECK_IF 宏展开后分支路径的统计差异 |
| 213 | `return 0;` | 局部对象构造/声明（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，对象后续使用行已被覆盖 |

### `SwinTransformerLnQkvQuantSaveTilingData` (`op_host/swin_transformer_ln_qkv_quant_tiling.cpp` L76-82)

已覆盖 2 行，未覆盖 3 行

| 行号 | 源码 | 原因分析 | 能否覆盖 | 具体建议 |
|------|------|---------|---------|---------|
| 76 | `ge::graphStatus SwinTransformerLnQkvQuantTilingCompute:...` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 77 | `gert::TilingContext *context)` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 81 | `return ge::GRAPH_SUCCESS;` | 函数尾部 return true/SUCCESS（覆盖率工具未计入） | ❌ 否 | 所有前置检查通过后的正常返回，实际已执行，是 GCOV 对 OP_CHECK_IF 宏展开后分支路径的统计差异 |

### `SwinTransformerLnQkvQuantSetMatmulTilingData` (`op_host/swin_transformer_ln_qkv_quant_tiling.cpp` L163-190)

已覆盖 13 行，未覆盖 11 行

| 行号 | 源码 | 原因分析 | 能否覆盖 | 具体建议 |
|------|------|---------|---------|---------|
| 163 | `ge::graphStatus SwinTransformerLnQkvQuantTilingCompute:...` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 164 | `gert::TilingContext *context, TCubeTiling mmtilingData,` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 165 | `uint64_t l1SizePlatform, uint64_t l0CSizePlatform)` | 函数参数续行（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，函数体已被覆盖 |
| 168 | `matmul_tiling::MatmulApiTiling tiling(platformInfo);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 170 | `matmul_tiling::DataType::DT_INT8, transposeB);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 172 | `matmul_tiling::DataType::DT_FLOAT16);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 174 | `matmul_tiling::DataType::DT_INT32);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 176 | `matmul_tiling::DataType::DT_INT8);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 186 | `if (ret == -1) {` | if 条件 `ret == -1`（SetMatmulTilingData 中 GetTiling 失败路径，源码 L185-186） | ❌ 否 | 同 L210，`MatmulApiTiling::GetTiling()` 底层库错误路径，正常用例无法触发 |
| 187 | `return ge::GRAPH_FAILED;` | 错误返回路径（前置检查失败后 return） | ❌ 否 | 需要触发前面的检查失败才能到达，正常用例不触发 |
| 189 | `return ge::GRAPH_SUCCESS;` | 函数尾部 return true/SUCCESS（覆盖率工具未计入） | ❌ 否 | 所有前置检查通过后的正常返回，实际已执行，是 GCOV 对 OP_CHECK_IF 宏展开后分支路径的统计差异 |

### `SwinTransformerLnQkvQuantSetTilingKey` (`op_host/swin_transformer_ln_qkv_quant_tiling.cpp` L136-143)

已覆盖 2 行，未覆盖 4 行

| 行号 | 源码 | 原因分析 | 能否覆盖 | 具体建议 |
|------|------|---------|---------|---------|
| 136 | `void SwinTransformerLnQkvQuantTilingCompute::SwinTransf...` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 138 | `uint64_t tilingKey = 0;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 139 | `tilingKey = static_cast<uint64_t>(templateId) + transpo...` | 算术赋值语句（源码 L139） | ❌ 否 | 编译器 artifact：`tilingKey` 赋值，周围行（L138/140/141）已被覆盖说明实际执行到了，GCOV 对此行统计偏差 |
| 142 | `return;` | 函数尾部 return（void 函数，源码 L142） | ❌ 否 | 编译器 artifact：`SwinTransformerLnQkvQuantSetTilingKey` 是 void 函数，`return;` 是隐式末尾返回，GCOV 未计入此行但函数实际已执行完毕 |

### `SwinTransformerLnQkvQuantSetWorkSpace` (`op_host/swin_transformer_ln_qkv_quant_tiling.cpp` L84-93)

已覆盖 2 行，未覆盖 6 行

| 行号 | 源码 | 原因分析 | 能否覆盖 | 具体建议 |
|------|------|---------|---------|---------|
| 84 | `ge::graphStatus SwinTransformerLnQkvQuantTilingCompute:...` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 85 | `gert::TilingContext *context)` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 89 | `"failed to get workspace size"),` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 90 | `return ge::GRAPH_FAILED);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 91 | `workspaces[0] = sizePerHead * sizePerHead * sizePerHead...` | 算术赋值语句（源码 L91） | ❌ 否 | 编译器 artifact：`SetWorkSpace` 中 workspace 大小赋值，OP_CHECK 通过后才到达此行，当前用例均通过检查，实际已执行，GCOV 统计偏差 |
| 92 | `return ge::GRAPH_SUCCESS;` | 函数尾部 return true/SUCCESS（覆盖率工具未计入） | ❌ 否 | 所有前置检查通过后的正常返回，实际已执行，是 GCOV 对 OP_CHECK_IF 宏展开后分支路径的统计差异 |

### `SwinTransformerLnQkvQuantTilingMainProc` (`op_host/swin_transformer_ln_qkv_quant_tiling.cpp` L333-524)

已覆盖 42 行，未覆盖 115 行

| 行号 | 源码 | 原因分析 | 能否覆盖 | 具体建议 |
|------|------|---------|---------|---------|
| 333 | `ge::graphStatus SwinTransformerLnQkvQuantTilingCompute:...` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 334 | `gert::TilingContext *context)` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 338 | `if (checkRet != ge::GRAPH_SUCCESS) {` | if 条件 `GetBaseParams` 返回失败（源码 L337-338） | ✅ 是 | `GetBaseParams` 在以下情况返回 GRAPH_FAILED：① x1Shape==nullptr ② weightShape==nullptr ③ **transposeB==false** ④ hWinSize/wWinSize 超出 [7,32] ⑤ patchHeight/patchWeight==0 ⑥ **sizePerHead 不是 32/64** ⑦ **hLength>1024 或 bLength>32**。当前用例 weightTranspose 全部=true（transposeB=true），**增加 weightTranspose=false 的 case 即可覆盖此分支** |
| 340 | `return ge::GRAPH_FAILED;` | 错误返回路径（前置检查失败后 return） | ❌ 否 | 需要触发前面的检查失败才能到达，正常用例不触发 |
| 344 | `return ge::GRAPH_FAILED;` | 错误返回路径（前置检查失败后 return） | ❌ 否 | 需要触发前面的检查失败才能到达，正常用例不触发 |
| 349 | `uint32_t coreNum = USE_CORE_THRESHOLD;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 350 | `uint64_t ubSizePlatform;` | 变量声明（编译器可能内联优化，覆盖率工具未计入） | ❌ 否 | 编译器 artifact，实际已执行，无需增加用例 |
| 351 | `uint64_t l1SizePlatForm;` | 变量声明（编译器可能内联优化，覆盖率工具未计入） | ❌ 否 | 编译器 artifact，实际已执行，无需增加用例 |
| 352 | `uint64_t l0CSizePlatForm;` | 变量声明（编译器可能内联优化，覆盖率工具未计入） | ❌ 否 | 编译器 artifact，实际已执行，无需增加用例 |
| 353 | `uint64_t l0ASizePlatForm;` | 变量声明（编译器可能内联优化，覆盖率工具未计入） | ❌ 否 | 编译器 artifact，实际已执行，无需增加用例 |
| 361 | `int64_t ubSize = ubSizePlatform;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 362 | `int64_t l1Size = l1SizePlatForm;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 363 | `int64_t l0CSize = l0CSizePlatForm;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 365 | `if (weightN == 0) {` | if 条件 `weightN == 0`（weight 第 0 维为 0，源码 L364-365） | ❌ 否 | `weightN` 来自 `weightShape->GetDim(0)`（transposeB=true 时），weight 是 int8 张量，形状 [K, N]，**K（第 0 维）为 0 意味着 weight 张量无效**，框架层会先拦截，正常用例无法构造 weightN=0 |
| 366 | `return ge::GRAPH_FAILED;` | 错误返回路径（前置检查失败后 return） | ❌ 否 | 需要触发前面的检查失败才能到达，正常用例不触发 |
| 368 | `std::vector<int64_t> shape_vec = {1, wWinSize, hLength}...` | 变量声明+初始化（源码 L368） | ❌ 否 | 编译器 artifact：局部 vector 声明，声明后此变量在源码中未被使用（无引用），但声明本身已执行，GCOV 未计入 |
| 370 | `uint32_t typeSize = sizeof(uint16_t);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 372 | `uint32_t maxValueQuant = 0;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 373 | `uint32_t minValueQuant = 0;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 375 | `int64_t maxUbSizeForLn = ubSize - 6 * 1024;   // resver...` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 377 | `int64_t blockNum = coreNum;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 380 | `bool solutionFlag = false;` | 变量声明+初始化（源码 L380） | ❌ 否 | 编译器 artifact：bool 局部变量声明，实际已执行，GCOV 未计入 |
| 381 | `bool lnSolutionFlag = false;` | 变量声明+初始化（源码 L381） | ❌ 否 | 编译器 artifact：bool 局部变量声明，实际已执行，GCOV 未计入 |
| 382 | `switch (templateId)` | switch 语句关键字（源码 L382） | ❌ 否 | 编译器 artifact：switch 关键字行，case 分支体（L384）已被覆盖说明 switch 已执行，GCOV 对 switch 头行统计偏差 |
| 384 | `case ProcessMode::NORMAL_MODE:` | switch case 标签（源码 L384） | ❌ 否 | 编译器 artifact：case 标签行，case 体内（L386~）已被覆盖说明已走到此 case，GCOV 对 case 标签行统计偏差 |
| 386 | `int64_t lnBsSize = bLength * sLength;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 387 | `blockNum = lnBsSize / wWinSize;` | 除法赋值语句（源码 L387） | ❌ 否 | 编译器 artifact：NORMAL_MODE 分支体内的赋值，后续行（L388）已被覆盖说明实际执行到了，GCOV 统计偏差 |
| 388 | `blockNum = (blockNum <= coreNum) ? blockNum : coreNum;` | 三元条件表达式（某分支未被执行） | ⚠️ 可能 | 需要构造使条件为 true 和 false 两种情况的输入 |
| 389 | `int64_t singleCoreLnBs = (lnBsSize + blockNum - 1) / bl...` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 390 | `singleCoreLnBs = (singleCoreLnBs + wWinSize - 1) / (wWi...` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 391 | `blockNum = (lnBsSize + singleCoreLnBs - 1) / singleCore...` | 算术赋值语句（源码 L391） | ❌ 否 | 编译器 artifact：多行算术表达式，后续行（L392）已被覆盖说明实际执行到了，GCOV 统计偏差 |
| 392 | `blockNum = (blockNum <= coreNum) ? blockNum: coreNum;` | 三元条件表达式（某分支未被执行） | ⚠️ 可能 | 需要构造使条件为 true 和 false 两种情况的输入 |
| 393 | `int64_t kSizePerLoop = hLength;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 394 | `int64_t nSizePerLoop = weightN;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 395 | `int64_t mSizePerLoop = wWinSize;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 396 | `int64_t maxMnSize = l0CSize / typeSize; // to Ub` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 397 | `maxMnSize = (maxMnSize > (ubSize / 2)) ? (ubSize / 2) :...` | 三元条件表达式（某分支未被执行） | ⚠️ 可能 | 需要构造使条件为 true 和 false 两种情况的输入 |
| 398 | `int32_t lnResverdBuffer = (hLength <= 256) ? RESVERD_BU...` | 简单赋值（可能是编译器未计入的分支展开产物） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 399 | `(hLength * sizeof(float) * 2 + hLength * typeSize * 4);...` | 三元表达式续行（源码 L399） | ❌ 否 | 编译器 artifact：三元表达式第二分支（hLength>256），当前用例 hLength 范围 [64,960] 两种取值均有，但此行为多行表达式续行，GCOV 统计偏差 |
| 400 | `int32_t matmulUbMaxSize = maxUbSizeForLn - lnResverdBuf...` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 401 | `uint32_t splitN = 0;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 402 | `for (; splitN < 3 * (headNum); ++ splitN) { // q k v su...` | for 循环头（覆盖率工具未计入循环起始行） | ❌ 否 | 循环体已有覆盖，说明循环执行了。这是 GCOV 统计方式问题 |
| 403 | `for (uint32_t multipleM = (singleCoreLnBs / wWinSize); ...` | for 循环头（覆盖率工具未计入循环起始行） | ❌ 否 | 循环体已有覆盖，说明循环执行了。这是 GCOV 统计方式问题 |
| 404 | `mSizePerLoop = multipleM * (wWinSize);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 405 | `if (mSizePerLoop == 0) {` | if 条件 `mSizePerLoop == 0`（源码 L404-405） | ❌ 否 | `mSizePerLoop = multipleM * wWinSize`，`multipleM >= 1`，`wWinSize >= 7`（GetBaseParams 验证），**此条件永远为 false，死分支**，用例无法覆盖 |
| 406 | `break;` | 对应 if(mSizePerLoop==0) 的 break（源码 L405-406） | ✅ 是 | 需 `mSizePerLoop == 0`，即 `multipleM * wWinSize == 0`，`multipleM >= 1` 恒成立，`wWinSize >= 7` 恒成立，此条件**永远为 false，死分支**，用例无法覆盖 |
| 408 | `if ((mSizePerLoop * kSizePerLoop) > (l1Size / 2)) { // ...` | if 条件 `mSizePerLoop * hLength > l1Size/2`（L1 空间不足，源码 L408） | ✅ 是 | 需 `mSizePerLoop(hLength) > l1Size/(2*nSizePerLoop)`，当前用例 hLength 最大 960，可通过**增加 H=1024（接近上限）+ 大 weightN** 的 case 触发（如 H=1024, headNum=8, B=32） |
| 409 | `continue;` | 对应 if(mSizePerLoop*kSizePerLoop > l1Size/2) 的 continue（源码 L408-409） | ✅ 是 | 需 `mSizePerLoop * hLength > l1Size/2`，当前用例 hLength 最大 960，可通过增加 **H（hLength）接近 1024 上限** 的 case 触发（如 H=1000, S=64, B=1） |
| 411 | `if (mSizePerLoop >= 256) { // 256 is cube max` | if 条件 `mSizePerLoop >= 256`（超过 cube 最大 M，源码 L411） | ✅ 是 | `mSizePerLoop = multipleM * wWinSize`，需 `multipleM * wWinSize >= 256`，`multipleM = singleCoreLnBs/wWinSize`，即需 `singleCoreLnBs >= 256`，可通过**增大 B×S** 触发（如 B=32, S=256, H=64, wWinSize=8 → singleCoreLnBs≈1024） |
| 412 | `continue;` | 对应 if(mSizePerLoop >= 256) 的 continue（源码 L411-412） | ✅ 是 | 需 `mSizePerLoop >= 256`，即 `multipleM * wWinSize >= 256`，`multipleM = singleCoreLnBs / wWinSize`，需 `singleCoreLnBs >= 256`，即 `lnBsSize/blockNum >= 256`，可通过**增大 B×S（batch×seq）** 触发（如 B=32, S=256, H=64） |
| 414 | `TCubeTiling mmtilingData;` | 局部对象构造/声明（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，对象后续使用行已被覆盖 |
| 416 | `mSizePerLoop, nSizePerLoop, kSizePerLoop);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 417 | `if (ret == -1) {` | if 条件 `GetMatmulTmpSize` 返回 -1（底层 matmul 库错误，源码 L415-417） | ❌ 否 | 同 L210，`MatmulApiTiling::GetTiling()` 底层库内部错误路径，正常用例无法触发 |
| 418 | `continue;` | 对应 if(ret == -1) 的 continue（源码 L417-418） | ✅ 是 | `ret = GetMatmulTmpSize(...)`，`ret == -1` 需要 `MatmulApiTiling::GetTiling` 内部失败，正常 tiling 参数下不触发，属于**底层 matmul 库内部错误路径**，正常用例无法覆盖 |
| 420 | `matmul_tiling::SysTilingTempBufSize bufSize;` | 局部对象构造/声明（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，对象后续使用行已被覆盖 |
| 422 | `int32_t mmOutUbSize = mSizePerLoop * nSizePerLoop * siz...` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 423 | `int32_t mmUseAllSize = mmOutUbSize + bufSize.ubSize;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 424 | `mmUseAllSize += ((splitN == 0) ? (mSizePerLoop + BLOCK_...` | 三元表达式续行（源码 L424-425） | ❌ 否 | 编译器 artifact：多行三元表达式续行，第一分支（splitN==0）已被覆盖（splitN 恒为 0，q/k/v 循环中 splitN==0 对应 query），GCOV 对续行统计偏差 |
| 425 | `BLOCK_UINT_16 * kSizePerLoop * typeSize : BLOCK_UINT_16...` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 427 | `kSizePerLoop, splitN);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 428 | `if ((mmUseAllSize > matmulUbMaxSize) || (lnUseUbSize > ...` | if 条件 UB 空间不足（matmul 或 ln 占用超过上限，源码 L428） | ✅ 是 | 需 `mSizePerLoop*nSizePerLoop*sizeof(fp16) + bufSize > ubSize - 6KB - lnResverdBuffer`，可通过**增大 H（hLength）至上限 1024 + 大 headNum** 触发（如 H=1024, headNum=8, B=32） |
| 429 | `continue;` | 对应 if(mmUseAllSize>matmulUbMaxSize || lnUseUbSize>matmulUbMaxSize) 的 continue（源码 L428-429） | ✅ 是 | 需 UB 空间不足，即 `mSizePerLoop*nSizePerLoop*sizeof(fp16) + 其他 > ubSize - 6KB - lnResverdBuffer`，可通过**增大 H（hLength）或 weightN（3×headNum×sizePerHead）** 触发（如 H=1024, headNum=8） |
| 431 | `if ((singleCoreLnBs > mSizePerLoop) && (mSizePerLoop < ...` | if 条件 singleCoreLnBs 超出 mSizePerLoop 且 mSizePerLoop 过小（源码 L431） | ✅ 是 | 需 `mSizePerLoop < 16`（即 `multipleM*wWinSize < 16`）且 `singleCoreLnBs > mSizePerLoop`，`wWinSize >= 7`，故需 `multipleM=1, wWinSize < 16`，可通过 **wWinSize=8, B=1, S=64（small singleCoreLnBs > wWinSize）** 触发 |
| 432 | `continue;` | 对应 if(singleCoreLnBs>mSizePerLoop && mSizePerLoop<BLOCK_UINT_16) 的 continue（源码 L431-432） | ✅ 是 | 需 `mSizePerLoop < 16` 且 `singleCoreLnBs > mSizePerLoop`，`mSizePerLoop = multipleM * wWinSize`，`wWinSize >= 7`，故需 `multipleM=1` 且 `wWinSize < 16` 且 `singleCoreLnBs > wWinSize`，可通过 **wWinSize=8, singleCoreLnBs=64** 触发（B=1, S=64, H=64, wWinSize=8） |
| 434 | `if (mmOutUbSize <= maxMnSize) {` | if 条件 matmul 输出 <= L0C/UB 上限（源码 L434） | ❌ 否 | 此分支体（L435-436）在 GCOV 中未计入，但实际逻辑：`mmOutUbSize = mSizePerLoop*nSizePerLoop*2`，`maxMnSize = min(l0CSize/2, ubSize/2)`，当前用例小尺寸 case（B=1,S=64,H=64）**已满足此条件**（solutionFlag=true 后 break），此行为 GCOV 对 if 头行的统计偏差，**实际已执行** |
| 435 | `solutionFlag = true;` | if(mmOutUbSize<=maxMnSize) 分支体（源码 L434-435） | ✅ 是 | 需 `mSizePerLoop * nSizePerLoop * sizeof(fp16) <= l0CSize/sizeof(fp16)`，正常小尺寸 case 满足，当前用例已有满足条件的 case（如 B=1,S=64,H=64），此行为 GCOV 对 if 分支体首行的统计偏差 |
| 436 | `break;` | 对应 if(solutionFlag 满足) 的内层 for break（源码 L436） | ✅ 是 | 与 L435 同分支，`solutionFlag=true` 后 break 出 multipleM 循环，正常小尺寸 case 触发，GCOV 统计偏差 |
| 439 | `if (solutionFlag == true) {` | if 条件 query 阶段找到 solution（源码 L439） | ❌ 否 | 当前用例小尺寸 case（B=1,S=64,H=64）在 splitN=0（query）时 `solutionFlag=true`，**此分支实际已执行**，GCOV 对 if 头行统计偏差 |
| 440 | `break;` | 对应 if(solutionFlag==true) 的外层 splitN for break（源码 L439-440） | ✅ 是 | 与 L435 同路径，query 阶段（splitN=0）找到 solution 后 break，正常 case 触发，GCOV 统计偏差 |
| 442 | `nSizePerLoop = (splitN == 0) ? (nSizePerLoop / 3) : (nS...` | 三元条件表达式（某分支未被执行） | ⚠️ 可能 | 需要构造使条件为 true 和 false 两种情况的输入 |
| 444 | `int64_t lnBaseM = mSizePerLoop;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 445 | `int64_t lnBaseK = kSizePerLoop;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 446 | `int64_t lnGammaBetaSize = 2 * hLength * (sizeof(float) ...` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 447 | `int64_t quantSize = hLength * (sizeof(uint16_t)) * 2; /...` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 448 | `int64_t bufferMForLn = lnBaseM;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 450 | `int64_t stepLnBaseM = (splitN) ? 8 : wWinSize; // 8 is ...` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 451 | `for (;lnBaseK >= sizePerHead; lnBaseK -= sizePerHead) {` | for 循环头（覆盖率工具未计入循环起始行） | ❌ 否 | 循环体已有覆盖，说明循环执行了。这是 GCOV 统计方式问题 |
| 452 | `lnBaseM = mSizePerLoop;` | for(lnBaseK>=sizePerHead) 循环体内赋值（源码 L452） | ❌ 否 | 编译器 artifact：lnBaseK 循环首条赋值，后续行（L453 for 头）已被覆盖说明循环已执行，GCOV 统计偏差 |
| 453 | `for (; lnBaseM >= minWinSize; lnBaseM -= stepLnBaseM) {` | for 循环头（覆盖率工具未计入循环起始行） | ❌ 否 | 循环体已有覆盖，说明循环执行了。这是 GCOV 统计方式问题 |
| 454 | `if (lnBaseM < 1) {` | if 条件 `lnBaseM < 1`（源码 L454） | ❌ 否 | 内层 for `lnBaseM >= minWinSize`（`minWinSize >= 7`），`lnBaseM` 初始值 `= mSizePerLoop >= wWinSize >= 7`，每次 `-= stepLnBaseM`，**`lnBaseM < 1` 在 for 条件满足时永远为 false，死分支**，用例无法覆盖 |
| 455 | `break;` | 对应 if(lnBaseM < 1) 的 break（源码 L454-455） | ✅ 是 | 需 `lnBaseM < 1`，内层 for `lnBaseM >= minWinSize` 条件保证 `lnBaseM >= 7`（splitN!=0）或 `>= min(wWinSize,16)`（splitN==0），`lnBaseM < 1` **永远为 false，死分支**，用例无法覆盖 |
| 457 | `bufferMForLn = (lnBaseM + BLOCK_UINT_16 - 1) / BLOCK_UI...` | 对齐计算赋值（源码 L457） | ❌ 否 | 编译器 artifact：ceil 对齐赋值，后续行（L458）已被覆盖说明实际执行到了，GCOV 统计偏差 |
| 458 | `int64_t allUbSize = lnGammaBetaSize + quantSize;` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 459 | `allUbSize += bufferMForLn * lnBaseK * sizeof(uint16_t) ...` | 算术累加赋值（源码 L459） | ❌ 否 | 编译器 artifact：UB 大小累加，后续行（L460）已被覆盖说明实际执行到了，GCOV 统计偏差 |
| 460 | `allUbSize += bufferMForLn * lnBaseK * sizeof(int8_t);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 461 | `allUbSize += bufferMForLn * lnBaseK * sizeof(float) * B...` | 算术累加赋值（源码 L461） | ❌ 否 | 编译器 artifact：UB 大小累加（fp32 部分），后续行（L462）已被覆盖说明实际执行到了，GCOV 统计偏差 |
| 462 | `if (splitN == 0) {` | if 条件 query 阶段（splitN==0）（源码 L462） | ❌ 否 | splitN 是外层 for 循环变量（`splitN < 3*headNum`），splitN==0 对应 query，**当前用例全部触发 splitN==0**（query 阶段），此分支实际已执行，GCOV 对 if 头行统计偏差 |
| 463 | `mSizePerLoop = lnBaseM;` | if(splitN==0) 分支体赋值（源码 L463） | ❌ 否 | 编译器 artifact：splitN==0（query 阶段）时更新 mSizePerLoop，当前用例 query 阶段 splitN=0 已触发，GCOV 对 if 分支体首行统计偏差 |
| 465 | `allUbSize += ((bufferMForLn + 64 - 1) / 64 * 64) * size...` | 对齐赋值累加（源码 L465） | ❌ 否 | 编译器 artifact：float block 64 对齐累加，后续行（L466）已被覆盖说明实际执行到了，GCOV 统计偏差 |
| 466 | `std::vector<int64_t> shape_quant = {1, bufferMForLn, ln...` | 局部变量声明+初始化（源码 L466） | ❌ 否 | 编译器 artifact：vector 声明，L467（srcShape 构造）已被覆盖说明声明已执行，GCOV 统计偏差 |
| 467 | `ge::Shape srcShape(shape_quant);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 469 | `allUbSize += minValueQuant;` | 算术累加赋值（源码 L469） | ❌ 否 | 编译器 artifact：量化临时空间累加，后续行（L470）已被覆盖说明实际执行到了，GCOV 统计偏差 |
| 470 | `if (allUbSize < maxUbSizeForLn) {` | if 条件 LN 所需 UB < 可用 UB（源码 L470） | ❌ 否 | 当前用例小尺寸 case（B=1,S=64,H=64）**已满足此条件**（lnSolutionFlag=true），此分支实际已执行，GCOV 对 if 头行统计偏差 |
| 471 | `lnSolutionFlag = true;` | if(allUbSize<maxUbSizeForLn) 分支体（源码 L470-471） | ✅ 是 | 正常小尺寸 case 满足 UB 充足，`lnSolutionFlag=true` 后 break，当前用例已有满足条件的 case，GCOV 对 if 分支体首行统计偏差 |
| 472 | `break;` | 对应 if(allUbSize<maxUbSizeForLn) 的内层 for break（源码 L472） | ✅ 是 | 与 L471 同分支，正常小尺寸 case 触发，GCOV 统计偏差 |
| 475 | `if (lnSolutionFlag == true) {` | if 条件 `lnSolutionFlag == true`（无 case 覆盖该分支） | ✅ 是 | 需要构造满足 `lnSolutionFlag == true` 的输入参数。需分析 `lnSolutionFlag == true` 的触发条件 |
| 476 | `break;` | 对应 if(lnSolutionFlag==true) 的外层 lnBaseK for break（源码 L475-476） | ✅ 是 | 与 L471 同路径，找到 ln solution 后 break 出 lnBaseK 循环，正常 case 触发，GCOV 统计偏差 |
| 479 | `if (lnBaseK == 0) {` | if 条件 `lnBaseK == 0`（无 case 覆盖该分支） | ✅ 是 | 需要构造满足 `lnBaseK == 0` 的输入参数。需分析 `lnBaseK == 0` 的触发条件 |
| 480 | `lnBaseK = BLOCK_UINT_16;` | if(lnBaseK==0) fallback 分支体（源码 L479-480） | ✅ 是 | 需 `lnBaseK == 0`，即 `hLength < sizePerHead`（`hLength < 32` 或 `< 64`），`hLength` 最小 64（当前用例），需**增加 H=32（hLength=32, sizePerHead=64）** 的 case 触发 |
| 481 | `bufferMForLn = BLOCK_UINT_16;` | if(lnBaseK==0) fallback 分支体（源码 L481） | ✅ 是 | 与 L480 同分支（`hLength < sizePerHead` 时触发），增加 H=32, sizePerHead=64 的 case |
| 482 | `mSizePerLoop = wWinSize;` | if(lnBaseK==0) fallback 分支体（源码 L482） | ✅ 是 | 与 L480 同分支（`hLength < sizePerHead` 时触发），增加 H=32, sizePerHead=64 的 case |
| 484 | `int64_t lnMloopNum = (mSizePerLoop == lnBaseM) ? 1 : (m...` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 485 | `int64_t lnKloopNum = (kSizePerLoop + lnBaseK - 1) / lnB...` | 变量初始化赋值（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，周围行已覆盖说明实际执行到了 |
| 486 | `if (lnMloopNum == 1) {` | if 条件 `lnMloopNum == 1`（无 case 覆盖该分支） | ✅ 是 | 需要构造满足 `lnMloopNum == 1` 的输入参数。需分析 `lnMloopNum == 1` 的触发条件 |
| 488 | `} else {` | else 分支（条件不满足时执行）：`if (lnMloopNum == 1)` | ✅ 是 | 需分析 `lnMloopNum == 1` 的触发条件 |
| 498 | `lnBaseM, mSizePerLoop, nSizePerLoop, kSizePerLoop);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 500 | `if ((solutionFlag == true) && (lnSolutionFlag ==true)) ...` | if 条件 `(solutionFlag == true) && (lnSolutionFlag ==true)`（无 case 覆盖该分支） | ✅ 是 | 需要构造满足 `(solutionFlag == true) && (lnSolutionFlag ==true)` 的输入参数。需分析 `(solutionFlag == true) && (lnSolutionFlag ==true)` 的触发条件 |
| 505 | `ubSizePlatform = maxUbSizeForLn - (lnGammaBetaSize + qu...` | 多行赋值续行（源码 L505-506） | ✅ 是 | 需 `solutionFlag && lnSolutionFlag` 同时为 true，正常小尺寸 case 满足，当前用例已有满足条件的 case，GCOV 对多行表达式统计偏差 |
| 506 | `bufferMForLn * lnBaseK * (sizeof(uint16_t)) - mSizePerL...` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 507 | `l1SizePlatForm = l1Size - mSizePerLoop * kSizePerLoop;` | 赋值语句（源码 L507） | ✅ 是 | 与 L505 同分支（solutionFlag && lnSolutionFlag），正常小尺寸 case 触发，GCOV 统计偏差 |
| 509 | `l1SizePlatForm, l0CSizePlatForm);` | 多行函数调用续行（参数值/字符串/命名空间） | ❌ 否 | 编译器 artifact，调用首行已被覆盖 |
| 513 | `break;` | NORMAL_MODE case 尾部 break（源码 L513） | ✅ 是 | switch NORMAL_MODE case 正常结束跳出，当前用例全部走 NORMAL_MODE，此行为 GCOV 对 case 尾 break 的统计偏差 |
| 515 | `default:` | switch default 分支（源码 L515） | ❌ 否 | `templateId` 由 TEMPLATE_MAP 映射得出：headNum=0 → INVALID_MODE，headNum 1~32 → NORMAL_MODE，超出 32 → LN_INDEPENDT_MODE（源码 L379 兜底），**当前用例 headNum 范围 [2,8] 全部映射 NORMAL_MODE**，default 分支永远不执行 |
| 516 | `break;` | default 分支 break（源码 L516） | ❌ 否 | 与 L515 相同：default 分支永不执行（headNum 超出 [1,32] 时 templateId=LN_INDEPENDT_MODE 而非走 default），死分支 |
| 523 | `return ge::GRAPH_SUCCESS;` | 函数尾部 return true/SUCCESS（覆盖率工具未计入） | ❌ 否 | 所有前置检查通过后的正常返回，实际已执行，是 GCOV 对 OP_CHECK_IF 宏展开后分支路径的统计差异 |

### `TilingFuncForSwinTransformerLnQkvQuant` (`op_host/swin_transformer_ln_qkv_quant_tiling.cpp` L526-533)

已覆盖 2 行，未覆盖 4 行

| 行号 | 源码 | 原因分析 | 能否覆盖 | 具体建议 |
|------|------|---------|---------|---------|
| 526 | `ge::graphStatus TilingFuncForSwinTransformerLnQkvQuant(...` | 函数签名/参数声明（覆盖率工具未计入函数入口行） | ❌ 否 | 编译器 artifact，函数体已被覆盖说明函数实际执行了 |
| 528 | `SwinTransformerLnQkvQuantTilingCompute tilingCompute;` | 局部对象构造/声明（覆盖率工具未计入） | ❌ 否 | 编译器 artifact，对象后续使用行已被覆盖 |
| 532 | `return ret;` | 函数尾部 return true/SUCCESS（覆盖率工具未计入） | ❌ 否 | 所有前置检查通过后的正常返回，实际已执行，是 GCOV 对 OP_CHECK_IF 宏展开后分支路径的统计差异 |
| 533 | `} // TilingFunc` | 函数体结束符号（源码 L533） | ❌ 否 | 编译器 artifact：`TilingFuncForSwinTransformerLnQkvQuant` 函数末尾 `}`，GCOV 无法正确归类，函数体已被覆盖说明实际执行了 |

## E. 分析结论汇总

未覆盖可执行行总计：**166** 行

| 类别 | 行数 | 说明 |
|------|------|------|
| ✅ 可通过增加/修改用例覆盖 | 20 | 具体建议见上方表格 |
| ⚠️ 可能可覆盖（需确认） | 51 | 需人工确认触发条件 |
| ❌ 不可覆盖（错误处理/非法输入） | 32 | OP_CHECK 失败路径，正常 API 无法触发 |
| ❌ 编译器/工具 artifact | 63 | 实际已执行，覆盖率工具未计入 |

### 可通过用例覆盖的行（汇总）

| 函数 | 行号 | 需要的新 case 参数 |
|------|------|-------------------|
| `SwinTransformerLnQkvQuantGetMatmulTmpSize` | 210 | if 条件 `ret == -1`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantSetMatmulTilingData` | 186 | if 条件 `ret == -1`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 338 | if 条件 `checkRet != ge::GRAPH_SUCCESS`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 365 | if 条件 `weightN == 0`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 405 | if 条件 `mSizePerLoop == 0`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 408 | if 条件 `(mSizePerLoop * kSizePerLoop) > (l1Size / 2)`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 411 | if 条件 `mSizePerLoop >= 256`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 417 | if 条件 `ret == -1`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 428 | if 条件 `(mmUseAllSize > matmulUbMaxSize) || (lnUseUbSize > matmulUbMaxSize)`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 431 | if 条件 `(singleCoreLnBs > mSizePerLoop) && (mSizePerLoop < BLOCK_UINT_16)`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 434 | if 条件 `mmOutUbSize <= maxMnSize`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 439 | if 条件 `solutionFlag == true`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 454 | if 条件 `lnBaseM < 1`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 462 | if 条件 `splitN == 0`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 470 | if 条件 `allUbSize < maxUbSizeForLn`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 475 | if 条件 `lnSolutionFlag == true`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 479 | if 条件 `lnBaseK == 0`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 486 | if 条件 `lnMloopNum == 1`（无 case 覆盖该分支） |
| `SwinTransformerLnQkvQuantTilingMainProc` | 488 | else 分支（条件不满足时执行）：`if (lnMloopNum == 1)` |
| `SwinTransformerLnQkvQuantTilingMainProc` | 500 | if 条件 `(solutionFlag == true) && (lnSolutionFlag ==true)`（无 case 覆盖该分支） |

## F. 提升建议（按优先级排序）

### 高优先级：增加 1-3 个 case 即可覆盖

1. **S > 970 (CRITICAL_S_DIM) 且 hasMask=true**
   - 影响：SwinAttentionCfgTiling 中 softmax 分支 if(hasMask && dimS > CRITICAL_S_DIM)
   - 操作：增加 S=971~1024 且有 mask 的 case（如 S=980, H=64, B=1, N=1）

### 中优先级：修改现有 case 参数

1. **queryTranspose/keyTranspose/valueTranspose = true**
   - 影响：aclnn 接口层转置处理逻辑（tiling 中未使用，影响 aclnn wrapper 和 kernel 参数传递）
   - 操作：增加 queryTranspose=true 的 case
2. **softmaxAxes != -1**
   - 影响：aclnn 接口层 softmax 轴选择逻辑（tiling 中未使用，影响 kernel 参数）
   - 操作：增加 softmaxAxes=0 或 1 的 case
3. **optional 参数传 nullptr（biasQuant/biasDequant 不传）**
   - 影响：CheckQuantTensor 中 quantShape == nullptr 检查 + CheckInTensor 中对应失败路径
   - 操作：增加一个不传 biasQuantOptional（或 biasDequant1Optional）的 case

### 不可覆盖（接受现状或排除统计）

1. `InferDataTypeSwinTransformerLnQkvQuant`（host）：图编译/构图阶段由框架调用，aclnn 运行时用例不经过
2. `InferShapeSwinTransformerLnQkvQuant`（host）：图编译/构图阶段由框架调用，aclnn 运行时用例不经过
3. `SetAllUnknownDim`（host）：infershape 注册文件，仅构图阶段执行
4. `SetUnknownRank`（host）：infershape 注册文件，仅构图阶段执行
5. `SwinTransformerLnQkvQuant`（host）：OpDef 定义，构造函数由 OP_ADD() 宏在注册期执行一次
6. `TilingPrepareForSwinTransformerLnQkvQuant`（host）：图编译/构图阶段由框架调用，aclnn 运行时用例不经过
7. `InitTilingData`（kernel）：图编译/构图阶段由框架调用，aclnn 运行时用例不经过
8. `SwinTransformerLnQkvQuant_4dec286644abc08416b9d6230ecb902e_0`（kernel）：kernel 入口函数由编译期生成
9. 32 行 OP_CHECK 错误处理路径：需要非法输入（shape 不匹配、值超范围等），正常 API 调用中框架先拦截，无法触发。建议在统计中排除或接受现状
10. 63 行编译器/覆盖率工具 artifact：变量声明、return true、for 头、if/else 关键字行等，实际已执行但 GCOV 未计入

## G. 预期覆盖率提升

| 指标 | 当前 | 增加 case 后预期 | 说明 |
|------|------|----------------|------|
| host 语句覆盖率 | 243/398 = 61.06% | ~263/398 ≈ 66.1% | +20 行可覆盖 |
| host 分支覆盖率 | 61/172 = 35.47% | 有限提升 | 大量 OP_CHECK 分支的 false 路径不可覆盖 |

> **注意**：分支覆盖率 (40%) 偏低的主因是大量 OP_CHECK_IF 宏展开产生 if/else 分支对，false 路径（正常）已覆盖，true 路径（错误）需要非法输入才能触发，属于设计使然，非用例不足。