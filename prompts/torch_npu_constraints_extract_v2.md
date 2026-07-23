# torch_npu 算子约束提取提示词 v2

本提示词基于 v1（`prompts/torch_npu_constraints_extract_v1.md`），由
`iter_002/iter_003` 失败-analyst 反馈驱动升级。零算子专属信息；适用于所有
`torch_npu.*` Python API 文档。

## v1 → v2 变更摘要（必读）

| 类别 | 变更点 | 触发反馈 |
|---|---|---|
| Dtype 链 | 提示规则 3.2：必须写出 dtype 三元组一致性 | iter_002 EXECUTE 报 `aclnn 161002 dtype_mismatch` |
| Batch 对齐 | 提示规则 3.3：BSND/PA_BSND 必写 `shape[0]==` 链 | iter_002 EXECUTE 报 `aclnn 561002 batch_dim_mismatch` |
| Optional 参数 | 提示规则 3.4：统一使用 `is None` / `is not None` 表达 presence | 保持约束数据结构稳定，不暴露生成器内部属性 |
| Z3 兼容 | 提示规则 3.5：始终保留合法完整表达式，禁止 `# TODO:` 跳过约束 | 防止 shape/layout/presence 硬约束在生成和审计阶段同时失效 |
| Audit 兼容 | 提示规则 3.6：外部约束保持 `is None` 风格，内部适配由执行层负责 | 避免约束文件耦合 `ConstraintValue`/Z3 实现 |
| Bool 比较 | 提示规则 3.1.1：bool 参数与 `True`/`False` 必须用 `==`/`!=`，禁止 `is`/`is not` | 防止 bool 值关系被误写成 Python 对象身份判断 |

---

## 1. 角色、输入和唯一输出

（沿用 v1，章节编号不变。）

你是 `torch_npu` Python API 约束提取专家。输入：
1. 一份 `torch_npu` 算子 Markdown 文档；
2. 当前完整提示词快照；
3. 当前轮输出目录。

唯一业务输出是一个纯 JSON 对象，写入 `<iter-dir>/constraints.json`。不得在 JSON
前后输出 Markdown、解释、注释或第二个 JSON。随后依次执行：

```text
python scripts/normalize_constraints.py <iter-dir>/constraints.json
python scripts/validate_artifacts.py constraints <iter-dir>/constraints.json
python scripts/validate_artifacts.py hs_constraints <iter-dir>/constraints.json
```

## 2. OperatorRule 数据契约（沿用 v1）

（沿用 v1，章节不变。）

## 3. 提取规则

> **（v2 新增）** 章节 3.1-3.6 是从 v1 提炼的"必查清单"。每条都必须从原文找到
> 依据或显式标 `[NO_EVIDENCE]`；不得靠 LLM 推断。

### 3.1 基础属性与离散 enum（沿用 v1）

#### 3.1.1 Bool 参数和值的比较语法（v2 强制）

当约束表达式判断一个参数或参数属性是否为布尔值 `True`/`False` 时，必须使用值比较
运算符 `==` 或 `!=`，不得使用对象身份运算符 `is` 或 `is not`，也不要改写成隐式
真值判断。该规则适用于普通表达式、条件蕴含和析取分支。

正确示例：

```python
(return_value == False) or (layout_key.range_value != "PA_BSND")
(return_value != True) or (output.shape[0] == 1)
flag.range_value == True
```

错误示例：

```python
(return_value is False) or (layout_key.range_value != "PA_BSND")
flag.range_value is True
not return_value
```

`is` / `is not` 仅允许用于 `None` 的存在性判断，例如 `param is None` 或
`param is not None`；不得用于 `True`、`False` 或其他普通参数值。JSON 数据中的布尔
常量仍写小写 `true`/`false`，但 JSON 字符串字段 `expr` 内按 Python 语法写
`True`/`False`。

#### 3.1.2 Python List/aclIntArray 的长度与元素值域（v2 强制）

对于 Python `List[int]`、`tuple[int]`、aclIntArray 等非 Tensor 容器，文档中的
“shape 为 `[N]`”“长度为 N”“包含 N 个元素”都描述**容器长度**：固定长度写入
`array_length.value=[N]`。它们不是 Tensor rank，也不是元素取值范围，因此不得写入
`dimensions` 或 `allowed_range_value`。

`allowed_range_value` 只保存列表**每个元素**的离散候选或数值范围。若文档仅说明长度
而没有说明元素范围，该字段必须留空；不得把 optional 的 `None/null` 当成列表元素候选。
optional presence 由参数可选属性及 `param is None` / `param is not None` 关系表达。

正确示例（“`actual_shared_prefix_len` 存在时 shape 为 `[1]`”）：

```json
"array_length": {"value": [1], "src_text": "存在时shape需要为[1]", "type": null},
"allowed_range_value": {"value": [], "src_text": "", "type": null}
```

如果文档另外约束列表元素与 Tensor 轴的关系，必须再写带 presence 守卫的
`value_dependency`/`shape_value_dependency`，不得用 `array_length` 代替元素关系。
固定长度列表的元素用 `container[0]`、`container[1]` 访问；不得将整个
`container.range_value`（Z3 序列）直接与单个整数或 Tensor shape 轴比较。
同一条非平台特有的文档约束必须复制到所有产品平台结果，不能只修补第一个平台。

### 3.2 Dtype 三元组一致性（v2 新增）

**所有 query/key/weights 类三元组的算子（典型的 attention、layer_norm、matmul
族），必须从原文 dtype 段写出三元组一致性约束。** 文档原文可能分散在三段
（query/key 段 + weights 段 + dtype 段），需综合。常见两种形式：

- 严格一致：`query.dtype == key.dtype == weights.dtype`
- weights 允许 float32 escape hatch：
  `(weights.dtype == "float32") or ((query.dtype == key.dtype) and (weights.dtype == query.dtype))`

约束表达式写成 `(condition) or (full_match)` 形式以避免蕴含反向。**禁止**写
`(weights.dtype != "float32") or (weights.dtype == query.dtype)` —— 这类只要求
weights 与 query 同 dtype 而漏掉 key，是 v1 反复出现的蕴含反向错误。

### 3.3 Batch 对齐（v2 新增）

**所有 layout 依赖的 batch 维必须显式写出 shape[0] 等式。** 最常见：

- BSND: `(layout_query == "BSND" and layout_key == "BSND") implies (query.shape[0] == key.shape[0])`
- TND: query/key 第一维是各自总 token 数 Tq/Tk，不是 batch。文档将
  `actual_seq_lengths_query`、`actual_seq_lengths_key` 都描述为 `[B]` 时，B 是
  同一个 effective batch 符号，必须提取两者 `shape[0]` 相等的跨参数关系；不得令
  任一 `actual_seq_lengths_*.shape[0] == query.shape[0]`
- PA_BSND: `layout_key == "PA_BSND" implies (query.shape[0] == actual_seq_lengths_key.shape[0] == block_table.shape[0])`

展开成 implied Python：`(not (layout_key == "PA_BSND")) or ((query.shape[0] == actual_seq_lengths_key.shape[0]) and (query.shape[0] == block_table.shape[0]))`

TND 两个 `[B]` 的推荐表达式（参数为 optional，必须带 presence 守卫）：

```python
(layout_query.range_value != "TND") or \
(layout_key.range_value != "TND") or \
(actual_seq_lengths_query is None) or \
(actual_seq_lengths_key is None) or \
(actual_seq_lengths_query.shape[0] == actual_seq_lengths_key.shape[0])
```

提取后必须用反例核验：`layout_query=layout_key="TND"`、两个参数均存在且 shape
分别为 `[2]`、`[1]` 时，表达式必须为 `False`。只分别提取“rank 为 1”不能表达
共享符号 B，也不能阻止该无效用例。

注意三种 layout 下的 query batch 与 effective batch 不同（PA_BSND 用 actual_seq_lengths。
query 代替）。文档 §6（BSND/PA_BSND layout 章节）必查。

### 3.4 Optional 参数与 None presence（v2 修正）

**任何 optional tensor 参数（如 actual_seq_lengths、block_table 等），涉及该
参数属性的约束必须先用 `is None` 短路守卫；条件必传使用 `is not None`：**

```python
(param is None) or (param.range_value >= 0)
(param is None) or (len(param.shape) == 1)
(layout.range_value != "TND") or (param is not None)
```

条件存在/缺省关系必须先明确“条件 A”和“结果 B”，再按 `A -> B` 等价式
`(not A) or B` 展开。条件本身含 `!=` 时尤其不能凭文字直觉增删 `not`：

```python
# layout != PA_BSND -> block_table is None
(layout.range_value == "PA_BSND") or (block_table is None)

# layout == PA_BSND -> block_table is not None
(layout.range_value != "PA_BSND") or (block_table is not None)
```

“A 场景存在，否则缺省”是双向关系，必须保留上述两个 implication，或写成两个
完整 AND 分支。产出前至少代入一条正例和一条反例做真值表：对
`layout=TND, block_table=非空`，第一条表达式必须求值为 `False`；若为 `True`，
说明蕴含方向写反。不得把
`(layout != PA_BSND) or (block_table is None)` 误认为
“非 PA 场景必须为空”，它实际约束的是 PA 场景。

约束文件中禁止出现 `<param>.is_present`。它是生成器/Z3/审计包装对象的内部实现属性，
不是算子文档参数属性，也不是稳定的约束数据结构字段。执行层可以在内部将
`param is None` 转换为 presence flag，但提取结果必须保持文档语义写法。

文档 §3 参数描述章节必查可选项（"可选" / "Optional" / "默认值" / "None"）。
`*` 后 keyword-only 参数不等于可选；只有签名默认 `None` 或文档明确可不传时才能使用
None presence 分支。

### 3.5 保留完整约束，禁止 `# TODO:` 跳过（v2 修正）

`expr` 必须始终是可解析的完整 Python 布尔表达式。禁止在 `expr` 前添加 `# TODO:`、
`TODO:` 或其他使表达式失效的文本前缀，也禁止为了绕过 Z3 而删除 layout、shape、
dtype、value 或 presence 分支。

以下表达式都必须原样保留为有效约束：

```python
(return_value == False) or (layout_key.range_value != "PA_BSND")
(layout_query.range_value != "BSND") or (query.shape[3] == 128)
(param is None) or (len(param.shape) == 1)
```

若怀疑生成器无法求解，仍保留真实 `expr`，并在该条 `src_text` 或相关参数
`description` 末尾记录 `[GENERATOR_LIMITATION:具体原因]`。生成器是否降级、告警或
跳过只能由生成层决定，约束提取层不得通过破坏 `expr` 代替求解策略。

### 3.6 Audit 兼容（v2 提示）

`param is None` / `param is not None` 是约束文件唯一允许的 optional presence 写法。
audit 或 Z3 若需要内部 presence flag，应在各自执行层透明转换；不得要求提取器输出
`param.is_present`。涉及 optional 参数属性时，None 守卫必须位于 `or` 左侧以利用
Python 短路语义，避免 absent 时继续访问 `.shape`、`.dtype` 或 `.range_value`。

### 3.7 shape/dtype/format/range 分离（沿用 v1）

### 3.8 constraint_in_parameters 聚合（沿用 v1）

## 4. 输出 schema 与验证（沿用 v1）

## 5. 自修正协议（沿用 v1）

## 6. 引用文档章节（v2 提示 — 必须按 § 查）

| 约束类别 | 文档章节 |
|---|---|
| 参数列表与 dtype | § 算子函数签名 / § 参数 |
| layout 与 shape | § BSND / § TND / § PA_BSND |
| dtype 一致性 | § dtype 支持范围 + § query/key/weights 单独段 |
| batch 对齐 | § layout 章节 + § 参数 shape 描述 |
| optional 参数 | § 参数 / § 默认值 / § 调用示例 |
| 互斥关系 | § 使用限制 / § 注意 |
| 返回值含义 | § 返回值说明 |

---

## 附：v2 提示词交付后预期效果

按本提示词抽取的 constraints.json 应在 GENERATE 阶段：
1. dtype 一致性约束直接命中，EXECUTE 不再报 161002
2. BSND/PA_BSND batch 对齐直接命中，EXECUTE 不再报 561002
3. optional 参数统一用 `is None` / `is not None`，不暴露 `.is_present`
4. shape/layout/dtype/value/presence 约束保持完整有效，不使用 `# TODO:` 跳过
5. audit/Z3 所需的 presence 转换由执行层内部完成
6. EXECUTE 0/10 失败应减少 70%+（剩余的 Z3 solver hint 与下游 scenario planner 优化）
7. 所有 bool 参数值关系使用 `== True/False` 或 `!= True/False`，不存在
   `is True/False`、`is not True/False` 或隐式 `not bool_param`

## 附：v2 不解决的问题

- Z3 对混合 sort Or 的 solver hint（需 generation 层适配）
- scenario planner 与约束的同步（应在 scenario_planner.py 引入 `pre_constraints` 钩子）
- block_table.shape[1] >= maxBlockNumPerSeq 等更细约束（需文档 § 参数 shape 描述深入）
- TND 非递减前缀和及末值（已记 `[SCHEMA_GAP:sequence_element_relation]`，需 schema 层支持）
