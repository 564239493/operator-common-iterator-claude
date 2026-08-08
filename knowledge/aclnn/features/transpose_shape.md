---
module: transpose_shape
description: 转置参数(语义B:shape元组重排)引入 <param>_transposed 隐式 bool,驱动 shape_value_dependency if/else 门控转置关键轴位置,使 cases 层 bool 与 shape 转置形态自洽对应
triggers:
  - kind: doc_contains
    value: "转置"
depends_on: []
---

# 模块 transpose_shape（按需加载）

> 本模块覆盖**语义 B** 转置:文档对同一张量参数给出 shape 元组的**两种顺序**
> (如 x `(M,K)`/`(K,M)`、weight `(N,K)`/`(K,N)` 或 `(g,N,K)`/`(g,K,N)`)。
> 为每个有转置形态的张量参数 `<param>` 引入隐式 bool `<param>_transposed`，
> 通过 `shape_value_dependency` if/else 门控**转置关键轴（K 或 M）在 shape 中的位置**，
> 使生成器产出 cases 时 bool 取值与 shape 转置形态**在 cases 层直接自洽对应**，
> 无需 executor 侧 stride 改造。

> **与 §4.6.3 I 的关系**：§I 原判定语义 B"不引入隐式 bool"是默认形态；本模块是其
> **显式 bool 变体**——当需要 cases 层 bool 与 shape 对应时，语义 B 亦可引入
> `<param>_transposed` 驱动**轴位置**（非 stride）。区别于语义 A（stride 编码，shape
> 不变，需 executor 侧 stride 透传改造，本模块不处理）。两者不混用：本模块的 bool
> 只驱动 shape 元组轴位置，**不**驱动 stride、**不**改 shape 元组维数。

#### 适用判定

对张量参数 `<param>`，满足下列**全部**条件时执行本模块规则：

1. 文档对 `<param>` 给出 shape 元组的**两种顺序**（如 `(M,K)` 与 `(K,M)`、
   `(g,N,K)` 与 `(g,K,N)`），或文档明示该参数"支持转置/必须转置/不支持转置"且
   转置态与默认态的 shape 元组顺序不同；
2. 函数签名**无**真实 transpose bool 参数（如 `transposeX1`/`transposeX2`）；
   若有，转置由真实参数表达，**不**引入隐式 bool（避免重复门控）；
3. 该参数转置前后**维数不变**（仅轴顺序重排）。维数变化的（如 2D↔3D）不属本模块，
   按 §4.6.3 G 条件 shape 处理。

#### 隐式 bool 卡片（复用 `knowledge/aclnn/operators/batch_matmul_weight_nz.md` §B.1 字段规范）

为 `<param>` 新增 `<param>_transposed`，**不得**写入 `function_signature`，为
`product_support` 每个平台生成完整 `ParamAttributes`：

```json
{
  "description": "隐式变量，标识 <param> 是否按转置 shape 元组顺序解释",
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
| "支持转置"（可转可不转，如非量化场景 x/weight） | `[false, true]` | 两分支都生成 |

#### constraints_in_parameters 体现（语义 B 核心：bool 驱动轴位置）

**必须**产出 `shape_value_dependency` if/else，把 `<param>` 的**转置关键轴**（收缩
轴 K 或输出轴 M）在 shape 中的位置与 `<param>_transposed` 绑定。生成器不直接表达
"shape 元组整体"，而是表达**轴等式**——bool=True 时关键轴落在转置态位置、False 时
落在默认态位置，cases 产出后 bool 与 shape 形态自洽。

##### 模板（以 x `(M,K)`/`(K,M)`、K 为收缩轴、与 weight 的 K 轴相等为例）

```text
# x_transposed 门控 K 轴在 x.shape 的位置（False→shape[1]，True→shape[0]）
expr_type: shape_value_dependency
expr: (x.shape[1] == weight.shape[<weight_K_axis>])
        if (x_transposed.range_value == False)
      else (x.shape[0] == weight.shape[<weight_K_axis>])
        if (x_transposed.range_value == True)
      else True
relation_params: ["x", "weight", "x_transposed"]
src_text: "x 不转置 shape=(M,K)，K 在 shape[1]；转置 shape=(K,M)，K 在 shape[0]；
           由 x_transposed 门控。K 轴与 weight 的 K 轴相等（收缩轴）。"
```

`<weight_K_axis>` 由该场景 weight 的 shape 元组读出（weight 自身是否转置、是否 3D
等决定 K 落在 weight.shape 的第几位）。多场景（不同 groupType/splitItem）按
`knowledge/aclnn/operators/grouped_matmul_v5.md` §4.6.3 H 的 `cross_param_constraint` unless 范式叠加门控，析取覆盖场景表全部合法行。

##### 场景门控（value_dependency）

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
src_text: "groupType=2（k 轴分组）时 x 必须转置，shape=(K,M)"
```

```text
# 场景3：groupType∈{-1,0} → x 不转置
expr_type: value_dependency
expr: not(groupType.range_value in [-1, 0]) or (x_transposed.range_value == False)
relation_params: ["groupType", "x_transposed"]
src_text: "groupType=-1/0 时 x 不转置，shape=(M,K)"
```

场景 2 + 3 联立后，`x_transposed` 取值由 `groupType` 唯一决定，再由上面的
`shape_value_dependency` if/else 驱动 K 轴位置——cases 层 `x_transposed` 与 `x.shape`
形态一一对应。

#### 与 §4.7.3 item 15（groupType 场景轴等式）的协调

- §4.7.3 item 15 落 groupType 场景的**收缩轴/输出轴等式**（`x.shape[K_i]==weight.shape[K_j]`
  等），用 `cross_param_constraint` unless 范式门控 groupType/splitItem/单-多 tensor；
- 本模块落 `<param>_transposed` 驱动的**轴位置 if/else**（K 在 shape[0] 还是 shape[1]）；
- **若 groupType 决定转置**（如 GroupedMatmulV5 groupType=2→x 必转置），**优先**落本模块
  的 `value_dependency`（groupType→`<param>_transposed` 派生）+ `shape_value_dependency`
  （`<param>_transposed`→轴位置），由 bool 统一驱动轴位置；item 15 的 groupType 场景
  等式作为**场景层**补充（门控 groupType 合法组合），**不**与 bool 轴位置门控重复
  表达同一 K 轴等式——同一轴等式只由一处门控，避免双重约束冲突。

#### 反例（禁止）

```text
# ❌ 无条件轴等式（不按 bool 门控）
x.shape[1] == weight.shape[2]
# x_transposed=True 时 K 应在 shape[0]，此式取错轴

# ❌ bool 当孤立场景标志，不驱动 shape（bool 与 shape 对应不上）
expr_type: value_dependency
expr: not(x.dtype == "INT8" and weight.dtype == "INT8") or (x_transposed.range_value == False)
# 缺配套 shape_value_dependency if/else → cases 层 bool 与 x.shape 无绑定，对应失败

# ❌ 把 bool 写进 function_signature
# <param>_transposed 是隐式控制变量，不是 API 真实入参，不得入签名
```

#### src_text 要求

`src_text` **必须同时摘录**转置与非转置两种 shape 元组原文（如"x 不转置 shape=(M,K)"
与"转置 shape=(K,M)"），不可只摘默认布局；变量名与布尔值是生成器补充的结构化控制信息，
不得伪造成函数签名原文（与 `knowledge/aclnn/operators/batch_matmul_weight_nz.md` §B.1 规则 5 一致）。
