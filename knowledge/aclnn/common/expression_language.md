---
module: expression_language
scope: common
description: expr 语法、关系模式与 expr_type 字典
default_load: true
triggers: []
depends_on: [implicit_parameters]
---

# 约束表达式语言

`expr` 字段在将裸 `null` 规范化为 Python `None` 后，必须是**合法 Python 布尔表达式**
（`eval()` 可执行，返回 `bool`）。禁止空字符串、赋值、自然语言、箭头和未定义函数。

## §语法

1. **变量引用**：使用**裸参数名**或 `参数名.shape[i]` / `参数名.dtype` / `参数名.format` / `参数名.range_value`：
   - ✅ `len(x.shape) == 3`
   - ✅ `x.shape[0] * x.shape[1] <= 2147483647`
   - ✅ `rankSize.range_value in [2, 4, 8]`
   - ✅ `x1.shape[0] == BS.range_value`
   - ✅ `x1.format == x2.format`
   - ❌ `tensor_x.dim == 3`（**禁止**别名）
2. **取值范围**：数值区间必须使用比较运算；离散枚举使用 `in [v1, v2]`：
   - ✅ `0 <= actType.range_value <= 5`（数值闭区间）
   - ✅ `0 < epsilon.range_value <= 1e-4`（数值开/闭区间）
   - ✅ `activation.range_value in ["relu", "gelu"]`（对枚举查）
   - ✅ `alltoAllAxesOptional.range_value == [-2, -1]`（对固定值等号）
   - ✅ `transposeX1.range_value == False`（bool 等号）
   - ❌ `actType.range_value in [[0, 5]]`（嵌套列表是数据结构，不是区间谓词）
   - ❌ `epsilon.range_value in [[null, 0.0001]]`（不得用 `null` 充当数值边界）
3. **复合逻辑 —— 蕴含两种等价形式**：
   - **形式 A（if/else）**：`(B) if (A) else True` —— 条件不满足时返回 True（约束不适用）
     - ✅ `(bias.dtype == "FLOAT16") if (x.dtype == "FLOAT16") else True`
   - **形式 B（unless 结构）**：`not(A) or B` —— 条件不满足时约束不生效
     - ✅ `not(quantization_type.range_value == "per-channel") or (bias.shape == [E, N1])`
     - ✅ `not(A and B) or C` 用于"两个条件同时成立才约束"的场景
   - 等价关系若涉及 presence（任一侧含 `is None` / `is not None`），禁止使用布尔
     `(A) == (B)`。两个 Optional 参数共存时使用 `if/else`：`(zeroPoints2 is None)
     if (scales2 is None) else (zeroPoints2 is not None)`。presence 与必选 Tensor 的
     rank 两态绑定也使用下方模式 3 的 `if/else`。
4. **生成器**：必须用 `all()` / `any()` 包裹：
   - ✅ `all(v >= 1 for v in padding.range_value)`
   - ✅ `all(d > 0 for d in x.shape)`（不允许空 Tensor）
   - ❌ `[v >= 1 for v in padding.range_value]`（返回 list，不返回 bool）
   - `all()` / `any()` 的生成器仅用于逐元素布尔判定；**不得**将生成器
     传给 `sum()`，当前求解器不支持 `sum(expr for ... in ...)`、
     `sum(... for ... in zip(...))` 或带索引循环的求和。
   - 数组整体求和只使用 `sum(param.range_value)`。若被求和项是线性组合，
     必须先做代数等价变换：

     ```text
     # 文档：reduceSum(A[i] - B[i]) <= capacity
     # 正例：分别对完整数组求和，再做标量运算
     sum(A.range_value) - sum(B.range_value) <= capacity.range_value

     # 反例：当前求解器无法翻译
     sum(A.range_value[i] - B.range_value[i]
         for i in range(min(len(A.range_value), len(B.range_value)))) <= capacity.range_value
     sum(a - b for a, b in zip(A.range_value, B.range_value)) <= capacity.range_value
     ```

   - 代数改写不得擅自加入 `min(len(...))` 截断。若文档明确要求数组等长，
     另建 shape/长度约束；若合法场景中两数组长度本来不同，不得为了
     索引循环强制等长，应根据文档的总量语义对两个完整数组分别求和。
5. **"维数 vs 长度"**：表达式中的 `len(x.shape)` 表示 rank（仅 `aclTensor` / `aclTensorList` 有 `.shape`），"shape size" 永远指 rank，**不是**各维大小乘积。`aclIntArray` / `aclFloatArray` / `aclBoolArray` **没有 `.shape`**，其元素个数直接写 `len(paramName)`（裸参数名），**禁止** `len(paramName.shape)`。
6. **负索引优先**：当约束引用了以字母命名的维度（如 `H`、`W`）且该维度在 shape 描述中**始终处于固定语义位置**（如"最后一维"），必须使用 `shape[-1]` 而非固定正索引 `shape[1]` 或 `shape[3]`。
   - **"固定语义位置"指物理位置固定，非逻辑轴名固定**：shape 元组重排编码转置时（如 `x` 不转置 shape=(M,K)、转置 shape=(K,M)），末维物理位置始终是 `shape[-1]`，逻辑轴名虽变（K 或 M）但物理位置不变，仍适用本条。条件映射（"K 轴或 M 轴"）只放 `src_text`，不因此改用 `[:-1]` 或加 if/else。
   - **`[-1]` 与 `[:-1]` 不可混用（语义相反）**：`shape[-1]` 取最后一维（单值），`shape[:-1]` 是**排除**最后一维的切片（多值序列）。文档"最后一维 < X"必须写 `shape[-1] < X` 或保守写 `all(d < X for d in shape)`（全维）；**禁止** `all(d < X for d in shape[:-1])`（漏掉末维，约束的是非末维，与文档相反）。`[:-1]` 仅用于第 12 条"排除末维派生轴"的 shape 切片等式。
7. **命名维度变量 / 外部常量引用**：使用 `变量名.range_value` 形式（如 `BS.range_value`、`rankSize.range_value`），不写 `BS.shape[0]`。
8. **已知常量直接使用数值**：若文档给出 `k0 = 16` 这种赋值，表达式里直接写 `16`，不需要 `k0.range_value`；NZ 块尺寸硬约束中 `mat2.shape[3] == 16` / `mat2.shape[4] == 16` 即此规则的体现（v2 新增）。
9. **禁止关键字**：`lambda`、非蕴含三元运算符滥用、`implies`、伪代码、平台值作为判断条件。
10. **`null` / `None`**：表达式允许使用 JSON 风格裸值 `null`，执行前会规范化为
    Python `None`；也可直接写 `None`。它只用于空值、可选值和存在性判断，例如
    `bias is null` 或 `bias is not None`，
    不得作为数值区间端点参与 `<`、`<=`、`>`、`>=`。**整条约束无法形式化为
    Python 布尔表达式时，不得产出空 `expr` 的 `constraints_in_parameters` 条目**
    （违 §4.7.2——`InterParamConstraint.expr` 不得为空字符串）；改把语义记入相关参数 `description`/`src_text`。不要用整个
    JSON 值 `null` 代替 `expr` 字符串。
11. **参数名冲突**：当参数名为 `max`/`min`/`sum` 等内置函数名时，表达式中**不要再调用**同名内置函数；`relation_params` 仍写原名。
12. **Partial-Shape 切片**：当文档明确表明只有最后一维是派生轴时，
    `gradOutput.shape[:-1] == self.shape[:-1]` 是合法的 `shape_equality`
    表达式。`-1` 表示排除这个已被文档确认的末维派生轴，并非由 backward /
    1d 名称自动决定。必须直接使用 shape 切片等式，不得改写为
    `in [self.shape[:-1]]`，也不得用无关的 `gradInput.shape` 近似替代；其他切片
    边界只有在文档明确给出对应派生轴时才能使用。

`relation_params` 按 expr 首次出现顺序列出所有业务/隐式参数，常量与内建名不列入。
`src_text` 必须同时覆盖 guard 和结论；不能只摘条件的一半。表达式无法直接对应原句
（如文档只给 "shape 与 x 一致"）时，`expr` 写 `out.shape == x.shape`，`src_text` 摘录 `"out 的 shape 与 x 保持一致"`。

## §常用模式

> 按以下流程匹配：先识别场景特征 → 套用对应模板。

### 模式 0：Optional TensorList 长度相等

**适用场景**：参数 P、Q 均为 `aclTensorList`，文档明确说明“P 长度与 Q 相同”。

```text
# P 为 Optional
(P is None) or (len(P) == len(Q))

# P 为必选
len(P) == len(Q)
```

`relation_params` 必须为 `[P, Q]`（按表达式首次出现顺序去重），`expr_type` 可使用
`presence_dependency`。禁止以下错误写法：

```text
len(P.shape) == Q.array_length
P.array_length == Q.array_length
```

### 模式 1：枚举条件 + 条件 Shape

**适用场景**：同时含 `per-channel`/`per-tensor` 等枚举值、`Optional` 是否存在判断、`[E, N1]` 条件 shape。

```text
# 单条件
not({enum_param}.range_value == "{value}")
  or ({target}.shape == [{vars}.range_value, ...])

# 双条件（枚举 + 存在性）
not({enum_param}.range_value == "{value}" and {presence_param} is not None)
  or ({target}.shape == [{vars}.range_value, ...])
```

### 模式 2：多 Shape 候选

**适用场景**：shape 有多个候选，由枚举参数或条件决定。

```text
# 二选一（条件驱动）
({target}.shape == [shape_A]) if (condition) else ({target}.shape == [shape_B])

# 多选一（枚举驱动）
({target}.shape == [shape_A]) if ({enum}.range_value == "mode_A")
else ({target}.shape == [shape_B]) if ({enum}.range_value == "mode_B")
else True
```

### 模式 3：存在性依赖

```text
# 两个 Optional A/B 共存：使用 if/else，禁止布尔 == 和 OR-of-ANDs
(B is None) if (A is None) else (B is not None)

# P 缺席 ↔ T 为2D，且 P 存在 ↔ T 为3D
(len(T.shape) == 2) if (P is None) else (len(T.shape) == 3)

# 条件存在：(B is not None) if (A is not None) else True
# 条件不存在：(B is None) if (A is not None) else True
```

presence/rank 双向关系不得只保留 `P is None and len(T.shape) == 2`；必须在 `else`
中保留 P 存在时的 rank=3 分支。仅当平台或场景已经明确排除 P 存在分支时，才可只
使用缺席分支的合取。

该模板假定 `T` 在两态中都存在；若 `T` 本身 Optional，分支中必须先约束 T 的
presence，再访问 `T.shape`。若文档只陈述一个方向，应使用单向蕴含 `not(A) or B`，
不得擅自补成双向合法组合。

注意：presence 参与合法组合时统一优先使用 `if/else`。当前求解器会把同一 `and`
中的纯 presence 守卫与属性条件解释成蕴含，所以把两个 presence/rank 分支写成
OR-of-ANDs 会退化为两个蕴含的 OR，并可能恒为 True；两个 Optional 的 OR-of-ANDs
还会在缺参预处理阶段错误清空 False 分支。

若业务同时要求 Optional 参数存在且属性满足，不写 `P is not None and P.attr == v`；
应拆成两条约束：`P is not None` 强制存在，`P is None or P.attr == v` 在存在时限制属性。
“允许缺席，存在时限制属性”则只需要第二条。

### 模式 4：单参数自身约束

```text
# 取值范围：{min} < {param}.range_value < {max}  /  {param}.range_value > {min}
# 允许枚举：{param}.range_value in [{v1}, {v2}, ...]
# 维度数量（aclTensor/aclTensorList）：{min} <= len({param}.shape) <= {max}
# 数组长度（aclIntArray/aclFloatArray/aclBoolArray，无 .shape）：{min} <= len({param}) <= {max}
# 各维大小（aclTensor/aclTensorList）：all(d <= {max} for d in {param}.shape)
# 空 Tensor 限制（aclTensor/aclTensorList）：all(d > 0 for d in {param}.shape)
```

**非 Tensor 数组类型的长度区间**：`aclIntArray` / `aclFloatArray` / `aclBoolArray` **本身即数组**（int/float/bool 的一维序列），**没有 `.shape` 属性**——`.shape` 仅对 `aclTensor` / `aclTensorList` 合法。当文档对这类数组参数写"支持 N-M 维"（如 `aclnnCalculateMatmulWeightSize.tensorShape` 的"输入shape支持2-6维"），表达的是**数组长度区间**，必须写成 `2 <= len(tensorShape) <= 6`（裸参数名直接入 `len()`）；**禁止**写成 `2 <= len(tensorShape.shape) <= 6`（aclIntArray 无 `.shape`，运行期 `AttributeError`）或 `2 <= tensorShape.shape[0] <= 6`（第一元素值范围，语义错误）。`array_length.value=[2,6]`（`type="range"`），`dimensions.value=[]`（非 Tensor 类型按类型前置规则恒为空，**不**写 `[2,6]`）。

**隐式 >0 约束（大小/数量语义参数，v3 增补）**：当某标量取值参数的 description 含
"空间大小"/"数据量"/"元素个数"/"数量"等语义短语时，必须按 `knowledge/aclnn/features/implicit_pos.md` §4.6.9 追加
`P.range_value > 0` 条目（`expr_type=value_dependency`）。此约束来自参数语义而非
文档显式取值范围描述，`src_text` 摘录 description 原文并补注"大小/数量语义隐含 >0"。

### 模式 6：门控条件 Shape（v3 新增）

**适用场景**：某参数的 shape 由 enum/boolean 门控参数 Y 的取值决定（典型：
MatMul 类算子的 `transposeX2` 门控 x2 的 shape、轴变换算子的 `axis` 门控 output 的
shape、MoE 类算子的 `group` 门控 token 排布等）。文档常常先写默认 shape，再写
门控后的 shape，例如：

> "x2 的 shape 为 (H\*rankSize, N)；配置为 True 时右矩阵 Shape 为 (N, rankSize\*H)"

**推荐写法（if/elif/else 链）**：

```text
# 二选一（单 bool 门控）
({target}.shape == [{shape_default}]) if ({gate}.range_value == {default_value})
else ({target}.shape == [{shape_gated}]) if ({gate}.range_value == {gated_value})
else True

# 真实例子（aclnnAlltoAllMatmul）：x2 shape 由 transposeX2 门控
# - transposeX2=False: x2.shape == [H*rankSize, N]
# - transposeX2=True:  x2.shape == [N, H*rankSize]
(x2.shape == [H.range_value * rankSize.range_value, N.range_value])
    if (transposeX2.range_value == False)
else (x2.shape == [N.range_value, H.range_value * rankSize.range_value])
    if (transposeX2.range_value == True)
else True
```

**等价写法（`unless` 多分支）**：

```text
# 每条分支用 not(or)/or 包成"前提不满足则跳过"
not({gate}.range_value == {default_value}) or ({target}.shape == [{shape_default}])
not({gate}.range_value == {gated_value}) or ({target}.shape == [{shape_gated}])
```

**`expr_type` 选择**：

- 优先 `shape_choice`（多个候选 shape 中选其一），便于下游按枚举遍历；
- 也可使用 `parameter_representation` 或 `shape_value_dependency`；
- 若门控参数取值范围文档未完全枚举，末尾必须保留 `else True` 兜底。

**反例（禁止）**：

- 写两条独立无条件 expr（如 `x2.shape == [H*rankSize, N]` 与 `x2.shape == [N, H*rankSize]`），
  **丢失门控上下文**，下游生成器会把它们当作两个独立候选而不区分 `transposeX2`。
- 把 `transposeX2.range_value == True` 写成 `transposeX2 == True`（**禁止裸参数名**，
  必须 `参数名.range_value`）。
- 把 shape 字面量写成 `"[H*rankSize, N]"` 字符串（**禁止**）。

**触发场景示例**：

| 触发信号 | 目标 shape 字段 | 门控参数 |
| -------- | ---------------- | -------- |
| "transposeX2 为 True 时 shape 为 (N, H*rankSize)" | `x2.shape` | `transposeX2` |
| "axis=1 时输出 shape 为 (BS, N, H)" | `output.shape` | `axis` |
| "group=tp 时 x shape 为 (BS/rankSize, H)" | `x.shape` | `group` |
| "squeeze 为 True 时输出 shape 去除 axis 维" | `output.shape` | `squeeze` |

> 模式 5（NZ 块尺寸）、模式 6.1（隐式 bool 门控）、模式 7（Partial-Shape）、模式 9（派生值查找）按需见
> `knowledge/aclnn/features/nz_matmul.md`、`knowledge/aclnn/features/backward_partial.md`、
> `knowledge/aclnn/features/format_cast.md`。

## §expr_type

> `InterParamConstraint.expr_type` 类型为**自由 `str`**（不受 Pydantic 枚举约束）。
> 下表列出**已知的常用取值**作为**参考指引**；若语义无法匹配，允许使用文档实际语义值。

### 参数间约束（2+ 参数）

| `expr_type` | 适用场景 | 典型 `expr` |
| --- | --- | --- |
| `shape_broadcast` | 形状需满足广播关系 | `all(a.shape[i] == b.shape[i] or a.shape[i]==1 or b.shape[i]==1 for i in range(N))` |
| `shape_choice` | 形状在多个候选中选其一（含 v3 新增的门控条件 Shape） | `bias.shape == gamma.shape or bias.shape == x.shape` |
| `shape_equality` | 形状完全相等 | `out.shape == x.shape` |
| `shape_dependency` | 输出 shape 由输入 + 辅助参数推导 | `out.shape[0] == pad + x.shape[0]` |
| `shape_value_dependency` | shape 中具体轴值/元素值依赖 | `x1.shape[0] == x2.shape[1] and x2.shape[1] == BS.range_value` |
| `type_equality` | 无条件、独立的纯 dtype 属性相等；整条 expr 不得含门控、`None`、`.range_value` 或非 dtype 属性 | `x1.dtype == x2.dtype`；`x.dtype == y.dtype and x.dtype == z.dtype` |
| `type_dependency` | dtype 依赖模式/取值/存在性/条件分支/合法组合（含互推导关系） | `optional is None or x.dtype == optional.dtype`；`(mode.range_value == "PA_NZ") or (x.dtype == y.dtype)`；互推导用合法 dtype 组合析取表达 |
| `value_dependency` | 取值依赖/取值范围 | `BS.range_value % rankSize.range_value == 0` |
| `format_equality` | 数据格式必须一致 | `x1.format == x2.format` |
| `presence_dependency` | 两个 Optional 共存规则（None/非None） | `(zeroPoint is None) if (scale is None) else (zeroPoint is not None)` |
| `derived_value` | 派生输出取值由子接口确定映射推导（须可求解，见 `knowledge/aclnn/features/format_cast.md` §4.6.7 模式 9） | `actualFormat.range_value == dstFormat.range_value`（恒等）；查找表用析取 |

### 单参数约束（扩展值，不在枚举中但实际广泛使用）

| `expr_type` | 适用场景 | 典型 `expr` |
| --- | --- | --- |
| `cross_param_constraint` | 通用跨参数约束（语义较泛） | 按具体上下文 |
| `parameter_representation` | 隐式维度变量/外部常量与张量 shape 的绑定 | `x1.shape[0] == BS.range_value` 或 `rankSize.range_value in [2,4,8]` |
| `self_value_range` | 单参数取值范围（区间） | `0 <= actType.range_value <= 5` |
| `self_value_enum` | 单参数取值枚举 | `activation.range_value in ["relu", "gelu", "silu"]` |
| `self_value_dependency` | 单参数取值 ≈ 固定布尔/唯一合法值 | `transposeX1.range_value == False` |
| `self_string_length` | 字符串参数长度约束 | `0 < len(group.range_value) < 128` |
| `self_shape_dim_range` | 单参数维度（rank）/ 数组长度范围 | `2 <= len(x.shape) <= 3`（`aclTensor`/`aclTensorList`）；`2 <= len(arr) <= 6`（`aclIntArray` 等无 `.shape` 数组，裸参数名） |
| `self_shape_axis_value` | 单参数某轴值约束 | `x.shape[0] >= 1` |

模型字段虽是自由字符串，也不得随意创造下游没有消费语义的新类型；扩展类型必须已有代码路径或校验依据。
