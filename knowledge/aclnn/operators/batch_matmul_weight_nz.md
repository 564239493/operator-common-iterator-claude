---
module: batch_matmul_weight_nz
scope: operator
description: aclnnBatchMatMulWeightNz 的 transpose_id 逻辑视图约束与转置隐式 bool 场景门控
default_load: false
triggers:
  - kind: operator_name_eq
    value: "aclnnBatchMatMulWeightNz"
depends_on: [nz_matmul, expression_language]
---

# aclnnBatchMatMulWeightNz 专项：转置逻辑视图（按需加载）

> 原为 `prompts/history/operator_constraints_extract_v4.md` §4.6.5 B.1 + §6.3 模式 6.1，按算子精确
> 命中由 `select_prompt.py` 装配。原 § 编号保留，便于交叉引用按标题文本定位。
> 仅当算子名精确为 `aclnnBatchMatMulWeightNz` 时适用，并以当前版本文档的转置/NZ
> 描述复核。

##### B.1 `aclnnBatchMatMulWeightNz` 转置声明与隐式变量（算子特例，强制）

当且仅当 `operator_name == "aclnnBatchMatMulWeightNz"` 时，即使函数签名和参数表中
没有转置标志，也必须主动向 `inputs` 新增以下两个**隐式控制变量**：

- `self_transposed`：标识 `self` 是否按转置布局输入；
- `mat2_transposed`：标识 `mat2` 是否按转置布局输入。

两个变量都不是 API 的真实入参，**不得**写入 `function_signature`，但必须为
`product_support` 中的每个平台分别生成完整 `ParamAttributes` 卡片。字段要求如下：

```json
{
  "description": "隐式变量，标识 self 是否需要转置",
  "type": {"value": "bool", "src_text": ""},
  "format": {"value": "N/A", "src_text": ""},
  "is_optional": {"value": false, "src_text": ""},
  "is_support_discontinuous": {"value": "N/A", "src_text": ""},
  "is_operator_param": {"value": false, "src_text": ""},
  "array_length": {"value": [], "src_text": "", "type": null},
  "dtype": {"value": ["bool"], "src_text": ""},
  "dimensions": {"value": [], "src_text": ""},
  "allowed_range_value": {
    "value": [true, false],
    "src_text": "由 self 的转置与非转置布局描述抽象出的隐式控制变量",
    "type": "enum"
  }
}
```

`mat2_transposed` 使用相同字段结构，仅将 `description` 和
`allowed_range_value.src_text` 中的 `self` 替换为 `mat2`。以下规则均为强制：

1. 名称必须精确为 `self_transposed`、`mat2_transposed`，不得改成
   `transposeSelf`、`transposeMat2` 或其他别名；
2. `type.value="bool"`、`dtype.value=["bool"]`、
   `allowed_range_value.type="enum"`，且
   `allowed_range_value.value=[true, false]`；不得反转顺序、不得写成字符串；
3. `is_operator_param.value=false`，因为二者是生成器求解使用的隐式变量，不是函数
   签名参数；
4. **同步声明 `transpose_id`**：`self` 卡 `"transpose_id": {"value": [[0,2,1]], ...}`、
   `mat2` 卡 `"transpose_id": {"value": [[0,2,1,4,3]], ...}`（full-form perm 候选集，
   `permute(物理S, perm) == 逻辑视图 L`，L 为非转置规范布局）。`src_text` 同时摘录
   转置/非转置两种布局原文；
5. `src_text` 优先摘录文档中转置/非转置布局的原文；变量名、perm 和布尔值是为生成器
   补充的结构化控制信息，不得伪造成函数签名原文；
6. **约束按逻辑视图 L 单条书写，不得按 bool 分支**：`self` 的 L=(b,m,k)、`mat2` 的
   L=(b,n1,k1,k0,n0)。所有轴等式/ceil/整块关系在 L 上只有一套索引，**禁止**以
   `self_transposed.range_value` / `mat2_transposed.range_value` 为条件写
   `shape_value_dependency` if/else 换轴位（旧写法已废止，见模式 6.1 反例）；
   bool 只用于场景 `value_dependency`（把转置态绑到场景判别条件）与逐用例形态选择。
   触发核对条件：`operator_name == "aclnnBatchMatMulWeightNz"` 且
   `constraints_in_parameters[平台]` 的 expr 引用 `mat2.shape[j]` / `self.shape[i]`
   （j ∈ [1,2,3]，i ∈ [1,2]）——此时必须能在 L 轴位上唯一定位，且同卡必有
   `transpose_id`。

##### L / 物理 S 对照（cases 层形态）

| 张量 | 逻辑视图 L（约束层唯一形状） | 转置态物理 S（cases 落盘 shape） | transpose_id |
| --- | --- | --- | --- |
| self | `(b, m, k)` | `(b, k, m)` | `[[0,2,1]]` |
| mat2 | `(b, n1, k1, k0, n0)`，`k0=n0=16` | `(b, k1, n1, n0, k0)` | `[[0,2,1,4,3]]` |

生成器按 `<param>_transposed` 解出值：True → cases 存 S + `is_transpose=true` +
`transpose_id`；False → cases 存 L + `is_transpose=false`。executor 以
`permute(*transpose_id)` 从 S 还原 L 后调用。NZ 物理布局识别（S 的判读）见
`knowledge/aclnn/features/nz_matmul.md`。

##### D+ 转置关系按逻辑视图 L 单条落库

#### 模式 6.1：ceil 关系单条模板（L 无分支）

**适用场景**：`k1 = ceil(k / k0)`、`n1 = ceil(n / n0)` 等 ceil 关系需要落为
`shape_value_dependency`。

L 轴位：`self.shape[2] = k`；`mat2.shape[2] = k1`、`mat2.shape[3] = k0`。
单条写法（两种转置形态共用，无门控）：

```text
expr_type: shape_value_dependency
expr: (self.shape[2] + 15) // 16 == mat2.shape[2]
relation_params: ["self", "mat2"]
src_text: "mat2 非转置时 NZ 为 (b, n1, k1, 16, 16)；转置时为 (b, k1, n1, 16, 16)；
           统一按逻辑视图 L=(b, n1, k1, k0, n0) 表达，ceil(k, k0) = k1 即
           self.shape[2] 与 mat2.shape[2] 的 ceil 关系；mat2 卡 transpose_id=[[0,2,1,4,3]]。"
```

配套 NZ 块尺寸约束（同为 L 单套，共 2 条 `shape_equality`）：

```text
expr_type: shape_equality
expr: mat2.shape[3] == 16
relation_params: ["mat2"]
src_text: "NZ (b, n1, k1, k0, n0) 中 k0 = 16（L 轴位 shape[3]；转置布局由 transpose_id 承载）"

expr_type: shape_equality
expr: mat2.shape[4] == 16
relation_params: ["mat2"]
src_text: "NZ (b, n1, k1, k0, n0) 中 n0 = 16（L 轴位 shape[4]；转置布局由 transpose_id 承载）"
```

##### 反例（禁止）

```text
# ❌ 旧写法：按 mat2_transposed 门控换轴位（已废止，L 语义下还原错位）
expr: ((self.shape[2] + 15) // 16 == mat2.shape[2])
        if (mat2_transposed.range_value == False)
      else ((self.shape[2] + 15) // 16 == mat2.shape[1])
        if (mat2_transposed.range_value == True)
      else True

# ❌ 按转置物理布局写轴位（把 S 当 L 用）
expr: (self.shape[2] + 15) // 16 == mat2.shape[1]   # (b,k1,n1,n0,k0) 的 k1 在 shape[1]
# 约束必须写 L 的轴位（mat2.shape[2]）；物理布局差异由 transpose_id 承载
```

##### `expr_type` 与 `src_text` 选择

- `expr_type` 优先 `shape_value_dependency`（与原风格一致）；亦可使用
  `shape_choice` / `parameter_representation`。
- `src_text` 必须**同时摘录两个布局的 NZ 维度元组原文**（"当B矩阵不转置时..."
  与 "当B矩阵转置时..."），不可只摘默认布局。

#### 模式 6.2：Reduce 维度整块相等（L 单条）

**适用场景**：文档出现"mat2 的 Reduce 维度需要与 self 的 Reduce 维度大小相等"等
Reduce 轴相等原文时，**必须**在 ceil 关系（模式 6.1）之外，补出 Reduce 维度整块
相等。仅落 ceil 会允许 k 为非 16 倍数（如 k=769 → k1=ceil(769/16)=49 →
k1*16=784≠769），运行时 `GetWorkspaceSize` 返回 161002 "self's last dim and mat2's
penultimate dim should be same"。

L 轴位：reduce 整块 `k == k1*k0 = mat2.shape[2] * mat2.shape[3]`，self 侧
`k = self.shape[2]`。单条写法（原四象限 if/else 按 L 统一后合并）：

```text
expr_type: shape_value_dependency
expr: self.shape[2] == mat2.shape[2] * mat2.shape[3]
relation_params: ["self", "mat2"]
src_text: "mat2的Reduce维度需要与self的Reduce维度大小相等。统一按逻辑视图 L：
           self L=(b,m,k) 的 k=shape[2]；mat2 L=(b,n1,k1,k0,n0) 的 k1=shape[2]、
           k0=shape[3]。转置态由 transpose_id 承载（self [[0,2,1]]、mat2 [[0,2,1,4,3]]）。"
```

> provenance：源自 run `aclnnBatchMatMulWeightNz-20260810-101830-574372` iter_001
> 失败反推（96/100 失败 161002 → 补后 iter_002 进入），`origin=diagnose_inferred`，
> 经 iter_003 SUCCESS 验证（promotion_gate=on_success）。原四象限 unless 门控在
> transpose_id 逻辑视图约定下数学等价合并为本单条。

**反例（禁止）**：

```text
# 只写 ceil，无 reduce 相等 → 允许 k 非 16 倍数，运行时 161002
((self.shape[2] + 15) // 16 == mat2.shape[2])   # 仅有此条，缺 self.shape[2]==mat2.shape[2]*mat2.shape[3]
```

#### 模式 6.3：out n 维度整块相等（L 单条）

**适用场景**：文档 out 使用说明写"n 与 mat2 的 n1 以及 n0 满足 ceil(n / n0) = n1 的关系"
时，**必须**在 ceil 关系之外，补出 out n 维度整块相等（即 out_n == n1*n0）。
仅落 ceil 会允许 out_n 为非 16 倍数（如 out_n=4 → ceil(4/16)=1=n1 满足，但
4≠1*16=16），运行时 `GetWorkspaceSize` 返回 161002 "out_n[X] must be same with
other_n[Y]"。

> 关键：文档只写 ceil，运行时**强制整块相等**——运行时硬约束、非文档原文直引，
> provenance 见本节末。与模式 6.2 同构（文档写 ceil、运行时要求 dim==count*block）。

L 轴位：mat2 的逻辑 N = `n1*n0 = mat2.shape[1] * 16`；out 为 `(b, m, n)` ND 三维，
n 固定 `out.shape[2]`。单条写法（原 mat2 转置/非转置两分支按 L 统一后合并）：

```text
expr_type: shape_value_dependency
expr: out.shape[2] == mat2.shape[1] * 16
relation_params: ["out", "mat2"]
src_text: "out各个维度表示：（b, m，n），n与mat2的n1以及n0满足ceil(n / n0) = n1的关系，其中n0为16。NZ整块语义要求 out 的 n 等于 mat2 的逻辑 N=n1*n0；统一按逻辑视图 L=(b,n1,k1,k0,n0)，n1=shape[1]、n0=shape[4]=16。mat2 转置态由 transpose_id=[[0,2,1,4,3]] 承载。"
```

> provenance：源自 run `aclnnBatchMatMulWeightNz-20260810-101830-574372` iter_002
> 失败反推（68/100 失败 161002 → 补后 iter_003 SUCCESS），`origin=diagnose_inferred`，
> promotion_gate=on_success。文档只写 ceil、未写整块相等，整块语义由运行时失败实证得出。

**与 ceil 关系的关系**：补 out_n==n1*16 后，out_n 必为 16 倍数且 n1=out_n/16=ceil(out_n/16)，
模式 6.1 的 ceil relation 变冗余但无害（保留不冲突，与模式 6.2 风格一致）。

## 规则要点

- 候选顺序按既有闭环约定为 `[true,false]`；不得扩散到其他 NZ/MatMul 算子。
- 转置张量卡必须带 `transpose_id`；Reduce 轴整块相等（模式 6.2）、out n 整块相等
  （模式 6.3）与块尺寸约束统一按逻辑视图 L **单条落库，无 bool 分支**。
- 按隐式 bool 写轴位 if/else、引用转置物理布局轴位（把 S 当 L 用）、无条件关系漏
  整块相等、或把 stride 转置与 shape 元组重排混为一谈，均视为不完整提取。
