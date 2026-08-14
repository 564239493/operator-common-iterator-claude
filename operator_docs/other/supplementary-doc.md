# supplementary-doc.md — aclnnNpuFormatCast 源码补充约束（extract 域）

> 来源：`source_raw.json`（155 raw_checks）+ 算子主源码
> `ops-math/conversion/npu_format_cast/op_host/op_api/aclnn_npu_format_cast.cpp`。
> 本轮（iter_002, extract 域）重新跑 `extract_source_constraints.py`，快照未变，
> raw_checks 仍 155 条（OP_CHECK 17 / OP_LOGE 41 / OP_TILING_CHECK 24 /
> VECTOR_INNER_ERR_REPORT 63 / OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE 10），
> S1–S16 全部由 aclnn_npu_format_cast.cpp 的 19 条 host 检查复现，S17 由
> canndev/ops/built-in/op_tiling/runtime/trans_data.cc 的 3 条 OP_TILING_CHECK
>（L275/L329/L475）+ GetC0SizeWithType(L58) **直接从源码推导**（本轮 extract
> 域），并经第 1 轮 diagnose 域失败日志确认（cases 1/4/5/9），双重确认。
> 供 constraint-supplementer 读取并合并进 `constraints_in_parameters`（add 候选）。
> 平台 key 与 constraints.json 一致：`Atlas 350 加速卡`、
> `Atlas A3 训练系列产品/Atlas A3 推理系列产品`、
> `Atlas A2 训练系列产品/Atlas A2 推理系列产品`。
> 平台映射（源码 SocVersion/IsRegBase ↔ 文档平台）：
> `IsRegBase()==true` → `Atlas 350 加速卡`（ASCEND950）；`SocVersion::ASCEND910B`/`ASCEND910_93`
> → `Atlas A2/A3`（源码对二者取值完全相同，文档亦合并为一组）。
>
> **int 枚举码 ↔ 名称映射（关键，避免 IntSort vs DType sort mismatch）**：
> - additionalDtype(int): `1`=FLOAT16、`27`=BFLOAT16、`2`=INT8、`36`=FLOAT8_E4M3FN、`-1`=不参与(A2/A3)。
> - dstFormat/actualFormat(int): `2`=ND、`29`=FRACTAL_NZ(NZ)、`30`=NCDHW、`32`=NDC1HWC0、`33`=FRACTAL_Z_3D、`50`=FRACTAL_NZ_C0_16、`51`=FRACTAL_NZ_C0_32。
> - srcTensor.dtype(string 名): INT8/UINT8/INT32/UINT32/FLOAT32/FLOAT16/BFLOAT16/FLOAT8_E4M3FN/FLOAT4_E2M1。
> 凡涉及 `additionalDtype != srcTensor.dtype` 的条件，一律展开为同 sort 字面量析取
> `(additionalDtype == <code> and srcTensor.dtype != "<name>") or ...`，禁止把 int 枚举码与
> dtype 名字符串直接 `==` 比较（会恒 False）。

---

## S1 — dstFormat 入参枚举码白名单（双平台通用）

- expr_type: `value_dependency`
- expr: `dstFormat in [2, 29, 30, 32, 33]`
- relation_params: `["dstFormat"]`
- target_platform: `all`
- source_location: `ops-math/conversion/npu_format_cast/op_host/op_api/aclnn_npu_format_cast.cpp:547-568`
- error_string: `aclnnNpuFormatCastCalculateSizeAndFormat unsupported format transformation`（OP_LOGW + `return ACLNN_ERR_RUNTIME_ERROR`）
- 依据：`aclnnNpuFormatCastCalculateSizeAndFormat` 的 if/else-if 链只接受
  `FORMAT_FRACTAL_NZ(29)/FORMAT_ND(2)/FORMAT_NCDHW(30)/FORMAT_NDC1HWC0(32)/FORMAT_FRACTAL_Z_3D(33)`，
  其余落到 `:567 OP_LOGW("...unsupported format transformation") + return ACLNN_ERR_RUNTIME_ERROR`。
  dstFormat 入参直接是 int 枚举码，本函数不做枚举码到 C0_16/C0_32 的判定（50/51 由 actualFormat 输出，非 dstFormat 入参）。
- 文档缺/弱：文档参数表 dstFormat「数据格式」列列了 5 个格式名，但未以 int 枚举码集合形式给出
  `dstFormat in {2,29,30,32,33}` 的硬约束；Atlas 350 支持表仅列 29，易被误读为唯一合法值。

---

## S3 — srcTensor view shape 维度 [2,6]（Atlas 350）

- expr_type: `shape_value_dependency`
- expr: `len(srcTensor.shape) >= 2 and len(srcTensor.shape) <= 6`
- relation_params: `["srcTensor"]`
- target_platform: `Atlas 350 加速卡`
- source_location: `aclnn_npu_format_cast.cpp:122-129` 与 `:147-154`
- error_string: `Only support srcTensor's viewShapeDim is between 2 and 6 when ...`（`OP_LOGE(ACLNN_ERR_PARAM_INVALID)`）
- 依据：`ValidateNonQuantMatmulParams`(:122) 与 `ValidateQuantMatmulParams`(:147) 均
  `OP_CHECK(viewShapeDim >= 2 && viewShapeDim <= 6, ...)`；`viewShapeDim = srcTensor->GetViewShape().GetDimNum()`。
  仅 `Check95NdToNz*`（IsRegBase 即 Atlas 350）路径生效。
- 文档缺/弱：文档 srcTensor 维度列写「2-6」，但未在 constraints_in_parameters 以
  `len(srcTensor.shape)` 不等式表达（仅放 dimensions 属性）；补充为显式约束。

---

## S4 — WeightQuant 分支 srcTensor view shape 仅 2 或 3（Atlas 350）

- expr_type: `shape_value_dependency`
- expr: `not ((additionalDtype == 1 and srcTensor.dtype != "FLOAT16") or (additionalDtype == 27 and srcTensor.dtype != "BFLOAT16") or (additionalDtype == 2 and srcTensor.dtype != "INT8") or (additionalDtype == 36 and srcTensor.dtype != "FLOAT8_E4M3FN")) or len(srcTensor.shape) == 2 or len(srcTensor.shape) == 3`
- relation_params: `["srcTensor", "additionalDtype"]`
- target_platform: `Atlas 350 加速卡`
- source_location: `aclnn_npu_format_cast.cpp:183-190`
- error_string: `Only support srcTensor's viewShapeDim is 2 or 3 when additionalDtype is not equal to srcTensors's dtype, current viewShapeDim: [%zu]`（`OP_LOGE(ACLNN_ERR_PARAM_INVALID)`）
- 依据：`ValidateWeightQuantMatmulParams` 中 `OP_CHECK(viewShapeDim == 2 || viewShapeDim == 3, ...)`，
  仅在 `additionalDtype != srcDtype`（WeightQuant）分支调用（`:238-242`）。
  条件 `additionalDtype != srcTensor.dtype` 按 int↔name 映射展开为字面量析取，避免 IntSort vs DType sort mismatch。
- 文档缺/弱：文档 srcTensor 维度列笼统写「2-6」，未细化 WeightQuant 子场景为「2 或 3」。

---

## S5 — WeightQuant 分支 dstTensor storage shape 仅 4 或 5（Atlas 350）

- expr_type: `shape_value_dependency`
- expr: `not ((additionalDtype == 1 and srcTensor.dtype != "FLOAT16") or (additionalDtype == 27 and srcTensor.dtype != "BFLOAT16") or (additionalDtype == 2 and srcTensor.dtype != "INT8") or (additionalDtype == 36 and srcTensor.dtype != "FLOAT8_E4M3FN")) or len(dstTensor.shape) == 4 or len(dstTensor.shape) == 5`
- relation_params: `["dstTensor", "additionalDtype"]`
- target_platform: `Atlas 350 加速卡`
- source_location: `aclnn_npu_format_cast.cpp:357-366`
- error_string: `Only support srcViewShapeDim is 2/3 and storageShapeDim is 4/5 when srcDtype is not equal to dstDtype, which are [%zu] and [%zu].`（`OP_LOGE(ACLNN_ERR_PARAM_INVALID)`）
- 依据：`Check95NdToNzGetWorkSpaceSizeInputs` else 分支
  `(srcviewShapeDim == DIMS_TWO || srcviewShapeDim == DIMS_THREE) && (storageShapeDim == DIMS_FOUR || storageShapeDim == DIMS_FIVE)`，
  `storageShapeDim = dstTensor->GetStorageShape().GetDimNum()`；该 else 分支即 WeightQuant（`srcDtype != dstDtype`）。
  条件同 S4 跨 sort 展开。
- 文档缺/弱：文档 dstTensor 维度列笼统写「4-8」，未细化 WeightQuant 子场景为「4 或 5」。

---

## S9 — 非量化 K=1 拦截（Atlas 350，FLOAT16/BFLOAT16 additionalDtype==srcDtype）

- expr_type: `shape_value_dependency`
- expr: `not ((additionalDtype == 1 and srcTensor.dtype == "FLOAT16") or (additionalDtype == 27 and srcTensor.dtype == "BFLOAT16")) or srcTensor.shape[-2] != 1`
- relation_params: `["srcTensor", "additionalDtype"]`
- target_platform: `Atlas 350 加速卡`
- source_location: `aclnn_npu_format_cast.cpp:113-120`
- error_string: `Only support srcTensor's k Dim is not 1 when additionalDtype equals srcTensors's dtype.`（`OP_LOGE(ACLNN_ERR_PARAM_INVALID)`）
- 依据：`ValidateNonQuantMatmulParams` 中 `int64_t kDim = viewShape.GetDim(viewShapeDim - 2);
  OP_CHECK(kDim != 1, ...)`，倒数第 2 维为 K 维。仅 `IsNonQuantMatmulDtype(additionalDtype) && dstFormat==FORMAT_FRACTAL_NZ`
  分支调用（`:244-248`）。`IsNonQuantMatmulDtype`(:71-77) 对 `dtype==FLOAT16 || dtype==BFLOAT16` 返回 true
  （FLOAT && dstFormat∉{C0_16,C0_32} 分支因 Atlas 350 additionalDtype 枚举不含 FLOAT(0) 而不可达，故只取 FLOAT16/BF16）。
- 文档缺/弱：文档 Atlas 350「不支持的特殊场景」仅文字描述「FLOAT16/BF16 且 additionalDtype==srcDtype 时 [k,n] 的 k=1 不支持」，
  未以 `srcTensor.shape[-2] != 1` 约束形式表达。

---

## S10 — srcTensor.dtype==INT32 时 additionalDtype 枚举（Atlas 350）

- expr_type: `value_dependency`
- expr: `srcTensor.dtype != "INT32" or additionalDtype in [1, 27, 2]`
- relation_params: `["srcTensor", "additionalDtype"]`
- target_platform: `Atlas 350 加速卡`
- source_location: `aclnn_npu_format_cast.cpp:161-169`
- error_string: `Only support additionalDtype is float16/bfloat16/int8 when srcTensors's dtype is int32, current additionalDtype: [%s].`（`OP_LOGE(ACLNN_ERR_PARAM_INVALID)`）
- 依据：`if (srcDtype == ge::DT_INT32) { OP_CHECK(additionalDtype == ge::DT_FLOAT16 || additionalDtype == ge::DT_BF16 || additionalDtype == ge::DT_INT8, ...) }`。
  `DT_FLOAT16=1, DT_BF16=27, DT_INT8=2`。
- 文档缺/弱：文档 Atlas 350 支持表 INT32 行 additionalDtype 仅列 `ACL_FLOAT16(1)、ACL_BF16(27)`，未列 `INT8(2)`
  （源码允许 INT8(2)，见 conflict-doc.md CF2）；补充把源码允许集 `[1,27,2]` 写入约束。

---

## S11 — srcTensor.dtype==FLOAT32 时 additionalDtype 枚举（Atlas 350）

- expr_type: `value_dependency`
- expr: `srcTensor.dtype != "FLOAT32" or additionalDtype in [1, 27, 36]`
- relation_params: `["srcTensor", "additionalDtype"]`
- target_platform: `Atlas 350 加速卡`
- source_location: `aclnn_npu_format_cast.cpp:170-180`
- error_string: `Only support additionalDtype is float16 or bfloat16 or float8_e4m3fn when srcTensors's dtype is float32, current additionalDtype: [%s].`（`OP_LOGE(ACLNN_ERR_PARAM_INVALID)`）
- 依据：`else if (srcDtype == ge::DT_FLOAT) { OP_CHECK(additionalDtype == ge::DT_FLOAT16 || additionalDtype == ge::DT_BF16 || additionalDtype == ge::DT_FLOAT8_E4M3FN, ...) }`。
  `DT_FLOAT8_E4M3FN=36`。
- 文档缺/弱：文档 Atlas 350 支持表 FLOAT 行 additionalDtype 列 `ACL_FLOAT16(1)、ACL_BF16(27)、ACL_FLOAT8_E4M3FN(36)`，
  与源码一致但未以 `additionalDtype in [1,27,36]` 约束形式表达。

---

## S12 — WeightQuant 分支 dstFormat 必须 29（Atlas 350）

- expr_type: `format_equality`
- expr: `not ((additionalDtype == 1 and srcTensor.dtype != "FLOAT16") or (additionalDtype == 27 and srcTensor.dtype != "BFLOAT16") or (additionalDtype == 2 and srcTensor.dtype != "INT8") or (additionalDtype == 36 and srcTensor.dtype != "FLOAT8_E4M3FN")) or dstFormat == 29`
- relation_params: `["dstFormat", "additionalDtype", "srcTensor"]`
- target_platform: `Atlas 350 加速卡`
- source_location: `aclnn_npu_format_cast.cpp:192-199`
- error_string: `Only support dstFormat is 29, when additionalDtype is not equal to srcTensors's dtype, current dstFormat: [%d].`（`OP_LOGE(ACLNN_ERR_PARAM_INVALID)`）
- 依据：`ValidateWeightQuantMatmulParams` 中 `OP_CHECK(dstFormat == op::Format::FORMAT_FRACTAL_NZ, ...)`，
  仅 WeightQuant(`additionalDtype != srcDtype`)分支生效。`FORMAT_FRACTAL_NZ=29`。条件同 S4 跨 sort 展开。
- 文档缺/弱：文档 Atlas 350 支持表 dstFormat 列恒为 29，但未显式表达「WeightQuant 时 dstFormat 必须 29」的强约束。

---

## S13 — 量化分支 srcFormat∈{ND,NCL,NCHW,NCDHW} 且 dstFormat==29（Atlas 350）

- expr_type: `format_equality`
- expr: `not (srcTensor.dtype == dstTensor.dtype and (srcTensor.dtype == "INT8" or srcTensor.dtype == "UINT8" or srcTensor.dtype == "FLOAT8_E4M3FN")) or (srcTensor.format in ["ND", "NCL", "NCHW", "NCDHW"] and dstFormat == 29)`
- relation_params: `["srcTensor", "dstFormat", "dstTensor"]`
- target_platform: `Atlas 350 加速卡`
- source_location: `aclnn_npu_format_cast.cpp:262-273`（`CheckFormatValid`/`IsQuantMatmulDtype` 分支）
- error_string: `Only support srcFormat is ND/NCL/NCHW/NCDHW and dstFormat is FRACTAL_NZ when srtDtype equals int8/uint8/float8_e4m3fn, which are [%s] and [%s].`（`OP_LOGE(ACLNN_ERR_PARAM_INVALID)`）
- 依据：`if (IsQuantMatmulDtype(srcDtype, dstDtype)) { OP_CHECK(CheckInputFormatSupportedToNz(srcFormat) && dstFormat == FORMAT_FRACTAL_NZ, ...) }`。
  `IsQuantMatmulDtype`(:79-83) = `srcDtype==dstDtype && (INT8||UINT8||FLOAT8_E4M3FN)`；
  `INPUT_FORMAT_TO_NZ_SUPPORT_LIST = {ND, NCL, NCHW, NCDHW}`(:68-69)。
- 文档缺/弱：文档 Atlas 350 支持表隐含 srcFormat=ND/NCL，但未列 NCHW/NCDHW 也被 `CheckInputFormatSupportedToNz` 接受。

---

## S14 — 非量化分支 srcFormat∈{ND,NCL} 且 dstFormat==29（Atlas 350）

- expr_type: `format_equality`
- expr: `not ((srcTensor.dtype == "FLOAT32" and dstFormat not in [50, 51]) or srcTensor.dtype == "FLOAT16" or srcTensor.dtype == "BFLOAT16") or (srcTensor.format in ["ND", "NCL"] and dstFormat == 29)`
- relation_params: `["srcTensor", "dstFormat"]`
- target_platform: `Atlas 350 加速卡`
- source_location: `aclnn_npu_format_cast.cpp:274-285`（`CheckFormatValid`/`IsNonQuantMatmulDtype` 分支）
- error_string: `Only support srcFormat is ND/NCL and dstFormat is FRACTAL_NZ when srtDtype equals float16 or bfloat16, which are [%s] and [%s].`（`OP_LOGE(ACLNN_ERR_PARAM_INVALID)`）
- 依据：`else if (IsNonQuantMatmulDtype(srcDtype, dstFormat)) { OP_CHECK((srcFormat==FORMAT_ND || srcFormat==FORMAT_NCL) && dstFormat==FORMAT_FRACTAL_NZ, ...) }`。
  `IsNonQuantMatmulDtype`(:71-77) = `(dtype==FLOAT && dstFormat∉{C0_16,C0_32}) || FLOAT16 || BF16`；`C0_16=50, C0_32=51`。
- 文档缺/弱：文档 Atlas 350 功能说明隐含 srcFormat=ND/NCL，但未以约束形式表达非量化分支的 srcFormat 限制。

---

## S15 — WeightQuant 分支 srcFormat==ND 且 dstFormat∈{29,50,51}（Atlas 350）

- expr_type: `format_equality`
- expr: `not ((srcTensor.dtype == "INT32" or srcTensor.dtype == "FLOAT32" or srcTensor.dtype == "FLOAT8_E4M3FN") and ((additionalDtype == 1 and srcTensor.dtype != "FLOAT16") or (additionalDtype == 27 and srcTensor.dtype != "BFLOAT16") or (additionalDtype == 2 and srcTensor.dtype != "INT8") or (additionalDtype == 36 and srcTensor.dtype != "FLOAT8_E4M3FN"))) or (srcTensor.format == "ND" and dstFormat in [29, 50, 51])`
- relation_params: `["srcTensor", "dstFormat", "additionalDtype"]`
- target_platform: `Atlas 350 加速卡`
- source_location: `aclnn_npu_format_cast.cpp:286-299`（`CheckFormatValid` else 分支）
- error_string: `Only support srcFormat is ND and dstFormat is FRACTAL_NZ_C0_16 or FRACTAL_NZ_C0_32 when srcDtype equals int32 or float32, which are [%s] and [%s].`（`OP_LOGE(ACLNN_ERR_PARAM_INVALID)`）
- 依据：else 分支 `OP_CHECK((srcDtype==DT_INT32 || srcDtype==DT_FLOAT || srcDtype==DT_FLOAT8_E4M3FN) && srcFormat==FORMAT_ND && (dstFormat==FRACTAL_NZ_C0_16 || FRACTAL_NZ_C0_32 || FRACTAL_NZ), ...)`。
  `FRACTAL_NZ_C0_16=50, FRACTAL_NZ_C0_32=51`。该 else 即 WeightQuant 路径，条件 `additionalDtype != srcDtype` 同 S4 跨 sort 展开。
- 文档缺/弱：文档 Atlas 350 支持表隐含 srcFormat=ND，但未以 `srcTensor.format=="ND" and dstFormat in [29,50,51]` 约束形式表达。

---

## S16 — additionalDtype 平台默认值规则（双平台）

- expr_type: `value_dependency`
- expr（Atlas 350）: `additionalDtype in [1, 27, 2, 36]`
- expr（A2/A3）: `additionalDtype == -1`
- relation_params: `["additionalDtype"]`
- target_platform: `Atlas 350 加速卡` 与 `Atlas A3 训练系列产品/Atlas A3 推理系列产品`/`Atlas A2 训练系列产品/Atlas A2 推理系列产品`（分平台两条）
- source_location: `aclnn_npu_format_cast.cpp:540-546`
- error_string: `The current socVersion does not support additionalDtype.`（`OP_LOGW` + `return ACLNN_ERR_PARAM_INVALID`）
- 依据：`OP_CHECK((additionalDtype == -1 && (socVersion==ASCEND910B || socVersion==ASCEND910_93)) || (additionalDtype != -1 && IsRegBase()), ...)`；
  随后 `if (additionalDtype == -1) { additionalDtype = static_cast<int>(srcTensor->GetDataType()); }`。
  即 A2/A3 必传 -1（运行时回填为 srcDtype），Atlas 350 必传非 -1 的真实枚举码。
- 文档缺/弱：文档 additionalDtype 参数表列了枚举码，A2/A3 的 -1 仅由示例代码 `int additionalDtype = -1;` 隐含，
  未以「平台 → additionalDtype 取值」约束形式显式表达。注：此关系亦可由 allowed_range_value 表达，
  补充为 constraints_in_parameters 显式约束以防遗漏。

---

## S17 — src 格式 C0 维必须匹配 dtype（extract+diagnose 双重确认）

- origin: `diagnose_confirmed`（uncertain-doc U3 提升于第 1 轮 diagnose 域；本轮 extract 域再直接从源码推导，双重确认）
- source: `extract+diagnose`（本轮 extract 域：OP_TILING_CHECK raw_checks 直接命中；第 1 轮 diagnose 域：失败日志命中）
- matched_failed_cases: `case 4`、`case 9`（361001）、`case 1`、`case 5`（561103）
- matched_error_strings:
  - `Convert6HDToNCDHW failed, the c0 should be 8 or 16 for b32, 16 for b16 and 32 for b8!`（cases 4, 9）
  - `call DoTiling failed` / `ChooseCompileInfo Failed` / `Autotiling func failed`（cases 1, 5，NZ→ND 路径
    因 C0 非法致 DSL tiling 选 compile_info 失败）
- expr_type: `shape_value_dependency`
- expr: `srcTensor.format not in ["NZ", "NDC1HWC0"] or ((srcTensor.dtype == "INT8" or srcTensor.dtype == "UINT8" or srcTensor.dtype == "FLOAT8_E4M3FN") and srcTensor.shape[-1] == 32) or ((srcTensor.dtype == "FLOAT16" or srcTensor.dtype == "BFLOAT16") and srcTensor.shape[-1] == 16) or ((srcTensor.dtype == "INT32" or srcTensor.dtype == "UINT32" or srcTensor.dtype == "FLOAT32") and (srcTensor.shape[-1] == 8 or srcTensor.shape[-1] == 16))`
- relation_params: `["srcTensor"]`
- target_platform: `all`
- source_location:
  - `canndev/ops/built-in/op_tiling/runtime/trans_data.cc:329`（`Convert6HDToNCDHW`，NDC1HWC0→NCDHW 方向；
    `axisC0 = inShape[inShape.GetDimNum() - 1]` 后 `OP_TILING_CHECK(axisC0 != C0_8 && axisC0 != C0_16 && axisC0 != C0_32, ...)`）
  - `canndev/ops/built-in/op_tiling/runtime/trans_data.cc:275`（`ConvertNCDHWTo6HD`，NCDHW→NDC1HWC0 方向，同检查）
  - `canndev/ops/built-in/op_tiling/runtime/trans_data.cc:475`（`ConvertNCDHWToFZ3D`，NCDHW→FRACTAL_Z_3D 方向，同检查）
  - `canndev/ops/built-in/op_tiling/runtime/trans_data.cc:58`（`GetC0SizeWithType`：`if (dtype==DT_INT8 || dtype==DT_UINT8) return C0_32; return C0_16;`）
  - `canndev/ops/built-in/op_tiling/runtime/trans_data.cc:977`（`call DoTiling failed`，NZ→ND 路径 DSL tiling 失败包裹）
- error_string: `Convert6HDToNCDHW failed, the c0 should be 8 or 16 for b32, 16 for b16 and 32 for b8!`（OP_TILING_CHECK）
- 依据：本轮 extract 域直接从快照内 `trans_data.cc` 推导——
  (1) `aclnnNpuFormatCastCalculateSizeAndFormat`（aclnn_npu_format_cast.cpp:532-568）按 srcFormat/dstFormat 分发到
  `CalcToNd`(:557)/`CalcToNCDHW`(:561/:565)，二者**仅计算 dstShape/actualFormat，不校验 src C0**；
  `:567 OP_LOGW("...unsupported format transformation")` 兜底返回 ACLNN_ERR_RUNTIME_ERROR。
  (2) `aclnn_npu_formatCast` 执行段 `:633 l0op::TransData(formatTensor, dstTensor->GetStorageFormat(), 1, ...)`，
  最终落入 canndev `Tiling4TransData`（trans_data.cc）。
  (3) canndev tiling 层对 NDC1HWC0（6HD）与 NCDHW 的末维 `axisC0 = inShape[inShape.GetDimNum()-1]`
  做 `OP_TILING_CHECK(axisC0 != C0_8 && axisC0 != C0_16 && axisC0 != C0_32, ...)` 强校验（:329 Convert6HDToNCDHW /
  :275 ConvertNCDHWTo6HD / :475 ConvertNCDHWToFZ3D，三条均命中本轮 raw_checks）；
  对 NZ→ND 路径，经 `DoAutoTiling`→DSL tiling（trans_data.cc:877 `call DoTiling failed`），C0 非法时 `ChooseCompileInfo Failed`（561103）。
  (4) dtype↔C0 映射：error_string 文本 `c0 should be 8 or 16 for b32, 16 for b16 and 32 for b8` +
  `GetC0SizeWithType`(trans_data.cc:58-64) `INT8/UINT8→C0_32, 其余→C0_16`：
  b8(INT8/UINT8/FLOAT8_E4M3FN)→32、b16(FLOAT16/BF16)→16、b32(INT32/UINT32/FLOAT32)→8 或 16
  （check 允许 C0_8 与 C0_16 二者；`GetC0SizeWithType` 对 b32 返回 C0_16，error_string 文本说 8）。
- 失败对照（第 1 轮 diagnose 域日志）：
  - case 4: int8 NDC1HWC0 [1,1,1,1,1,2]→NCDHW，C0=2∉{8,16,32}，361001
  - case 9: uint8 NDC1HWC0 [1,1,4,4,1,1]→NCDHW，C0=1∉{8,16,32}，361001（另含 `TilingNegativeNtc200` at trans_data_negative_target_ntc.cc:150）
  - case 1: uint32 NZ [1,1,1,1,2]→ND，C0=2∉{8,16,32}，561103（ChooseCompileInfo Failed）
  - case 5: fp32 NZ [1,1,1,1,2]→ND，C0=2∉{8,16,32}，561103（ChooseCompileInfo Failed）
- 文档缺/弱：文档 srcTensor 参数表列了支持的 format（NZ/NDC1HWC0 等），但**未约束当 srcTensor.format 为
  C0-based 格式（NZ/NDC1HWC0）时 srcTensor.shape 末维（C0）必须等于 dtype 对应的 C0 值**。此约束仅在
  canndev tiling 层（运行时）校验，aclnn host 代码（GetWorkspaceSize/CalculateSizeAndFormat）不做前置检查，
  故生成器可产出 C0=1/2 的非法用例。补充为生成期约束，防止 C0 不匹配的用例进入执行。
- 注：FRACTAL_Z_3D（33）的 FZ3D→NCDHW 反向路径（trans_data.cc:586 `ConvertFZ3DToNCDHW`，读 `axisC0 = inShape[inShape.GetDimNum()-1]`）
  读 C0 但**无 OP_TILING_CHECK**（case 6 FRACTAL_Z_3D [1,1,1,2]→NCDHW C0=2 执行成功佐证），故本约束暂仅覆盖 NZ 与 NDC1HWC0。
- 本轮 extract 域确认：以上 OP_TILING_CHECK 三条（:275/:329/:475）+ GetC0SizeWithType(:58) + l0op::TransData 派发(:633)
  + CalculateSizeAndFormat 分发(:532-568) 全部在本轮 source_raw.json raw_checks 中直接命中，无需 diagnose 即可独立推导。

---

## S18 — FRACTAL_Z_3D→NCDHW 移除（CANN CalcToNCDHW 对 4D FRACTAL_Z_3D 越界 OOM workaround）

- expr_type: `value_dependency`
- expr: `not (srcTensor.format == "FRACTAL_Z_3D" and dstFormat.range_value == 30)`
- relation_params: `["srcTensor", "dstFormat"]`
- target_platform: `all`
- source_location: `ops-math/conversion/npu_format_cast/op_host/op_api/aclnn_npu_format_cast.cpp:510-529`（`CalcToNCDHW`）+ `:564`（`FRACTAL_Z_3D→NCDHW` 派发分支）
- error_string: `torch.OutOfMemoryError: NPU out of memory. Tried to allocate <src_numel×2^48> GiB`（运行期 OOM，非 ACL 参数校验）
- 依据：`aclnnNpuFormatCastCalculateSizeAndFormat`（:532-568）在 `:564` 对 `(srcFormat==FRACTAL_Z_3D, dstFormat==NCDHW)`
  派发到 `CalcToNCDHW`（:510-529）。`CalcToNCDHW` 盲读 `viewShape.GetDim(0)..GetDim(4)` 共 5 维、**无秩校验**；
  但 FRACTAL_Z_3D 是 **4HD**（反向 `CalcNCDHWToFZ3D`:499 `*dstShapeSize=4; new int64_t[4]{D*C1*H*W, N1, N0, C0}` 可证），
  viewShape 仅 4 维（下标 0~3）→ `GetDim(4)` 越界返回哨兵 `2^48` → 派生 dst NCDHW =
  `[dim0, dim1, dim2, dim3, 2^48]`，numel = (dim0·dim1·dim2·dim3)·2^48 = **src_numel×2^48** → PB 级 OOM。
  `2^48` 跨 19 用例、跨所有 dtype/shape 恒定（PyTorch 报 "Tried to allocate" 字节数反推精确吻合），为运行时计算缺陷签名。
- 对照：`NDC1HWC0→NCDHW`（:560）共用同一 `CalcToNCDHW`，但 NDC1HWC0 是 6HD（`CalcNCDHWToNDC1HWC0`:473
  `*dstShapeSize=6`），`GetDim(4)` 在界内不越界，故不 OOM。case 52（NDC1HWC0→NCDHW）的 361001 为 TransData tiling
  `Convert6HDToNCDHW`（trans_data.cc:317）axes 校验失败，**另码点，非本 bug**。
- 实测：run `aclnnNpuFormatCast-20260717-155353-963852/iter_001`（Atlas A2，supports_npu=true，真 NPU 执行），
  `(srcFormat=FRACTAL_Z_3D, dstFormat=30)` **19/19 全 OOM**（用例 id
  1,7,8,31,33,41,50,51,54,64,69,70,74,85,88,89,91,96,98）；其余 6 种格式对 0 失败。
  失败用例 src shape 均 1~64 元素（非 shape 过大），OOM 全由 ×2^48 派生所致。
- 与 S17 关系：S17 注记「case 6 FRACTAL_Z_3D [1,1,1,2]→NCDHW C0=2 执行成功」为早期 run（executor 模板未调
  `CalculateSizeAndFormat`、dst shape 用声明值）之观察；当前 run 模版（.tql NPU prelude 调
  `aclnnNpuFormatCastCalculateSizeAndFormat` 派生 dst）下 FZ3D→NCDHW 19/19 OOM，根因在**派生阶段（先于 tiling）**，
  与 S17 所述 tiling 层 `ConvertFZ3DToNCDHW` 无 C0 `OP_TILING_CHECK` 不矛盾——两处不同代码点（CalcToNCDHW 派生 vs TransData tiling）。
- 与 [3] 白名单关系：A2 `constraints_in_parameters[3]`（`value_dependency`，格式对白名单）第 4 子句
  `(srcTensor.format == "FRACTAL_Z_3D" and dstFormat.range_value == 30)` 允许此对，正是 19 OOM 的来源。
  实证 [3] 被生成器强制执行：A2 100 条用例的 (srcFormat, dstFormat) 分布（19/3/18/21/19/20）与 [3] 6 子句逐条精确吻合，
  桶为 5×5=25 笛卡尔只出 6=[3] 白名单。本条以 `not(...)` 与 [3] AND 叠加，等价于从白名单移除该子句；
  若 supplementer 走 op=replace，直接删 [3] 第 4 子句亦可。
  **应用后须验证 `constraints_patch.json` 实际移除该对、复跑生成确认 cases 中 (FRACTAL_Z_3D, 30) 归零、再执行确认 19 OOM 消失。**
- 文档缺/弱：文档 `aclnnNpuFormatCast.md:25` 明文「完成 NCDHW←→FRACTAL_Z_3D 的转换功能」（双向支持），
  与运行时实测冲突。**本条为 CANN runtime 缺陷（`CalcToNCDHW` 对 4D src 越界）的生成期 workaround，非算子固有约束**；
  CANN 修复 `CalcToNCDHW`（对 4D FRACTAL_Z_3D 做秩校验或按 [D*C1*H*W,N1,N0,C0] 正确解构）后须摘除本条并恢复 [3] 第 4 子句。
  归属 executor_bug（CANN 侧），非 constraint_extraction/prompt 优化范畴。

---

## S19 — NDC1HWC0→NCDHW D>1 或 C1>1 时 axes 校验失败 workaround（条件移除）

- expr_type: `value_dependency`
- expr: `not (srcTensor.format == "NDC1HWC0" and dstFormat.range_value == 30) or (srcTensor.shape[1] == 1 and srcTensor.shape[2] == 1)`
- relation_params: `["srcTensor", "dstFormat"]`
- target_platform: `all`
- source_location:
  - `ops-math/conversion/npu_format_cast/op_host/op_api/aclnn_npu_format_cast.cpp:560`（`NDC1HWC0→NCDHW` 派发分支）
  - `aclnn_npu_format_cast.cpp:510-529`（`CalcToNCDHW`，按 viewShape[0..4] 原样拷贝为 `[N,C,D,H,W]`，对 NDC1HWC0 的 `[N,D,C1,H,W,C0]` 产生 D/C1 错位且丢弃 C0）
  - `canndev/ops/built-in/op_tiling/runtime/trans_data.cc:339`（`Convert6HDToNCDHW`，`the corresponding axes is not same` OP_TILING_CHECK）
  - `canndev/ops/built-in/op_tiling/runtime/trans_data.cc:1021`（`Tiling4TransData`，`realShapeConverter failed`）
- error_string: `op[TransData], Convert6HDToNCDHW failed, the corresponding axes is not same![FUNC:Convert6HDToNCDHW][FILE:trans_data.cc][LINE:339]` + `op[TransData], realShapeConverter failed![FUNC:Tiling4TransData][FILE:trans_data.cc][LINE:1021]` + `LaunchKernelV2 failed because value 0 for parameter blockDim is invalid`（361001）
- 依据：`aclnnNpuFormatCastCalculateSizeAndFormat`（:532-568）在 `:560` 对 `(srcFormat==NDC1HWC0, dstFormat==NCDHW)`
  派发到 `CalcToNCDHW`（:510-529）。`CalcToNCDHW` 盲读 `viewShape.GetDim(0..4)` 共 5 维，
  对 NDC1HWC0 的 6HD viewShape `[N,D,C1,H,W,C0]` 产生 `[N(=dim0), C(=dim1=D), D(=dim2=C1), H(=dim3), W(=dim4)]`，
  **C 维取成了 D、D 维取成了 C1、C0 维被丢弃**。当 `D>1` 或 `C1>1` 时，dst numel = N·D·C1·H·W
  ≠ src numel = N·D·C1·H·W·C0（差 C0 倍），TransData 的 `Convert6HDToNCDHW`（trans_data.cc:339）
  检测到 6HD→5HD 的 axes 映射不匹配，报 `the corresponding axes is not same`，tiling 失败→blockDim=0→361001。
  当 `D==1 且 C1==1` 时，dst numel = N·1·1·H·W，数据量恰好退化一致（C0 维的元素全部折叠到 C 维的单个槽位），
  TransData 的 axes 退化映射可通过校验，执行成功。
- 失败对照（本轮 run aclnnNpuFormatCast-20260717-182002-902185/iter_001，Atlas A2 真 NPU 手动执行）：
  - case 20: uint8 NDC1HWC0 [1,2,1,1,1,32]→NCDHW，D=2>1，dst_shape=(1,2,1,1,1)，361001
  - case 58: fp32 NDC1HWC0 [2,8,65534,1,4,16]→NCDHW，D=8>1 且 C1=65534>1，dst_shape=(2,8,65534,1,4)，361001
- 成功对照（同 run，同格式对，D==1 且 C1==1）：
  - case 1: uint32 [2,1,1,1,1,8]→NCDHW，PASS
  - case 10: uint8 [1,1,1,2,1,32]→NCDHW，PASS
  - case 14: fp16 [1,1,1,2,1,16]→NCDHW，PASS
  - case 61: uint8 [1,1,1,1,1,32]→NCDHW，PASS
  - （共 18/20 PASS，全部满足 D==1 且 C1==1）
- 与 S18 关系：S18 注记「case 52（NDC1HWC0→NCDHW）的 361001 为 TransData tiling `Convert6HDToNCDHW`（trans_data.cc:317）
  axes 校验失败，另码点，非本 bug」即指本条。S18 移除 FRACTAL_Z_3D→NCDHW（越界 OOM），本条移除 NDC1HWC0→NCDHW
  的 `D>1 or C1>1` 子集（axes 不匹配），二者为同一 `CalcToNCDHW` 函数的不同缺陷签名，互不重叠。
- 与 [3] 白名单关系：A2 `constraints_in_parameters[3]`（格式对白名单）第 3 子句
  `(srcTensor.format == "NDC1HWC0" and dstFormat.range_value == 30)` 允许此对。本条以 `not(...) or (...)`
  与 [3] AND 叠加，等价于把该子句收窄为「仅 D==1 且 C1==1 时允许」。
  **应用后须验证 `constraints_patch.json` 实际收窄、复跑生成确认 cases 中 (NDC1HWC0, 30, D>1 or C1>1) 归零、再执行确认 361001 axes 失败消失。**
- 文档缺/弱：文档 `aclnnNpuFormatCast.md:25` 明文「完成 NCDHW←→NDC1HWC0 的转换功能」（双向支持），
  与运行时实测部分冲突——当 D>1 或 C1>1 时反向转换（NDC1HWC0→NCDHW）因 `CalcToNCDHW` 的 viewShape 维度错位
  导致 TransData axes 校验失败。**本条为 CANN runtime 缺陷（`CalcToNCDHW` 对 6HD NDC1HWC0 src 按 NCDHW 语义盲读维度）
  的生成期 workaround，非算子固有约束**；CANN 修复 `CalcToNCDHW`（对 6HD NDC1HWC0 正确按 `[N,D,C1,H,W,C0]`
  解构并计算 `C=C1*C0`）后须摘除本条并恢复 [3] 第 3 子句的完整形式。归属 executor_bug（CANN 侧），非 constraint_extraction/prompt 优化范畴。
