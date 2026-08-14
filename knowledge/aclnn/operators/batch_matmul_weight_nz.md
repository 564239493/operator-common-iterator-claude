---
module: batch_matmul_weight_nz
scope: operator
description: aclnnBatchMatMulWeightNz 的转置隐式 bool 与 shape_value_dependency 门控
default_load: false
triggers:
  - kind: operator_name_eq
    value: "aclnnBatchMatMulWeightNz"
depends_on: [nz_matmul, expression_language]
---

# aclnnBatchMatMulWeightNz 专项：隐式转置门控（按需加载）

> 原为 `prompts/history/operator_constraints_extract_v4.md` §4.6.5 B.1 + §6.3 模式 6.1，按算子精确
> 命中由 `select_prompt.py` 装配。原 § 编号保留，便于交叉引用按标题文本定位。
> 仅当算子名精确为 `aclnnBatchMatMulWeightNz` 时适用，并以当前版本文档的转置/NZ
> 描述复核。

##### B.1 `aclnnBatchMatMulWeightNz` 转置隐式变量（算子特例，强制）

当且仅当 `operator_name == "aclnnBatchMatMulWeightNz"` 时，即使函数签名和参数表中
没有转置标志，也必须主动向 `inputs` 新增以下两个**隐式控制变量**：

- `self_transposed`：标识 `self` 是否按转置布局解释；
- `mat2_transposed`：标识 `mat2` 是否按转置布局解释。

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
4. 当 `self` 或 `mat2` 的 shape、NZ 轴位、K/N 对应关系因是否转置而变化时，对应
   `constraints_in_parameters` 表达式必须引用
   `self_transposed.range_value` 或 `mat2_transposed.range_value` 作为门控条件，
   `relation_params` 同时包含实际张量和对应隐式变量；禁止把转置与非转置布局写成
   两条互不受门控的无条件约束；
5. `src_text` 优先摘录文档中转置/非转置布局的原文；变量名和布尔值是为生成器补充
   的结构化控制信息，不得伪造成函数签名原文。
6. 当 `mat2` 引用 `mat2.shape[j]`、`self` 引用 `self.shape[i]`
   （j ∈ [1, 2, 3]，i ∈ [1, 2]）时，对应的 `shape_value_dependency` **必须**按本节
   隐式 bool 变量分支。触发条件为：`operator_name == "aclnnBatchMatMulWeightNz"`、
   `constraints_in_parameters[平台]` 含 `expr_type == "shape_value_dependency"`，且
   expr 包含 `mat2.shape[j]` 或 `self.shape[i]`。三条同时成立时强制执行；具体模板见
   §D+ 模式 6.1。

##### D+ `shape_value_dependency` 必须按 B.1 隐式 bool 门控分支

#### 模式 6.1：`shape_value_dependency` 弱门控模板（v3 合并 v4 增补）

**适用场景**：

- `mat2_transposed` 隐式 bool 门控下，`mat2.shape[j]` 的轴语义反转；
- `self_transposed` 隐式 bool 门控下，`self.shape[i]` 的轴语义反转；
- 两者的 ceil 关系（`k1 = ceil(k / k0)`、`n1 = ceil(n / n0)`）需要落为
  `shape_value_dependency`。

##### mat2 引用模板

非转置分支（`mat2_transposed=False`）：`mat2.shape == (b, n1, k1, 16, 16)`，
**`shape[2] = k1`**。
转置分支（`mat2_transposed=True`）：`mat2.shape == (b, k1, n1, 16, 16)`，
**`shape[1] = k1`**。

推荐单条 if/else 写法：

```text
expr_type: shape_value_dependency
expr: ((self.shape[2] + 15) // 16 == mat2.shape[2])
        if (mat2_transposed.range_value == False)
      else ((self.shape[2] + 15) // 16 == mat2.shape[1])
        if (mat2_transposed.range_value == True)
      else True
relation_params: ["self", "mat2", "mat2_transposed"]
src_text: "mat2 非转置时 NZ 为 (b, n1, k1, 16, 16)；转置时为 (b, k1, n1, 16, 16)；
           ceil(k, k0) = k1，由 mat2_transposed 门控。"
```

等价写法（`unless` 多分支合并）：

```text
expr_type: shape_value_dependency
expr: not (mat2_transposed.range_value == False)
        or ((self.shape[2] + 15) // 16 == mat2.shape[2])
relation_params: ["self", "mat2", "mat2_transposed"]

expr_type: shape_value_dependency
expr: not (mat2_transposed.range_value == True)
        or ((self.shape[2] + 15) // 16 == mat2.shape[1])
relation_params: ["self", "mat2", "mat2_transposed"]
```

注：多分支等价写法必须拆为多条独立 `InterParamConstraint`；禁止在单条 JSON 表达式中
把 `not(A) or B and not(C) or D` 直接连写，避免 `and` / `or` 优先级歧义。

##### self 引用模板

`self` 隐式 bool 与轴位的对应：

- `self_transposed=False`：`self.shape == (b, m, k)`；`shape[1] = m`，`shape[2] = k`；
- `self_transposed=True`：`self.shape == (b, k, m)`；`shape[1] = k`，`shape[2] = m`。

按 `self_transposed` 门控的 `shape_value_dependency` 应同样使用 if/else 链。

##### 反例（禁止）

```text
((self.shape[2] + 15) // 16 == mat2.shape[2])
# 无条件，在 mat2_transposed=True 时语义错误（UNSAT）
```

```text
((self.shape[2] + 15) // 16 == mat2.shape[2]) if (mat2_transposed.range_value == False) else True
# 缺转置分支，self.shape[2] 在转置布局下等同 m 而非 k
```

##### `expr_type` 与 `src_text` 选择

- `expr_type` 优先 `shape_value_dependency`（与原风格一致）；亦可使用
  `shape_choice` / `parameter_representation`。
- `src_text` 必须**同时摘录两个布局的 NZ 维度元组原文**（"当B矩阵不转置时..."
  与 "当B矩阵转置时..."），不可只摘默认布局。

#### 模式 6.2：Reduce 维度相等门控模板（v4 增补）

**适用场景**：文档出现"mat2 的 Reduce 维度需要与 self 的 Reduce 维度大小相等"等
Reduce 轴相等原文时，**必须**在 ceil 关系（模式 6.1）之外，补出 Reduce 维度整块
相等 `self.shape[k_idx] == mat2.shape[k1_idx] * mat2.shape[k0_idx]`，按
`self_transposed × mat2_transposed` 四象限门控。仅落 ceil 会允许 k 为非 16 倍数
（如 k=769 → k1=ceil(769/16)=49 → k1*16=784≠769），运行时 `GetWorkspaceSize`
返回 161002 "self's last dim and mat2's penultimate dim should be same"。

**NZ 轴位**：
- 非转置 NZ mat2 `(b, n1, k1, k0, n0)`：`k1=shape[2]`、`k0=shape[3]`，reduce = `shape[2]*shape[3]`。
- 转置 NZ mat2 `(b, k1, n1, n0, k0)`：`k1=shape[1]`、`k0=shape[4]`，reduce = `shape[1]*shape[4]`。
- self 非转置 `(b, m, k)`：`k=shape[2]`；self 转置 `(b, k, m)`：`k=shape[1]`。

**unless 多分支写法（4 条独立 implication，对应四象限）**：

```text
# self 非转置 × mat2 非转置
expr_type: shape_value_dependency
expr: not(self_transposed.range_value == False and mat2_transposed.range_value == False) or (self.shape[2] == mat2.shape[2] * mat2.shape[3])
relation_params: ["self", "mat2", "self_transposed", "mat2_transposed"]
src_text: "mat2的Reduce维度需要与self的Reduce维度大小相等。非转置 NZ (b, n1, k1, k0, n0)，k1=shape[2]、k0=shape[3]，self 非转置 k=shape[2]。"

# self 转置 × mat2 非转置
expr_type: shape_value_dependency
expr: not(self_transposed.range_value == True and mat2_transposed.range_value == False) or (self.shape[1] == mat2.shape[2] * mat2.shape[3])
relation_params: ["self", "mat2", "self_transposed", "mat2_transposed"]
src_text: "mat2的Reduce维度需要与self的Reduce维度大小相等。非转置 NZ (b, n1, k1, k0, n0)，self 转置 k=shape[1]。"

# self 非转置 × mat2 转置
expr_type: shape_value_dependency
expr: not(self_transposed.range_value == False and mat2_transposed.range_value == True) or (self.shape[2] == mat2.shape[1] * mat2.shape[4])
relation_params: ["self", "mat2", "self_transposed", "mat2_transposed"]
src_text: "mat2的Reduce维度需要与self的Reduce维度大小相等。转置 NZ (b, k1, n1, n0, k0)，k1=shape[1]、k0=shape[4]，self 非转置 k=shape[2]。"

# self 转置 × mat2 转置
expr_type: shape_value_dependency
expr: not(self_transposed.range_value == True and mat2_transposed.range_value == True) or (self.shape[1] == mat2.shape[1] * mat2.shape[4])
relation_params: ["self", "mat2", "self_transposed", "mat2_transposed"]
src_text: "mat2的Reduce维度需要与self的Reduce维度大小相等。转置 NZ (b, k1, n1, n0, k0)，self 转置 k=shape[1]。"
```

> provenance：源自 run `aclnnBatchMatMulWeightNz-20260810-101830-574372` iter_001
> 失败反推（96/100 失败 161002 → 补后 iter_002 进入），`origin=diagnose_inferred`，
> 经 iter_003 SUCCESS 验证（promotion_gate=on_success）。

**反例（禁止）**：

```text
# 只写 ceil，无 reduce 相等 → 允许 k 非 16 倍数，运行时 161002
((self.shape[2] + 15) // 16 == mat2.shape[2])   # 仅有此条，缺 self.shape[2]==mat2.shape[2]*mat2.shape[3]
```

#### 模式 6.3：out n 维度整块相等门控模板（v4 增补）

**适用场景**：文档 out 使用说明写"n 与 mat2 的 n1 以及 n0 满足 ceil(n / n0) = n1 的关系"
时，**必须**在 ceil 关系之外，补出 out n 维度整块相等
`out.shape[2] == mat2.shape[n1_idx] * 16`（即 out_n == n1*n0），按 `mat2_transposed` 门控。
仅落 ceil 会允许 out_n 为非 16 倍数（如 out_n=4 → ceil(4/16)=1=n1 满足，但 4≠1*16=16），
运行时 `GetWorkspaceSize` 返回 161002 "out_n[X] must be same with other_n[Y]"。

> 关键：文档只写 ceil，运行时**强制整块相等**——运行时硬约束、非文档原文直引，
> provenance 见本节末。与模式 6.2 同构（文档写 ceil、运行时要求 dim==count*block）。

**NZ 轴位**：
- 非转置 NZ mat2 `(b, n1, k1, k0, n0)`：`n1=shape[1]`、`n0=shape[4]=16`，逻辑 N = `shape[1]*16`。
- 转置 NZ mat2 `(b, k1, n1, n0, k0)`：`n1=shape[2]`、`n0=shape[3]=16`，逻辑 N = `shape[2]*16`。
- out 为 `(b, m, n)` ND 三维，n 固定 `out.shape[2]`。

**unless 多分支写法（2 条独立 implication，对应 mat2 转置/非转置）**：

```text
# mat2 非转置
expr_type: shape_value_dependency
expr: not(mat2_transposed.range_value == False) or (out.shape[2] == mat2.shape[1] * 16)
relation_params: ["out", "mat2", "mat2_transposed"]
src_text: "out各个维度表示：（b, m，n），n与mat2的n1以及n0满足ceil(n / n0) = n1的关系，其中n0为16。NZ整块语义要求 out 的 n 等于 mat2 的逻辑 N=n1*n0；非转置 NZ (b, n1, k1, k0, n0) 中 n1=shape[1]、n0=shape[4]=16。"

# mat2 转置
expr_type: shape_value_dependency
expr: not(mat2_transposed.range_value == True) or (out.shape[2] == mat2.shape[2] * 16)
relation_params: ["out", "mat2", "mat2_transposed"]
src_text: "out各个维度表示：（b, m，n），n与mat2的n1以及n0满足ceil(n / n0) = n1的关系，其中n0为16。NZ整块语义要求 out 的 n 等于 mat2 的逻辑 N=n1*n0；转置 NZ (b, k1, n1, n0, k0) 中 n1=shape[2]、n0=shape[3]=16。"
```

> provenance：源自 run `aclnnBatchMatMulWeightNz-20260810-101830-574372` iter_002
> 失败反推（68/100 失败 161002 → 补后 iter_003 SUCCESS），`origin=diagnose_inferred`，
> promotion_gate=on_success。文档只写 ceil、未写整块相等，整块语义由运行时失败实证得出。

**与 ceil 关系的关系**：补 out_n==n1*16 后，out_n 必为 16 倍数且 n1=out_n/16=ceil(out_n/16)，
模式 6.1 的 ceil implication 变冗余但无害（保留不冲突，与模式 6.2 风格一致）。

## 规则要点

- 候选顺序按既有闭环约定为 `[true,false]`；不得扩散到其他 NZ/MatMul 算子。
- Reduce 轴相等按两个转置变量四象限分支（模板见模式 6.2）；out n 整块相等按 mat2 转置分支（模板见模式 6.3）；`relation_params` 包含 tensor 与对应 bool。
- 无条件 shape 关系、漏 bool 的轴引用、或把 stride 转置与 shape 元组重排混为一谈，
  均视为不完整提取。
