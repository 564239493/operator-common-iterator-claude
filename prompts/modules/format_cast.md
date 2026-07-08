---
module: format_cast
description: 格式转换算子：format↔rank 硬对应、CalculateSizeAndFormat 派生输出、per-platform 取值差异、dtype 等式
triggers:
  - kind: operator_name_regex
    value: "aclnn.*FormatCast"
  - kind: doc_contains
    value: "aclnnStatus\\s+aclnn\\w*CalculateSizeAndFormat|aclnnStatus\\s+aclnn\\w*GetSizeAndFormat|aclnnStatus\\s+aclnn\\w*GetShapeAndFormat"
depends_on: ["acl_format_enum"]
---

# 模块 format_cast（按需加载）

> 本模块原为 `operator_constraints_extract_v4.md` §4.6.7 + §4.6.8 + §4.6.11 + §4.6.12 + §6.3 模式9，按算子特征由 `scripts/select_prompt.py` 装配到活跃提示词末尾。原 § 编号保留，便于交叉引用按标题文本定位。

#### 4.6.7 格式-秩（format↔rank）硬对应表（v7 新增，通用规则）

> 本节来自 `aclnnNpuFormatCast` 闭环：iter_001 把 `dstTensor.dimensions=[4,8]`
> 与 `srcTensor.dimensions=[2,6]` 当成**扁平 rank 区间**提取，但漏掉 format 与
> rank 的一一对应关系，生成器把 `format` 与 `dimensions` 当独立字段采样，产出
> `NCDHW + 8D`、`NDC1HWC0 + 2D`、`FRACTAL_Z_3D + 6D` 这类非法组合。NPU 真机校验
> 直接拒绝（`AclNN_Parameter_Error(EZ1001): Input Tensor format not match it's
> shape`），CPU golden 只做 reshape 不校验 format↔rank 故全部漏网。该规则按
> `format.value` 的形态触发，**不**按算子名硬编码。

##### A. 昇腾格式标准 rank 对应表

| `format.value` | 标准 rank | 说明 |
| -------------- | --------- | ---- |
| `ND` | 变量（由文档 view shape 区间给出） | 自由 rank，仍须落入文档给定的 `[min,max]` |
| `NC` | 2 | `(N, C)`，2D 排布（全连接 / 矩阵运算） |
| `NCL` | 3 | `(N, C, L)` |
| `NCHW` / `NHWC` / `HWCN` | 4 | 4D 排布（`HWCN`：Height×Width×Channel×Batch，图像处理专用） |
| `NCDHW` / `NDHWC` | 5 | 5D 排布 |
| `NC1HWC0` / `NC1HWC0_C04` | 5 | 5D，`C1`/`C0` 为分块轴（`_C04` 为 `C0=4` 变体） |
| `NDC1HWC0` | 6 | 6D，`C1`/`C0` 为分块轴 |
| `NZ` / `FRACTAL_NZ` / `FRACTAL_NZ_C0_16` / `FRACTAL_NZ_C0_32` | 5 | 沿用 §4.6.5 既有 NZ 块尺寸规则（不重复块尺寸细节，仅引用 rank） |
| `FRACTAL_Z_3D` | 4 | storage shape 4D（`[D*C1*H*W, N1, N0, C0]`） |
| `FRACTAL_Z` | 4 | storage shape 4D（若文档涉及） |
| `NCHW_VECT_C0_16` | 5 | 5D 向量化排布 |

> 标准 rank 是**不可违反的硬约束**：当文档显式声明某张量的 `format.value` 是
> 上表中某格式时，其 `len(shape)` 必须等于对应 rank（ND 例外，落入文档区间即可）。

##### B. 适用判定

满足下列**任一**条件时，**必须**执行本节规则：

1. 某个 `aclTensor` / `aclTensorList` 参数的 `format.value`（按平台）是**多格式
   列表**（如 `["ND","NCDHW","NDC1HWC0","FRACTAL_Z_3D","NZ"]`），且这些格式在
   §A 对应表中**存在不同的标准 rank**；
2. 文档原文出现"format 与 shape 维度需匹配 / shape 维度需与 format 对应 /
   Input Tensor format not match it's shape"等 format↔rank 一致性约束信号；
3. 文档给出某张量 view shape rank 区间（如 `[2,6]`）与 storage shape rank
   区间（如 `[4,8]`），且 format 列表跨多种 rank 不同的格式族（ND/NCDHW/
   NDC1HWC0/FRACTAL_Z_3D 等）。

##### C. 必须产出的 `constraints_in_parameters` 条目

对每个满足适用判定、且 `format.value` 为多格式列表的张量参数 `T`，必须在
`constraints_in_parameters[平台]` 中追加**单一** `format_rank_consistency` 条目，
把"逐格式 rank 守卫"合并为一条带分支的布尔表达式（参考 §6.3 模式 8）。模板：

```text
expr_type: format_rank_consistency
expr: (T.format == "ND" and 2 <= len(T.shape) <= 6) or \
      (T.format == "NCL" and len(T.shape) == 3) or \
      (T.format == "NCDHW" and len(T.shape) == 5) or \
      (T.format == "NDC1HWC0" and len(T.shape) == 6) or \
      (T.format == "FRACTAL_Z_3D" and len(T.shape) == 4) or \
      (T.format == "NZ" and len(T.shape) == 5)
relation_params: ["T"]
src_text: "srcTensor 的 view shape 维度不在[2, 6]的范围；dstTensor 的 storage
shape 维度不在[4, 8]的范围；Input Tensor format not match it's shape。"
```

**规则要点**：

1. **逐格式守卫不可省略**：只要 `format.value` 列表里出现某格式，上表中对应
   rank 必须在 `expr` 中以 `(T.format == "X" and len(T.shape) == R)` 形式出现；
   ND 类按文档区间写 `min <= len(T.shape) <= max`。**不允许**只保留 `dimensions.value`
   的扁平 `[min,max]` 区间而省略逐格式守卫。
2. **合并为单一 expr**：所有分支用 `or` 串接为**一条** `format_rank_consistency`
   条目，**不**拆成多条独立 `shape_equality`（否则生成器会把它们当作并列候选
   而丢失 format 门控上下文，与 §6.3 模式 6 反例同理）。
3. **`dimensions.value` 仍可保留**文档给出的 `[min,max]` 作为弱范围（供生成器
   初筛 rank 槽数），但**必须**同时落库上述逐格式守卫，作为不可违反的硬约束。
4. **src 必须可溯**：`src_text` 摘录文档中"维度区间""format↔shape 一致性"等
   原文短语；若文档未显式写一致性短语但 format 列表跨多种 rank，仍须按 §A 表
   落库，`src_text` 摘录 format 列表与维度区间原文并补注"format↔rank 由 §4.6.7
   标准对应表推导"。
5. **NZ 族不重复块尺寸**：NZ/FRACTAL_NZ 等只在本节写 rank==5 的守卫，块尺寸
   硬约束（`shape[3]==16`/`shape[4]==16`）仍按 §4.6.5 落库，不在此重复。
6. **平台差异**：若不同平台 format 列表不同（如 A2 含 `NCL`、A3 不含），逐平台
   分别落库对应分支；同一平台 format 列表里的每个格式都必须出现在该平台的 expr 中。
7. **`format.value` 字面保真（不外扩同义别名）**：`format.value` 只列文档为该
   张量枚举的 §5.3 字面短名，**不**为同义短名做别名外扩。`NZ` 与 `FRACTAL_NZ`
   虽同指 29，但文档参数表只写其中一种（如 `ND、NZ、NCDHW、...`），`format.value`
   就只列 `"NZ"`，**不**额外追加 `"FRACTAL_NZ"`。expr 里的 `==`/`in` 集合必须与
   `format.value` 严格同源——不引入 `format.value` 里没有的别名分支，也不遗漏
   `format.value` 里出现的短名；仅当 `NZ` 族多个短名**同时**出现在同一
   `format.value` 时，才用 `in (...)` 列出实际出现的那几个。

##### D. 反例（禁止）

- `format.value=["ND","NCDHW","NDC1HWC0","FRACTAL_Z_3D"]` 但 `constraints_in_parameters`
  只有 `2 <= len(T.shape) <= 8` 一条扁平区间，**无**逐格式守卫 → 漏抓，违规则 C.1。
- 把逐格式守卫写成 4 条独立 `shape_equality`（`len(T.shape)==5`、`len(T.shape)==6`、
  `len(T.shape)==8`、`2<=len(T.shape)<=6`）→ 丢失 format 门控，违规则 C.2。
- `dstTensor.format == "NCDHW"` 但 `len(dstTensor.shape) == 8`（NCDHW 应为 5D）
  未被任何约束拦截 → 正是 aclnnNpuFormatCast iter_001 的根因。

##### E. aclnnNpuFormatCast 落库示例（srcTensor / dstTensor）

```json
{
  "srcTensor": {
    "Atlas A2 训练系列产品/Atlas A2 推理系列产品": {
      "description": "输入张量，view shape rank∈[2,6]，format 与 rank 须满足 §4.6.7 逐格式守卫",
      "type": {"value": "aclTensor", "src_text": ""},
      "format": {"value": ["ND","NCL","NCDHW","NDC1HWC0","NZ","FRACTAL_Z_3D"], "src_text": "支持的数据格式..."},
      "dimensions": {"value": [2, 6], "src_text": "srcTensor 的 view shape 维度不在[2, 6]的范围"},
      "dtype": {"value": ["FLOAT16","FLOAT32","INT8","BFLOAT16"], "src_text": ""},
      "is_optional": {"value": false, "src_text": ""},
      "is_support_discontinuous": {"value": true, "src_text": ""},
      "is_operator_param": {"value": true, "src_text": ""}
    }
  },
  "dstTensor": {
    "Atlas A2 训练系列产品/Atlas A2 推理系列产品": {
      "description": "[DERIVED] shape 与 format 由 aclnnNpuFormatCastCalculateSizeAndFormat(srcTensor, dstFormat, additionalDtype) 派生，见 §4.6.8；storage shape rank∈[4,8]，format↔rank 须满足 §4.6.7 逐格式守卫",
      "type": {"value": "aclTensor", "src_text": ""},
      "format": {"value": ["ND","NZ","NCDHW","NDC1HWC0","FRACTAL_Z_3D","FRACTAL_NZ_C0_16","FRACTAL_NZ_C0_32"], "src_text": "actualFormat 取值：ACL_FORMAT_ND(2)/FRACTAL_NZ(29)/NCDHW(30)/NDC1HWC0(32)/FRACTAL_Z_3D(33)/FRACTAL_NZ_C0_16(50)/FRACTAL_NZ_C0_32(51)"},
      "dimensions": {"value": [4, 8], "src_text": "dstTensor 的 storage shape 维度不在[4, 8]的范围"},
      "dtype": {"value": ["FLOAT16","FLOAT32","INT8","BFLOAT16"], "src_text": ""},
      "is_optional": {"value": false, "src_text": ""},
      "is_support_discontinuous": {"value": true, "src_text": ""},
      "is_operator_param": {"value": true, "src_text": ""}
    }
  }
}
```

对应 `constraints_in_parameters`（同一平台）至少包含：

```text
# srcTensor format-rank 一致性
expr_type: format_rank_consistency
expr: (srcTensor.format == "ND" and 2 <= len(srcTensor.shape) <= 6) or \
      (srcTensor.format == "NCL" and len(srcTensor.shape) == 3) or \
      (srcTensor.format == "NCDHW" and len(srcTensor.shape) == 5) or \
      (srcTensor.format == "NDC1HWC0" and len(srcTensor.shape) == 6) or \
      (srcTensor.format == "NZ" and len(srcTensor.shape) == 5) or \
      (srcTensor.format == "FRACTAL_Z_3D" and len(srcTensor.shape) == 4)
relation_params: ["srcTensor"]
src_text: "srcTensor 的 view shape 维度不在[2, 6]的范围；Input Tensor format not match it's shape。"

# dstTensor format-rank 一致性（dstTensor.shape/format 由子接口派生，见 §4.6.8）
expr_type: format_rank_consistency
expr: (dstTensor.format == "ND" and 4 <= len(dstTensor.shape) <= 8) or \
      (dstTensor.format in ("NZ","FRACTAL_NZ_C0_16","FRACTAL_NZ_C0_32") and len(dstTensor.shape) == 5) or \
      (dstTensor.format == "NCDHW" and len(dstTensor.shape) == 5) or \
      (dstTensor.format == "NDC1HWC0" and len(dstTensor.shape) == 6) or \
      (dstTensor.format == "FRACTAL_Z_3D" and len(dstTensor.shape) == 4)
relation_params: ["dstTensor"]
src_text: "dstTensor 的 storage shape 维度不在[4, 8]的范围；actualFormat 取值：ACL_FORMAT_ND(2)/FRACTAL_NZ(29)/NCDHW(30)/NDC1HWC0(32)/FRACTAL_Z_3D(33)/FRACTAL_NZ_C0_16(50)/FRACTAL_NZ_C0_32(51)。"
```

#### 4.6.8 派生输出张量（CalculateSizeAndFormat 类两段式语义，v7 新增）

> 本节来自 `aclnnNpuFormatCast` 闭环：iter_001 生成器直接给 `dstTensor` 随机
> 赋 shape/format，违背两段式语义——文档第 28、32 行明确「必须先调用
> `aclnnNpuFormatCastCalculateSizeAndFormat` 计算出 dstTensor 的 shape 和实际
> 数据格式，再调用两段式接口」。生成器独立赋值导致 dstTensor.shape 与
> srcTensor 不可由子接口推导，叠加 §4.6.7 缺失产生大量非法组合。该规则按
> "是否存在派生子接口"的语义触发，**不**按算子名硬编码。

##### A. 适用判定

满足下列**全部**条件时，**必须**执行本节规则：

1. 算子文档中存在形如 `aclnnXxxCalculateSizeAndFormat` / `aclnnXxxGetSizeAndFormat`
   / `aclnnXxxGetShapeAndFormat` 的**子接口**（函数原型独立出现）；
2. 该子接口的输出包含 `dstShape` + `actualFormat`（或同义返回量），用于构造
   主接口的 dstTensor；
3. 主接口文档明确写出「必须先调用 ...CalculateSizeAndFormat ... 再调用两段式
   接口」「dstTensor 的 shape/format 由 ... 计算」等派生语义短语。

##### B. 必须产出的派生标记

满足适用判定时，对主接口中由子接口派生 shape/format 的输出张量 `D`（如
`aclnnNpuFormatCast` 的 `dstTensor`）：

1. **ParamAttributes 标记**：在 `D` 的 `description` 字段（复用既有字段，**不**
   新增 schema 字段）前缀 `[DERIVED]`，并写明派生子接口签名，例如：
   `"[DERIVED] shape 与 format 由 aclnnNpuFormatCastCalculateSizeAndFormat(srcTensor, dstFormat, additionalDtype) 派生，生成器不得独立随机赋值；见 §4.6.8"`；
2. **`dimensions.value` / `format.value` 保留文档候选**作为弱提示（供生成器
   初筛与 §4.6.7 逐格式守卫使用），但 `description` 的 `[DERIVED]` 标记表明
   这些字段的真实取值由子接口在执行期填充，生成器**不得**独立随机赋值；
3. **生成器侧只采样** `srcTensor(shape, dtype, format) + dstFormat + additionalDtype`
   等子接口入参；`D.shape` / `D.format` 在用例构造期留空或标记为 `DERIVED`，
   由执行器调用子接口回填。

##### C. 必须产出可求解的 `derived_value` 约束（v3 增补修正）

> 早期版本要求为派生输出张量 `D` 追加一条 `expr=""` 的 `derived_value`
> `constraints_in_parameters` 条目作为派生语义标记。但空 `expr` 条目无法在生成期
> `eval()`，对生成器无用——生成器无法从空 `expr` 读出派生规则，转而对 `[DERIVED]`
> 输出参数独立随机赋值，导致 86/100 条用例 dstTensor.format/actualFormat 与期望不一致。
> `[DERIVED]` description 文本标记亦不足以单独约束生成器（生成器未识别该标记）。
> 正确做法：当文档存在确定映射（如 dtype/format 组合表 → actualFormat）时，
> `derived_value.expr` **必须**编码为可求解的查找/派生表达式（不得为空串）；
> 若映射确实无法表达为可求解 expr，则**不**产出该条目（派生语义退由 `[DERIVED]`
> description 承载），**禁止**产出 `expr=""` 的空壳条目。

对派生输出张量 `D`，分两种情况：

1. **文档存在确定映射**（`dtype_support_description` / `format_support_description`
   或文档正文存在从子接口入参到 `D` 取值的确定对应表，如 srcTensor.dtype × dstFormat
   × additionalDtype → actualFormat）时，**必须**为 `D` 在
   `constraints_in_parameters[每个支持平台]` 中产出**一条** `derived_value` 约束，
   其 `expr` 编码该映射为可 `eval()` 的 Python 布尔表达式（参见 §6.3 模式 9）：
   - **恒等映射**（`D` 取值恒等于某子接口入参，如 actualFormat == dstFormat）→
     直接等式：`D.range_value == keyParam.range_value`；
   - **查找表映射**（多行 combo 表）→ 析取所有合法行，每行合取键值与目标值：
     `(key1 == v1 and key2 == v2 and ... and D.range_value == w) or (key1 == v1' and ...) or ...`；
     亦可等价写为 if/elif/else 链；
   - **格式派生**（`D.format` 由 actualFormat 查表得出）→ 同样用析取或 if/elif/else
     把 actualFormat → format 的逐行对应编码为可求解 expr；
   - `relation_params` 必须包含 `D` 及全部键参数；`src_text` 摘录映射表原文。
2. **文档无确定映射**（派生关系依赖 NPU 运行期计算，文档未给出可枚举的对应表）时，
   **不**产出 `constraints_in_parameters` 派生条目；派生语义由 §4.6.8 B 的 `[DERIVED]`
   description 标记承载。**不得**产出 `expr=""` 的空壳条目（违 §4.7.2 "expr 不得为
   空字符串"）。
3. **`format_rank_consistency` 仍须落库**：无论是否产出 `derived_value`，派生张量
   仍须满足 §4.6.7 的 `format_rank_consistency` 守卫（针对子接口回填后的实际
   shape/format）；§4.6.7 守卫 rank↔format 一致性，§4.6.8 B 声明 shape/format 来源，
   二者职责不同，不得互相替代，也不得因 B 已标记 `[DERIVED]` 就省略 §4.6.7 守卫。

##### D. 平台差异

- 若不同平台的派生子接口或 `dstFormat` 候选不同（如 Atlas 350 dstFormat 固定
  为 `FRACTAL_NZ(29)`，A2/A3 dstFormat 为 `ND/NZ/NCDHW/NDC1HWC0/FRACTAL_Z_3D`），
  逐平台在 `D.description` 中分别写明 `[DERIVED]` 标记与对应子接口/`dstFormat`
  原文；存在确定映射时逐平台落库可求解 `derived_value` 条目（见 C.1），各平台
  `expr` 按该平台映射表分别编码；
- `dstFormat` 作为独立输入参数（非派生）正常提取 `allowed_range_value`（`type=enum`），
  不标记 `[DERIVED]`。

##### E. 反例（禁止）

- `dstTensor` 的 `description` 无 `[DERIVED]` 标记 → 生成器把 `dstTensor.shape`/
  `format` 当独立字段随机采样，违规则 B.1。
- 为 `dstTensor` 在 `constraints_in_parameters` 中产出 `expr=""` 的 `derived_value`
  条目而文档实际存在确定映射（如 `dtype_support_description` 含 actualFormat 对应表）→
  空 `expr` 无法 `eval()`，生成器无法读出派生规则、独立随机赋值，违 §4.7.2
  "expr 不得为空字符串"与 §4.6.8 C.1；必须把映射编码为可求解 expr。
- 文档无确定映射却仍产出 `expr=""` 的 `derived_value` 空壳条目 → 违 §4.6.8 C.2；
  应不产出该条目，派生语义由 `[DERIVED]` description 承载。
- 仅靠 `[DERIVED]` description 文本标记而不产出可求解 `derived_value` 条目（当文档
  存在确定映射时）→ 生成器未识别文本标记、独立随机赋值，违 §4.6.8 C.1。
- 把 `dstTensor.shape == aclnnNpuFormatCastCalculateSizeAndFormat(...).dstShape`
  写进 `expr` 当布尔表达式 → 该签名无法在生成期 `eval()`（子接口是 NPU 侧运行期
  调用），违 §6.1 合法 Python 布尔表达式要求。
- 因 `dstTensor` 是派生量就省略 §4.6.7 的 `format_rank_consistency` 守卫 →
  派生量仍须满足 format↔rank 一致性，二者职责不同，不得互相替代。

#### 4.6.11 产品相关参数取值范围差异（per-platform 候选值分歧，v3 增补）

> 本节来自 `aclnnNpuFormatCast` 闭环：`additionalDtype` 参数在"参数说明"总表中
> 统一列出候选 `ACL_FLOAT16(1)、ACL_BF16(27)、INT8(2)、ACL_FLOAT8_E4M3FN(36)`
> （即 `1/27/2/36`），但在"约束说明"按产品分节的 `<details>` 块与调用示例中，
> `<term>Atlas A3 训练系列产品/Atlas A3 推理系列产品</term>`、
> `<term>Atlas A2 训练系列产品/Atlas A2 推理系列产品</term>` 下 `additionalDtype`
> 实际固定为 `-1`（C0 改由 `srcTensor` 的基础类型计算，见原文"C0计算方法：
> 32B / size of srcTensor的基础类型"与示例代码 `int additionalDtype = -1;`）。
> v3 提示词无规则要求按产品分别识别这种"同一参数在不同产品下候选值不同"的
> 分歧，导致提取器把总表候选 `{1,27,2,36}` 直接套用到所有平台，A3/A2 平台
> 生成器会采样出文档示例不支持的 `additionalDtype` 值。该规则按"参数候选值随
> 产品分歧"的语义触发，**不**按算子名硬编码。

##### A. 适用判定

满足下列**任一**条件时，**必须**执行本节规则：

1. 某非张量标量/枚举参数 `P` 的候选值在"参数说明"总表中列出，但在文档"约束
   说明"章节按产品分节（`<details>` / `<term>` 块）给出**不同**的候选值或
   **固定单一值**；
2. 文档按产品分节的调用示例代码中，`P` 的赋值与总表候选不一致（如某产品示例
   写 `P = -1` 而总表无 `-1` 候选），且该产品分节的"C0 计算方法 / 推导方法"
   明确 `P` 不参与计算（改由其它参数推导）；
3. 文档对 `P` 显式标注"仅在 X 产品下生效 / X 产品下忽略 / X 产品下固定为 Y"
   等产品相关取值差异短语。

##### B. 必须产出的 per-platform `allowed_range_value`

满足适用判定时，对参数 `P` 在 `product_support` 中**每个**平台分别产出
`allowed_range_value` 条目，各平台 `value` 取该平台分节/示例中的**实际候选**，
**不**把总表候选统一套用到所有平台：

1. **逐平台候选**：`allowed_range_value.type="enum"`，`value` 为该平台实际候选
   列表（数值候选用裸数字，如 `[1, 27, 2, 36]`、`[-1]`；字符串候选用 §5.2
   受控字典标签）；各平台 `value` **可以不同**，这正是本节要捕获的产品差异；
2. **占位/未用值**：若某产品分节表明 `P` 不参与计算（改由其它参数推导）且
   示例固定写 `P = -1`（或文档指定的其它占位值），该平台 `value` 必须为**仅含
   该占位值的单元素列表**（如 `[-1]`），**不得**追加总表候选，也**不得**留空
   `[]`——留空会被生成器当作无约束自由采样，反而产出非法值；
3. **src_text 逐平台溯源**：各平台 `allowed_range_value.src_text` 必须摘录**该
   产品分节**的候选原文或示例代码行（如 A3/A2 摘录 `int additionalDtype = -1;`
   与"C0 = 32B / size of srcTensor的基础类型"），**不得**只抄总表"参数说明"行；
4. **`type` / `dtype` / `format` 逐平台一致**：`type.value`（如 `"int"`）、
   `dtype.value`（如 `["int"]`，按 §4.6.3 标量回填）、`format.value`（`"N/A"`）
   各平台保持一致；只有 `allowed_range_value.value` 随产品分歧。

##### C. aclnnNpuFormatCast `additionalDtype` 落库示例

```json
{
  "additionalDtype": {
    "Atlas 350 加速卡": {
      "description": "推断 FRACTAL_NZ 的 C0 大小所用的基本数据类型；C0 = 32B / size of additionalDtype",
      "type": {"value": "int", "src_text": "int additionalDtype"},
      "format": {"value": "N/A", "src_text": ""},
      "dtype": {"value": ["int"], "src_text": ""},
      "dimensions": {"value": [], "src_text": ""},
      "allowed_range_value": {
        "value": [1, 27, 2, 36],
        "type": "enum",
        "src_text": "ACL_FLOAT16(1)、ACL_BF16(27)、INT8(2)、ACL_FLOAT8_E4M3FN(36)"
      },
      "is_optional": {"value": true, "src_text": "可选输入"},
      "is_support_discontinuous": {"value": false, "src_text": ""},
      "is_operator_param": {"value": true, "src_text": ""}
    },
    "Atlas A3 训练系列产品/Atlas A3 推理系列产品": {
      "description": "A3/A2 下 C0 由 srcTensor 基础类型计算，additionalDtype 不参与，固定为 -1；见 §4.6.8 派生子接口",
      "type": {"value": "int", "src_text": "int additionalDtype"},
      "format": {"value": "N/A", "src_text": ""},
      "dtype": {"value": ["int"], "src_text": ""},
      "dimensions": {"value": [], "src_text": ""},
      "allowed_range_value": {
        "value": [-1],
        "type": "enum",
        "src_text": "示例代码 int additionalDtype = -1; C0 = 32B / size of srcTensor的基础类型"
      },
      "is_optional": {"value": true, "src_text": "可选输入"},
      "is_support_discontinuous": {"value": false, "src_text": ""},
      "is_operator_param": {"value": true, "src_text": ""}
    },
    "Atlas A2 训练系列产品/Atlas A2 推理系列产品": {
      "description": "A3/A2 下 C0 由 srcTensor 基础类型计算，additionalDtype 不参与，固定为 -1；见 §4.6.8 派生子接口",
      "type": {"value": "int", "src_text": "int additionalDtype"},
      "format": {"value": "N/A", "src_text": ""},
      "dtype": {"value": ["int"], "src_text": ""},
      "dimensions": {"value": [], "src_text": ""},
      "allowed_range_value": {
        "value": [-1],
        "type": "enum",
        "src_text": "示例代码 int additionalDtype = -1; C0 = 32B / size of srcTensor的基础类型"
      },
      "is_optional": {"value": true, "src_text": "可选输入"},
      "is_support_discontinuous": {"value": false, "src_text": ""},
      "is_operator_param": {"value": true, "src_text": ""}
    }
  }
}
```

##### D. 反例（禁止）

- `additionalDtype` 在所有平台都用 `value=[1,27,2,36]` → A3/A2 平台采样出
  `additionalDtype=1` 等文档示例不支持的值，违规则 B.1/B.2；
- A3/A2 平台 `allowed_range_value.value=[]`（留空）→ 生成器当作无约束自由采样，
  同样产出非法值，违规则 B.2；
- A3/A2 平台 `allowed_range_value.src_text` 抄总表"ACL_FLOAT16(1)、ACL_BF16(27)..."
  → 溯源错误，违规则 B.3；
- 因 `additionalDtype` 在 A3/A2 固定为 `-1` 就把它从 `inputs` 删除 → 它仍是
  `aclnnNpuFormatCastCalculateSizeAndFormat` 子接口的入参，§4.6.8 B.3 要求生成器
  采样 `srcTensor + dstFormat + additionalDtype`；删除会使生成器丢失该子接口入参、
  并破坏 `dstTensor` 的 `[DERIVED]` 派生语义与 `derived_value` 约束的
  `relation_params` 一致性（违 §4.6.8 B/C.1）；正确做法是保留参数
  卡片、仅收紧 `allowed_range_value.value`。

#### 4.6.12 格式转换算子的 dtype 等式约束（v3 增补，通用规则）

> 本节来自 `aclnnNpuFormatCast` 闭环：iter_001 `constraints_in_parameters` 三平台均
> 未提取 `srcTensor.dtype == dstTensor.dtype` 跨参等式，300/300 条用例 src.dtype !=
> dst.dtype（350: int32→int8；A3: uint8→int8；A2: uint8→int8），且 dstTensor.range_values
> 按 int8 负值域生成。文档 GetWorkspaceSize 表每行 src dtype == dst dtype，功能说明为
> 纯格式转换（数据值不变），示例代码用 srcDtype 构造 dstTensor，cases_executor.py 注释
> "data values are preserved"。该规则按算子语义与文档 dtype 表触发，**不**按算子名硬编码。

##### A. 适用判定

满足下列**全部**条件时，**必须**执行本节规则：

1. 算子功能为**格式转换 / 布局变换**类（`function_explanation` 或正文含"格式转换"、
   "数据值不变"、"纯格式转换"、"data values are preserved"、"only the memory layout
   changes"等语义短语，表明算子只改变内存排布、不改变数据数值）；
2. 文档 dtype/format 组合表（GetWorkspaceSize 接口表或 `dtype_support_description`）
   **每一行** srcTensor 数据类型 == dstTensor 数据类型（如 INT8→INT8、UINT8→UINT8）；
3. 或文档示例代码用同一 srcDtype 同时构造 srcTensor 与 dstTensor。

##### B. 不适用场景（禁止套用）

以下场景**不得**套用本规则：

1. 算子语义涉及**数据类型转换**（如 cast / convert dtype 类算子，src.dtype !=
   dst.dtype 是预期行为）；
2. 算子同时做格式转换与 dtype 转换，且文档 dtype 表存在 src.dtype != dst.dtype 的行；
3. 非格式转换类算子（如 MatMul、Conv 等，dtype 一致性由其它规则承载）。

##### C. 必须产出的 `constraints_in_parameters` 条目

满足适用判定时，必须在 `constraints_in_parameters[每个支持平台]` 中追加**一条**
`type_equality` 约束：

```text
expr_type: type_equality
expr: srcTensor.dtype == dstTensor.dtype
relation_params: ["srcTensor", "dstTensor"]
src_text: "<摘录文档 dtype 组合表或功能说明中 src dtype == dst dtype 的原文>；
           格式转换算子数据值不变，src 与 dst dtype 必须一致"
```

**规则要点**：

1. **dstTensor 值域沿用 src**：dstTensor 的取值范围（若生成器按 dtype 推导值域）
   必须与 srcTensor 一致；不得按不同 dtype 的负值域生成（如 src=uint8 而 dst 按
   int8 负值域 [-255,-1] 生成）。
2. **逐平台落库**：与其它约束一致，`product_support` 中每个平台都必须有对应条目，
   即使各平台 expr 完全相同。
3. **src_text 可溯源**：必须摘录文档中表明 src dtype == dst dtype 的原文（dtype 表
   行、功能说明"数据值不变"或示例代码用 srcDtype 构造 dstTensor 的行）。
4. **不替代互推导规则**：若算子同时引用 `互推导关系.md`，互推导约束（§4.6.10 A）
   仍须落库；本规则仅补充“格式转换场景下 src == dst”的等式约束。

#### 4.6.13 srcTensor.format → dstFormat 条件约束（v8 新增，仅 aclnnNpuFormatCast）

> 本节来自 `aclnnNpuFormatCast` 闭环：iter_001 把 `dstFormat` 当作**完全独立**
> 输入参数提取 `allowed_range_value=[2,29,30,32,33]`（A2/A3）/ `[29]`（350），
> 未落库任何 `srcTensor.format → dstFormat` 跨参约束。生成器把两者当独立字段
> 采样，产出 `src=ND, dstFormat=NCDHW(30)` 等算子不支持的非法组合（文档「功能
> 说明」明确 ND 只能转 FRACTAL_NZ，NCDHW 只能转 NDC1HWC0/FRACTAL_Z_3D）。
> `dstFormat` 是 `int` 型标量，故 dst 侧取值用 `acl_format_enum.md` §A 的整数。
> 该规则按「格式转换算子的 src.format↔dst(int) 转换语义」触发，**不**按算子名
> 硬编码 dstFormat 取值（算子名仅用于锁定本节适用对象）。

##### A. 适用判定

满足下列**全部**条件时，**必须**执行本节规则：

1. 算子为 `aclnnNpuFormatCast`（函数原型含 `aclnnNpuFormatCastCalculateSizeAndFormat`
   与两段式 `aclnnNpuFormatCastGetWorkspaceSize`）；
2. 存在 `int` 型标量参数 `dstFormat`，其 `allowed_range_value.type="enum"`、
   `value` 为 `ACL_FORMAT_*` 整数集（如 `[2,29,30,32,33]`）；
3. `srcTensor.format.value` 为多格式列表（如
   `["ND","NZ","NCDHW","NDC1HWC0","FRACTAL_Z_3D","NCL"]`）；
4. 文档「功能说明」给出 src→dst 转换语义（如“完成 ND←→NZ 的转换功能”“完成
   NCDHW←→NDC1HWC0、NCDHW←→FRACTAL_Z_3D 的转换功能”“完成 ND 到 FRACTAL_NZ 的
   转换”）。

##### B. 必须产出的 `value_dependency` 约束

满足适用判定时，**必须**在 `constraints_in_parameters[每个支持平台]` 中追加
**一条** `value_dependency` 约束，把「`srcTensor.format`(str) → `dstFormat`(int)
合法转换对」编码为 OR-of-ANDs 析取（每行一个 src 格式合取其允许的 dstFormat
整数集）。`aclnnNpuFormatCast` 的权威 src→dst 映射如下（dst 侧整数查
`acl_format_enum.md` §A）：

| 源格式（srcTensor.format） | → | 目标格式（dstFormat，int） |
|---|:---:|---|
| `NZ` | → | `2`（ND） |
| `NCDHW` | → | `32`（NDC1HWC0）或 `33`（FRACTAL_Z_3D） |
| `NDC1HWC0` | → | `30`（NCDHW） |
| `FRACTAL_Z_3D` | → | `30`（NCDHW） |
| `ND` 或 `NCL` | → | `29`（FRACTAL_NZ） |

**规则要点**：

1. **逐平台落库同一条 expr**：各平台 `dstFormat.allowed_range_value` 已按 §4.6.11
   分别收窄（350=`[29]`、A2/A3=`[2,29,30,32,33]`），本 expr 与之**联合**求解时
   自然只留下该平台可满足的行（350 仅 `==29` 行可满足 → src 必须是 `ND`/`NCL`；
   A2/A3 全行可满足），无需逐平台改写 expr；
2. **dst 侧必须用整数**：`dstFormat` 是 `int` 型，expr 里写 `dstFormat.range_value == 29`
   等裸整数，**禁止**写 `dstFormat.range_value == "29"` 字符串（违 §9.30 f）；
3. **src 侧用 §5.3 短名字符串，且与 `srcTensor.format.value` 字面一致**：
   `srcTensor.format == "NZ"`；**不**为 `NZ`/`FRACTAL_NZ` 同义别名做外扩——
   文档参数表写 `NZ` 就用 `"NZ"`、写 `FRACTAL_NZ` 就用 `"FRACTAL_NZ"`，expr 的
   `==`/`in` 集合与 `format.value` 严格同源（见 `acl_format_enum.md` §B）；
4. **src 必须可溯**：`src_text` 摘录文档「功能说明」的转换短语与 dstFormat 取值
   原文（`ACL_FORMAT_ND(2)/FRACTAL_NZ(29)/...`）；
5. **与本模块其它约束并行不冲突**：本条约束 `src.format → dstFormat`，§4.6.8 C
   的 `derived_value` 约束 `dstFormat → dstTensor.format`，§4.6.7 的
   `format_rank_consistency` 约束 `format ↔ rank`，§4.6.12 的 `type_equality`
   约束 `src.dtype == dst.dtype`；四者职责不同，形成
   `src.format → dstFormat → dstTensor.format` 的一致链，**并行落库**即可，
   不得因本条而省略其余。

##### C. 落库示例（aclnnNpuFormatCast，逐平台同一条 expr）

```text
expr_type: value_dependency
expr: (srcTensor.format == "NZ" and dstFormat.range_value == 2)
   or (srcTensor.format == "NCDHW" and (dstFormat.range_value == 32 or dstFormat.range_value == 33))
   or (srcTensor.format == "NDC1HWC0" and dstFormat.range_value == 30)
   or (srcTensor.format == "FRACTAL_Z_3D" and dstFormat.range_value == 30)
   or ((srcTensor.format == "ND" or srcTensor.format == "NCL") and dstFormat.range_value == 29)
relation_params: ["srcTensor", "dstFormat"]
src_text: "功能说明：完成 ND←→NZ、NCDHW←→NDC1HWC0、NCDHW←→FRACTAL_Z_3D 的转换；
           Atlas 350：完成 ND 到 FRACTAL_NZ 的转换。dstFormat 取值：
           ACL_FORMAT_ND(2)/FRACTAL_NZ(29)/NCDHW(30)/NDC1HWC0(32)/FRACTAL_Z_3D(33)。"
```

> 求解示例：src=`NZ` → 仅第一行可满足 → `dstFormat==2`；src=`NCDHW` → 第二行
> → `dstFormat∈{32,33}`；src=`ND` → 末行 → `dstFormat==29`。Atlas 350 因
> `dstFormat.allowed_range_value=[29]`，仅末行可满足，故 src 只能是 `ND`/`NCL`。

##### D. 反例（禁止）

- 只写 `dstFormat.allowed_range_value=[2,29,30,32,33]` 而**不**落库本
  `value_dependency` → 生成器独立采样出 `src=ND, dstFormat=30(NCDHW)` 等非法
  组合，违规则 B.1（正是 iter_001 现状）；
- expr 里写 `dstFormat.range_value == "29"` 字符串 → 与 `dstFormat` 的 `int`
  类型不符，Z3 IntSort 与 str 无法比较，违 §9.30 f 与规则 B.2；
- NZ 行的 expr 引用了 `format.value` 里没有的短名（如 `srcTensor.format.value=["NZ"]`
  但 expr 写 `srcTensor.format in ("NZ","FRACTAL_NZ")`，多出未列出的 `FRACTAL_NZ`
  分支；或反过来 `format.value=["FRACTAL_NZ"]` 但 expr 写 `== "NZ"`）→ expr 与
  `format.value` 字面不一致，生成器采到的值命中不了该分支，违规则 B.3 与
  §4.6.7 C.7；二者必须同源：文档参数表写哪个短名，`format.value` 与 expr 就用哪个；
- 把本条拆成 5 条独立 `value_dependency`（每 src 一条）→ 生成器把并列候选当
  独立分支丢失 src 门控上下文，与 §4.6.7 C.2 / §6.3 模式 6 反例同理；
- 因落库了本条就省略 §4.6.8 C 的 `derived_value`（dstFormat→dstTensor.format）
  → 二者职责不同，违规则 B.5。

##### E. 自检（本模块内，不进 base §9）

- [ ] `aclnnNpuFormatCast`：是否在 `constraints_in_parameters[每个平台]` 落库了
      **一条** `value_dependency` 把 `srcTensor.format → dstFormat` 编码为
      OR-of-ANDs？
- [ ] dst 侧取值是否用了**整数**（`2/29/30/32/33`，查 `acl_format_enum.md` §A），
      而非字符串 `"29"`？
- [ ] NZ 行的 expr 是否与 `srcTensor.format.value` 字面一致（文档写 `NZ` 则
      `== "NZ"`、未外扩 `FRACTAL_NZ`；写 `FRACTAL_NZ` 则 `== "FRACTAL_NZ"`）？
- [ ] `src_text` 是否摘录了文档「功能说明」转换短语与 dstFormat 取值原文？

#### 模式 9：派生值可求解查找表达式（v3 增补）

**适用场景**：派生输出参数 `D`（标记 `[DERIVED]`，见 §4.6.8）的取值由文档中的
确定映射表（正文 combo 表）从子接口入参推导。生成器必须能从 `expr` 读出派生规则，
不得独立随机赋值。**dtype×format 交叉联合组合表（同一行同时含 dtype 列与 format 列、
且 dtype 与 format 存在行内依赖——不同 dtype 对应不同 format 候选、拆开会丢失信息）
必须按本模式落库为 OR-of-ANDs expr，不得拆进 `dtype_support_description`/
`format_support_description`（见 §4.9/§4.10）。纯 dtype 组合表（只有 dtype 列）仍填
`dtype_support_description`，纯 format 组合表（只有 format 列）仍填
`format_support_description`；同表但独立的 dtype+format 表（任意 dtype 配任意 format）
按"单独 dtype 约束 + 单独 format 约束"拆开处理，不属本模式**。

**恒等映射**（`D` 取值恒等于某入参）：

```text
expr_type: derived_value
expr: D.range_value == keyParam.range_value
relation_params: ["D", "keyParam"]
src_text: "<摘录映射表原文，如 dtype_support_description 中 actualFormat == dstFormat 的行>"
```

**查找表映射**（多行 combo 表，析取所有合法行）：

```text
expr_type: derived_value
expr: (srcTensor.dtype == "INT8" and dstFormat.range_value == 29 and additionalDtype.range_value == 2 and actualFormat.range_value == 29)
      or (srcTensor.dtype == "INT32" and dstFormat.range_value == 29 and additionalDtype.range_value == 1 and actualFormat.range_value == 50)
      or (...)
relation_params: ["actualFormat", "srcTensor", "dstFormat", "additionalDtype"]
src_text: "<摘录 dtype_support_description 映射表原文>"
```

**格式派生**（`D.format` 由 actualFormat 查表得出，析取所有合法对应）：

```text
expr_type: derived_value
expr: (actualFormat.range_value == 2 and dstTensor.format == "ND")
      or (actualFormat.range_value == 29 and dstTensor.format == "NZ")
      or (actualFormat.range_value == 30 and dstTensor.format == "NCDHW")
      or (...)
relation_params: ["dstTensor", "actualFormat"]
src_text: "<摘录 actualFormat → format 对应原文>"
```

**主接口联合组合表**（`GetWorkspaceSize` 类主接口的 dtype×format 联合 combo 表；
同一行同时锁定 `srcTensor.dtype`、`dstTensor.dtype`、`dstTensor.format`，行间互斥）：
**不得**把这类联合表拆进 `dtype_support_description` / `format_support_description`
（见 §4.9/§4.10），必须落库为**一条** `derived_value`（或 `cross_param_constraint`）
expr，析取所有合法行、每行合取键值与目标值。以 `aclnnNpuFormatCast` Atlas 350
`GetWorkspaceSize` 表为例（format 用 §5.3 受控字典短名，`FLOAT`→`FLOAT32`）：

```text
expr_type: derived_value
expr: (srcTensor.dtype == "INT8"          and dstTensor.dtype == "INT8"          and dstTensor.format == "NZ")
   or (srcTensor.dtype == "INT32"         and dstTensor.dtype == "INT32"         and dstTensor.format == "FRACTAL_NZ_C0_16")
   or (srcTensor.dtype == "FLOAT32"       and dstTensor.dtype == "FLOAT32"       and (dstTensor.format == "FRACTAL_NZ_C0_16" or dstTensor.format == "FRACTAL_NZ_C0_32"))
   or (srcTensor.dtype == "FLOAT16"       and dstTensor.dtype == "FLOAT16"       and dstTensor.format == "NZ")
   or (srcTensor.dtype == "BFLOAT16"      and dstTensor.dtype == "BFLOAT16"      and dstTensor.format == "NZ")
   or (srcTensor.dtype == "FLOAT8_E4M3FN" and dstTensor.dtype == "FLOAT8_E4M3FN" and dstTensor.format == "NZ")
   or (srcTensor.dtype == "FLOAT4_E2M1"   and dstTensor.dtype == "FLOAT4_E2M1"   and dstTensor.format == "FRACTAL_NZ_C0_32")
relation_params: ["srcTensor", "dstTensor"]
src_text: "<摘录 GetWorkspaceSize 接口 srcTensor/dstTensor数据类型/dstTensor数据格式 组合表原文>"
```

该 expr 同时编码「`srcTensor.dtype == dstTensor.dtype`」与「dtype→`dstTensor.format`
映射」;`dstTensor` 标记 `[DERIVED]`（§4.6.8），此 expr 即其派生规则，生成器不得
独立随机赋 `dstTensor.format`。§4.6.12 的 `type_equality`（`srcTensor.dtype ==
dstTensor.dtype`）与本条并行落库不冲突（本条更严，生成器两条都满足即可）。A3/A2
平台的联合表同理逐平台落库一条，`dtype_support_description` /
`format_support_description` 对该算子留 `{}`。

**规则要点**：

1. **expr 不得为空**：存在确定映射时 `expr` 必须编码该映射为可 `eval()` 的布尔
   表达式；空 `expr` 对生成器无用（违 §4.7.2、§4.6.8 C.1）。
2. **析取须覆盖全部合法行**：映射表的每一行都必须在析取中出现；遗漏一行会使
   该组合下 `D` 取值无约束、生成器随机赋值。
3. **无确定映射时不产出**：若派生依赖 NPU 运行期计算且文档无可枚举对应表，
   不产出 `derived_value` 条目，派生语义由 `[DERIVED]` description 承载（§4.6.8 C.2）。
4. **逐平台落库**：各平台映射表不同时 `expr` 按该平台表分别编码。
5. **format 短名与 `format.value` 对齐（字面保真）**：combo 表里若用 `ACL_FORMAT_FRACTAL_NZ(29)`
   等全名，expr 须归一化为该张量 `format.value` 里实际使用的 §5.3 短名（如 `format.value`
   写 `"NZ"` 则 expr 用 `dstTensor.format == "NZ"`，**不**写 `"FRACTAL_NZ"`），否则生成器
   从 `format.value` 采到的值命中不了 expr 分支；同一张量的 `format.value` 与所有引用它的
   expr 必须用同一短名，不为同义别名做外扩（见 §4.6.7 C.7）。

---

