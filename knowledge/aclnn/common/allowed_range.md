---
module: allowed_range
scope: common
description: allowed_range_value 文本到结构化枚举/区间的映射
default_load: true
triggers: []
depends_on: []
---

# `allowed_range_value` 结构化映射

> 项目 `OperatorRule.allowed_range_value.value` 用**结构化**形式（区别于早期
> `knowledge/allowed_range/` 的文本形式）。本节映射表必须严格遵守。

## §映射表

| 原文描述 | `value` | `type` | 备注 |
| -------- | ------- | ------ | ---- |
| `"0-100"` / `"[-1,1]"` | `[[0, 100]]` / `[[-1, 1]]` | `range` | 区间 |
| `"0或1"` | `[[0, 1]]` | `range` | 二元 |
| `"0到5"` | `[[0, 5]]` | `range` | 中文区间 |
| `"大于0"` | `[]` | `range` | 单边/开区间不在 `allowed_range_value` 中伪造边界；写 `value_dependency`：`param.range_value > 0` |
| `"小于1024"` | `[]` | `range` | 写 `value_dependency`：`param.range_value < 1024` |
| `"padding的两个数值都需小于self最后一维度"` | `[]` | `range` | 动态边界依赖 `self.shape[-1]`，必须写入 `constraints_in_parameters`，禁止枚举几个样例值 |
| `"大于等于1"` | `[]` | `range` | 写 `value_dependency`：`param.range_value >= 1` |
| `"取值范围为245~333"` | `[[245, 333]]` | `range` | ~分隔 |
| `"fastgelu/gelu/relu/silu"` | `["fastgelu", "gelu", "relu", "silu"]` | `enum` | `/` 分隔 |
| `"fastgelu/gelu/relu/silu以及geglu/swiglu/reglu"` | `["fastgelu","gelu","relu","silu","geglu","swiglu","reglu"]` | `enum` | **必须拆分**为独立项 |
| `"支持配置空或者[-2,-1]"` | `[null, [-2, -1]]` | `enum` | aclIntArray 的"空"表示未传值，必须序列化为 JSON `null` |
| `"per-channel/per-group/per-tensor/per-token"` | `["per-channel","per-group","per-tensor","per-token"]` | `enum` | 量化粒度 |
| `"true/false"` | `[true, false]` | `enum` | bool 列举 |
| `"支持空或某个固定值"` | `[null, fixed_value]` | `enum` | `type=enum` 时 `null` 是合法离散候选 |
| `"当前仅支持输入nullptr"`（必选 tensor/attr 参数） | `[null]` | `enum` | 必选参数（`is_optional.value=false`）只支持 `nullptr`；`null` 是"取值语义"（参数出现且值为 `nullptr`），不是"缺席语义"；`src_text` 必须摘含 `nullptr`/`空指针` 原文以过 `_EXPLICIT_NULL_RE`；`dtype.value=[]`；**禁止**写 `value=[]` 或在 `constraints_in_parameters` 追加 `param is None` |
| `"支持传 nullptr 或 [0,1]"`（混合候选） | `[null, [0, 1]]` | `enum` | `null` 与其他取值并列；`null` 仍为"取值语义"；`src_text` 摘含 `nullptr` 的完整原文段；必选参数须满足 `_EXPLICIT_NULL_RE` 关键词要求 |
| `"k0=16、n0=16"`（NZ 块尺寸硬约束） | `[]` | `range` | **5D NZ 张量**：块尺寸 16 是 **shape 硬约束**，落 `constraints_in_parameters` 的 `shape_equality`（见 `knowledge/aclnn/features/nz_matmul.md` §C）；`allowed_range_value` 只约束元素数据值，**不**承载块尺寸，故留空 |
| `"块尺寸为 16"`（NZ 通用，未指明轴位） | `[]` | `range` | 同上，shape 约束走 `shape_equality`；具体轴位在 `nz_matmul.md` 识别后落 `mat2.shape[3]/[4]==16` |
| 文档无任何取值约束 | `[]` | `range` | **不**在数组中产出该参数 |

## §null语义

`type=range` 与 `type=enum` 对 `null` 的规则不同：

- `type=range`：任何区间端点都不得为 `null`。当前生成器不把 `null` 当作数值无界；
  单边、开区间必须用 `constraints_in_parameters` 中的不等式表达。
- `type=enum`：允许 `null`，表示"空值/未传值"本身是一个明确的离散候选。
- 当原文中的"空"表示未传值、缺省、空指针或 `nullptr` 时，必须输出 JSON `null`，
  禁止照抄为字符串 `"空"`。只有 API 明确接收字面字符串"空"时才能输出 `"空"`。
- 参数为必选（`is_optional.value=false`）且原文未明确允许未传值/空指针时，
  `allowed_range_value` 禁止包含 `null`；C/C++ 签名是指针不等于参数可以为空。**但"当前仅支持
  输入 nullptr"/"仅支持传空指针"/"必须为空指针"属于原文明确允许空指针**：此时必选参数的
  `allowed_range_value` 应为 `{"value": [null], "type": "enum", "src_text": "<含 nullptr/空指针
  的原文>"}`，不算违反上述禁令——`null` 在此是"取值语义"（参数出现且值为 `nullptr`），不是
  "缺席语义"。`allowed_range_value.src_text` 必须摘录含 `nullptr`/`空指针`/`未传`/`缺省`/
  `支持空`/`可为空`/`配置空` 之一的原文，否则校验层 `_validate_dynamic_allowed_ranges` 会以
  "必选参数 + `src_text` 无空值语义"为由拦截（见 `scripts/validate_artifacts.py:394-448`）。
- "未传容器"和"传入零长度容器"不是同一语义：前者为 `null`；只有原文明示传入
  长度为 0 的数组/列表实例时，才将空容器候选表示为 `[[]]`。空 Tensor 应使用
  shape/dimensions 约束表达，不在 `allowed_range_value` 中写 `"空"`。

## §aclIntArray特殊取值

`aclIntArray` 参数的取值往往是**特定数组值**或**未传值**，`type` 统一设为 `enum`。
仅当上下文明确出现"传入空""缺省""空指针"，或参数确为 Optional 时，空候选使用
JSON `null`；不得仅因 C/C++ 类型是指针就添加 `null`，也不得使用字符串 `"空"`：

| 原文 | `value` |
| ---- | ------- |
| `"支持配置空或者[-2,-1]"` | `[null, [-2, -1]]` |
| `"支持配置[-2,-1]或[-1,-2]或空"` | `[[-2, -1], [-1, -2], null]` |

## §bool类型参数

bool 参数（`is_xxx`/`xxxFlag` / `transposeX*` 等）**必须**产出 `allowed_range_value`，
`type` 统一为 `"enum"`，并按下表选择 `value`：

| 原文约束 | `allowed_range_value.value` |
| -------- | --------------------------- |
| "暂不支持配为 True" / "仅支持 False" | `[false]` |
| "暂不支持配为 False" / "仅支持 True" | `[true]` |
| 无明确固定值约束（仅描述为 bool） | `[false, true]` |

**算子特例优先级**：`aclnnBatchMatMulWeightNz` 的隐式布尔变量
`self_transposed`、`mat2_transposed` 必须按 `knowledge/aclnn/operators/batch_matmul_weight_nz.md` §B.1 使用
`value=[true, false]`（顺序也必须一致），不得套用本表的默认 `[false, true]`。

禁止：填写 `value=[]` + `type="range"`，否则下游生成器按浮点范围填充，
会产生 `1.0`、`0.0`、`1.23e-40`、`-2147483648.0` 等非法 bool 值。

## §无约束参数处理

下列场景**不**产出 `allowed_range_value.value` 条目（保持 `[]`）：

- 描述只涉及 shape/dtype/format，不涉及值域；
- Tensor / TensorList 参数（`aclTensor` / `aclTensorList`），维度不属于取值范围；
- 取值上下界依赖其他参数的 shape、长度或取值；这种动态关系必须完整写入
  `constraints_in_parameters`，禁止用少量"代表性样例"伪造枚举；
- 动态表达式只编码原文明示的比较方向和边界；原文仅写"小于"时禁止自行补充
  `>= 0`、非空等额外条件；
- **bool 参数例外**：见上一节"bool 类型参数"，必须产出 `type="enum"` 条目。

## §补充规则

- 离散值（"0 或 1""A/B/C"）写扁平 `enum`；分隔符拆成独立候选。
- 只有闭合数值区间可写 `range`；开区间、单边界、动态边界、整除与公式令本字段
  `value=[]`，并用可执行 expr 表达。
- `aclDataType` 的文档 dtype 候选是参数值域，写 `allowed_range_value(enum)`；其自身
  `dtype.value=["string"]`。
- `aclIntArray` 的元素 dtype 固定为 int；文档若列关联 Tensor dtype，不得写进数组
  dtype 或值域。
- 块尺寸、shape 轴范围和跨参数取值关系不得塞进 `allowed_range_value`。
