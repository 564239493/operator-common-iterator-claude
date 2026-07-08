# 约束提取提示词 · 10 个典型算子对齐示例

> 本文件原为 `operator_constraints_extract_v3.md` 附录 A，已移出活跃提示词。
> 提取约束时**无需加载**本文件；仅供维护者参考 schema 在真实算子上的形态。
> **不构成**对其余算子的强制要求。

## 附录 A：与 10 个典型算子的对齐示例

> 下面给出 10 个 Transformer / NN 类算子的提取样例，**用于**说明 schema 在真实场景下的形态，**不构成**对其余算子的强制要求。

| 算子 | 类型 | 关键提取点 |
| ---- | ---- | ---------- |
| `aclnnReflectionPad1dBackward` | NN / 反向 | `padding` 长度固定 2；`padding` 数值 < `self` 最后一维；`gradOutput.shape[:-1] == self.shape[:-1]`、rank 一致及末维派生公式必须分别落库 |
| `aclnnBatchMatMulWeightNz` | NN / MatMul | 必须主动新增隐式 bool 变量 `self_transposed`、`mat2_transposed`（逐平台，`is_operator_param=false`，`allowed_range_value=[true,false]`）；转置相关布局由对应变量门控；涉及 `mat2.shape[j]` / `self.shape[i]` 的 `shape_value_dependency` 必须按隐式 bool 分支；`mat2` 强制 NZ 格式；**§4.6.5 双布局**：非转置 `(b, n1, k1, k0=16, n0=16)` + 转置 `(b, k1, n1, n0=16, k0=16)` 各落两条 `shape[3]/shape[4]==16`；Reduce 维度相等必须约束到 NPU 逻辑 Reduce 轴；self/mat2/out dtype 必须按互推导结果绑定，混合 `FLOAT16/BFLOAT16` 若推导为 `FLOAT` 且 out 不支持 `FLOAT` 则排除；batch `b` 必须提取 broadcast 及 out-b 结果约束；`cubeMathType` 可选 int8 |
| `aclnnGroupedMatmulV5` | NN / 分组 MatMul | `actType ∈ [0,5]`；大量 `Optional` 参数与 `aclTensorList` |
| `aclnnSwinAttentionScoreQuant` | Transformer | int8 量化；`biasDequant*Optional` 取值为 0–255 整型 |
| `aclnnSwinTransformerLnQkvQuant` | Transformer | LN + QKV 拆分；`headNum`/`seqLength`/`epsilon` 等标量属性 |
| `aclnnAlltoAllMatmul` | 通信 + MatMul | `alltoAllAxesOptional` 取值 JSON `null`（原文"空"）或 `[-2,-1]`；**条件 Shape**：`x2.shape` 由 `transposeX2` 门控（`§6.3` 模式 6）——False 时 `(H*rankSize, N)`，True 时 `(N, H*rankSize)`；隐式变量 `BS`/`H`/`N` + 外部常量 `rankSize` |
| `aclnnFFNV3` | NN / MoE FFN | `activation` 为枚举字符串；`innerPrecise` 标量属性 |
| `aclnnNpuFormatCast` | 格式转换 | `srcTensor`、`dstTensor` 必须逐平台生成 `format_rank_consistency`：NCDHW=5D、NDC1HWC0=6D、FRACTAL_Z_3D=8D、NZ/FRACTAL_NZ=5D，ND 使用文档 rank 区间；专项反例 `NCDHW + 8D` 必须被排除；dtype 与 format 强耦合；`dstTensor` 标记 `[DERIVED]` 且 `dtype_support_description` 含 actualFormat 确定映射时必须产出可求解 `derived_value`（A3/A2 为 `actualFormat.range_value == dstFormat.range_value`，不得 `expr=""`）；格式转换语义 + dtype 表每行 src==dst 须产出 `srcTensor.dtype == dstTensor.dtype` 的 `type_equality` 约束（§4.6.12） |
| `aclnnCalculateMatmulWeightSize` | 辅助计算 / 一段式 | 仅计算输出，无 Tensor 真正计算；`workspaceSize`/`executor` 是唯一输出 | `tensorShape` 为 `aclIntArray` 输入（2-6 维，`dtype.value=["int"]` 固定；文档列 `FLOAT16`/`BFLOAT16` 描述关联权重张量 dtype，不写入 `tensorShape.dtype`）；`weightTensorSize` 为 `uint64_t*` 标量输出（公式 result）；一段式 `function_signature` 无 `GetWorkspaceSize`，不写入 `is_single_function_mode` 字段 |
| `aclnnCalculateMatmulWeightSizeV2` | 辅助计算 | 同上 V2，差异在 weight 排布 / NZ 转换 |

> **参考产物位置**：
> - 旧版（`temp/batch-20250625_195726-results/`）—— 历史产物，不一定准确；
> - 新版（`batch-20250626_182854-constraints/`）—— 基于项目实际 `assemble_result.py` 产出的新约束 JSON。
> 两者均仅作参考，**不保证完全正确**。

---
