---
module: transpose_shape
description: 转置参数(语义B:shape元组重排)在张量卡声明 transpose_id(perm候选集,permute(S,perm)==逻辑视图L),约束表达式统一按L无分支书写,<param>_transposed隐式bool仅做场景门控与逐用例形态选择,生成器把cases层shape还原为物理S,executor以permute还原L
triggers:
  - kind: doc_contains
    value: "转置"
depends_on: []
---

# 模块 transpose_shape（按需加载）

> 本模块覆盖**语义 B** 转置：文档对同一张量参数给出 shape 元组的**两种顺序**
> （如 x `(M,K)`/`(K,M)`、weight `(N,K)`/`(K,N)` 或 `(g,N,K)`/`(g,K,N)`）。
> 处理方式：在该张量的参数卡上声明 `transpose_id`（perm 候选集），**所有约束表达式
> 统一按逻辑视图 L 书写（无分支）**；`<param>_transposed` 隐式 bool 仅用于场景
> `value_dependency` 门控与逐用例形态选择。生成器把转置用例的 cases 层 shape
> 还原为转置前物理 shape S，executor 以 `permute(*transpose_id)` 从 S 还原 L。

> **与 §4.6.3 I 的关系**：§I 定义语义 A/B 判别。语义 B（shape 元组重排）按本模块
> 处理：卡级 `transpose_id` + 约束按 L 无分支书写。语义 A（stride 编码、shape
> 元组不变）**不使用** `transpose_id`（恒等 perm 无意义），仍走 executor stride
> 物化通道。两者不混用：本模块的 `transpose_id` 只描述 shape 元组轴顺序，**不**
> 表达 stride、**不**改 shape 元组维数。

#### 核心语义（L / S / perm）

- **逻辑视图 L**：约束表达式中 `<param>.shape` 指代的形状 = 该张量**非转置模式的
  规范布局**。两种形态（转置/非转置）在约束层共用同一个 L，因此轴等式、ceil 关系
  只需写一套、无 if/else。
- **物理 shape S**：cases.json 该条目落盘的 `shape`（转置用例 = 转置前物理布局）。
- **transpose_id（正向 perm）**：满足 `permute(S, transpose_id) == L`，即
  `L[i] == S[transpose_id[i]]`。执行侧对物理张量 `permute(*transpose_id)` 还原 L。
- 还原数学：生成器按 `S[j] = L[inv[j]]`（`inv` 为 `transpose_id` 的逆置换）计算 S。

#### 适用判定

对张量参数 `<param>`，满足下列**全部**条件时执行本模块规则：

1. 文档对 `<param>` 给出 shape 元组的**两种顺序**（如 `(M,K)` 与 `(K,M)`、
   `(g,N,K)` 与 `(g,K,N)`），或文档明示该参数"支持转置/必须转置/不支持转置"且
   转置态与默认态的 shape 元组顺序不同；
2. 函数签名**无**真实 transpose bool 参数（如 `transposeX1`/`transposeX2`）；
   若有，转置由真实参数表达（走 §4.6.3 G 模式 6 条件 shape），**不**引入隐式 bool
   与 `transpose_id`（避免重复门控）；
3. 该参数转置前后**维数不变**（仅轴顺序重排）。维数变化的（如 2D↔3D）不属本模块，
   按 §4.6.3 G 条件 shape 处理。

#### 张量卡 `transpose_id` 声明（新增）

在 `<param>` 的 inputs `ParamAttributes` 上声明（仅 inputs；outputs 无物理转置语义，
声明即错误）。`value` 为 **full-form perm 候选集** `List[List[int]]`——rank 多态张量
每个 rank 一个候选（还原时按解出 shape 的 rank 唯一匹配）：

```json
"transpose_id": {
  "value": [[1, 0]],
  "src_text": "weight 不转置 shape=(K,N)；转置 shape=(N,K)。permute(物理S,perm)==逻辑视图L=(K,N)",
  "type": null
}
```

| 场景 | 候选集示例 | 说明 |
| --- | --- | --- |
| 2D `(K,N)`/`(N,K)` | `[[1,0]]` | 转置物理 (N,K) → L=(K,N) |
| 3D `(g,N,K)`/`(g,K,N)` | `[[0,2,1]]` | 转置物理 (g,K,N) → L=(g,N,K) |
| NZ 5D `(b,n1,k1,k0,n0)`/`(b,k1,n1,n0,k0)` | `[[0,2,1,4,3]]` | 转置物理 → L=非转置规范布局 |
| rank 多态（2D/3D 均可转） | `[[1,0],[0,2,1]]` | 每 rank 恰一个候选 |

**候选集为空（`[]`，缺省）= 无转置语义**。`src_text` 必须同时摘录转置与非转置两种
shape 元组原文。

#### 隐式 bool 卡片（保留，职责收窄）

`<param>_transposed` 隐式 bool **保留**，但**只**承担两个职责：① 场景
`value_dependency` 门控（把"是否转置"绑到 dtype 组合/groupType 等判别条件）；
② 逐用例形态选择变量（生成器按解出的 bool 决定该用例是否还原物理 shape）。
**不得**再用于 `shape_value_dependency` if/else 换轴位（已废止，见反例）。
字段规范不变（复用 `knowledge/aclnn/operators/batch_matmul_weight_nz.md` §B.1），
**不得**写入 `function_signature`：

```json
{
  "description": "隐式变量，标识 <param> 是否按转置 shape 元组顺序输入",
  "type": {"value": "bool", "src_text": ""},
  "format": {"value": "N/A", "src_text": ""},
  "is_optional": {"value": false, "src_text": ""},
  "is_support_discontinuous": {"value": "N/A", "src_text": ""},
  "is_operator_param": {"value": false, "src_text": ""},
  "array_length": {"value": [], "src_text": "", "type": null},
  "dtype": {"value": ["bool"], "src_text": ""},
  "dimensions": {"value": [], "src_text": ""},
  "allowed_range_value": {
    "value": [false, true],
    "src_text": "由 <param> 转置/非转置 shape 元组抽象出的隐式控制变量",
    "type": "enum"
  }
}
```

`allowed_range_value.value` **按场景**（逐场景读文档"约束说明"）：

| 文档场景描述 | `value` | 说明 |
| --- | --- | --- |
| "不支持转置"（如 GroupedMatmulV5 A8W8 x 不支持转置） | `[false]` | 锁死不转置 |
| "必须转置"（如 GroupedMatmulV5 groupType=2 x 必须转置） | `[true]` | 锁死转置 |
| "支持转置"（可转可不转） | `[false, true]` | 两种形态逐用例都生成 |

#### constraints_in_parameters（语义 B 核心：按 L 无分支单条）

**所有**引用转置张量 shape 的约束表达式**统一按 L 书写**，同一轴等式/ceil 关系
**只写一条、无门控**。bool **不得**出现在 shape 表达式里。

##### 模板（以 x `(M,K)`/`(K,M)`、K 为收缩轴、与 weight 的 K 轴相等为例）

```text
# x 卡 transpose_id=[[1,0]]：不转置 S=L=(M,K)；转置 S=(K,M)，permute(S,[1,0])=L=(M,K)。
# 两种形态下 L 的 shape[1] 都是 K，轴等式一条即可，无 if/else。
expr_type: shape_equality
expr: x.shape[1] == weight.shape[<weight_K_axis>]
relation_params: ["x", "weight"]
src_text: "x 不转置 shape=(M,K)，转置 shape=(K,M)；统一按逻辑视图 L=(M,K) 表达，
           K 在 L 的 shape[1]，与 weight 的 K 轴相等（收缩轴）。"
```

多场景（不同 groupType/splitItem）下若轴关系本身相同，同样只此一条；场景差异只
体现在 `value_dependency`（见下）。NZ 块尺寸类约束同理按 L 单条化，如 mat2
`((mat2.shape[2] + 15) // 16 == ...)` 直接引用 L 的轴位，不再按转置 bool 分支
（正例见 `knowledge/aclnn/operators/batch_matmul_weight_nz.md` §D+ 模式 6.1）。

##### 场景门控（value_dependency，bool 的唯一约束用途）

逐场景落 `value_dependency`，把"是否转置"绑定到场景判别条件：

```text
# 场景1：A8W8（x.dtype==INT8 and weight.dtype==INT8）→ x 不转置
expr_type: value_dependency
expr: not(x.dtype == "INT8" and weight.dtype == "INT8") or (x_transposed.range_value == False)
relation_params: ["x", "weight", "x_transposed"]
src_text: "A8W8 场景 x 不支持转置"
```

```text
# 场景2：转置由另一参数派生时（如 groupType==2 → x 必须转置）
expr_type: value_dependency
expr: not(groupType.range_value == 2) or (x_transposed.range_value == True)
relation_params: ["groupType", "x_transposed"]
src_text: "groupType=2（k 轴分组）时 x 必须转置，物理 shape=(K,M)"
```

```text
# 场景3：groupType∈{-1,0} → x 不转置
expr_type: value_dependency
expr: not(groupType.range_value in [-1, 0]) or (x_transposed.range_value == False)
relation_params: ["groupType", "x_transposed"]
src_text: "groupType=-1/0 时 x 不转置，物理 shape=(M,K)"
```

场景 2 + 3 联立后，`x_transposed` 取值由 `groupType` 唯一决定。

#### 生成器还原语义（cases 层）

生成器 Z3 求解全程在 L 上进行；最终写盘按每个用例解出的 `<param>_transposed`：

| bool 解出 | cases.json 条目 | 执行侧 |
| --- | --- | --- |
| False | `shape=L`，`is_transpose=false`，`transpose_id=null` | 直接使用 |
| True | `shape=S`（物理），`is_transpose=true`，`transpose_id=按 rank 匹配的正向 perm` | `permute(*transpose_id)` 还原 L |

TensorList（`tensors`）每个子张量同 perm 处理；`length` 不变。cases 层 bool 与
shape 形态一一对应（True ⇔ 存的是物理 S）。

#### 与 §4.7.3 item 15（groupType 场景轴等式）的协调

- 转置相关轴等式（K/M 轴对齐、ceil 关系）按 L **单条无门控**落一次，不再由
  groupType 或 bool 分支重复表达；
- item 15 的 unless 范式只保留**与转置无关**的场景层约束（groupType/splitItem/
  单-多 tensor 合法组合），不得与 L 单条等式重复表达同一轴关系；
- 场景→转置态的派生仍由本模块 `value_dependency` 承担（groupType→bool）。

#### 反例（禁止）

```text
# ❌ shape_value_dependency if/else 按 bool 换轴位（已废止）
expr_type: shape_value_dependency
expr: (x.shape[1] == weight.shape[2]) if (x_transposed.range_value == False)
      else (x.shape[0] == weight.shape[2]) if (x_transposed.range_value == True)
      else True
# 约束统一按 L 书写后无需分支；混合 L 语义与换轴门控会导致还原错位

# ❌ 按物理转置布局写无条件轴等式（把 S 当 L 用）
x.shape[0] == weight.shape[2]   # 转置态物理 (K,M) 的 K 在 shape[0]
# 必须写 L 的轴位（x.shape[1]）；物理布局差异由 transpose_id 承载

# ❌ bool 当孤立场景标志，张量卡未声明 transpose_id
# bool 与 shape/perm 无绑定，生成器无法还原物理 shape

# ❌ 把 bool 写进 function_signature / 把 transpose_id 放到 outputs 卡
# <param>_transposed 是隐式控制变量，不是 API 真实入参；
# outputs 由约束直接描述形状，无执行侧 permute 通道

# ❌ transpose_id 候选集里同一 rank 出现多个 perm / perm 不是 0..n-1 排列
# 还原时无法唯一匹配，validate_artifacts 直接报错
```

#### src_text 要求

`transpose_id` 与 `<param>_transposed` 的 `src_text` **必须同时摘录**转置与非转置
两种 shape 元组原文（如"x 不转置 shape=(M,K)"与"转置 shape=(K,M)"），不可只摘
默认布局；变量名、perm 与布尔值是生成器补充的结构化控制信息，不得伪造成函数签名
原文（与 `knowledge/aclnn/operators/batch_matmul_weight_nz.md` §B.1 规则 5 一致）。
