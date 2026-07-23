# Prompt: 翻译 cca 行为划分 guard → 补充约束 IR

> 用途：把 cca `fn-*.md` 的 `## 行为划分` 里每条 `(guard, outcome)` 翻译成生成器可消费的
> Python-bool `expr`，并判定与 doc-json 的 ✅/⚠️/❌。本 prompt 是 Recon-2b 的可复用模板。
> 词汇表与建模规则取自 cca 分析树里的 `eqclass-hint.md`，few-shot 取自
> `samples/v5_constraints_supplemented.json`。

---

## 任务

输入：一条 cca 行为划分分支（guard 原文 + outcome 类型 + 来源行号）。
输出：一个 JSON 对象，字段如下：

```jsonc
{
  "branch_ref": "cca 行号/分支序号",
  "category": "constraints_in_parameters | error_branches | ub_branches | normalize_rules | unreachable",
  "expr_type": "self_value_enum | self_value_range | value_dependency | shape_value_dependency | presence_dependency | ...",
  "expr": "<Python-bool，用下述词汇>",
  "relation_params": ["..."],
  "outcome": "success | defined_error | ub | unreachable | normalize",
  "verdict": "consistent | code_stricter_replace | supplement",
  "src_text": "<cca 原文 guard 片段，含 ¬/∧/∨>",
  "note": "<翻译要点、与 doc-json 哪条比对、为什么 verdict>"
}
```

## 分类规则（category / outcome 怎么定）

- outcome=**success** 且是“合法输入必须满足的约束” → `category=constraints_in_parameters`，喂 z3。
- outcome=**defined_error**（返回 ACLNN 错误码）→ `category=error_branches`，负例边界。
- outcome=**ub**（空指针解引用/越界/除零等）→ `category=ub_branches`，`not_for_solver=true`，不喂 z3。
- outcome=**unreachable**（与外层前置矛盾）→ `category=unreachable`，跳过。
- **副作用/原地改写**（Post 里 `SetDataType`/`UnpackB32ToB4` 等）→ `category=normalize_rules`，`when`/`rewrite` 两字段。

## verdict 规则

- `consistent`：与某条 doc-json 条目语义等价（标 doc ref）。
- `code_stricter_replace`：同一关系但代码更严/边界不同（如 doc 允许某值、代码排除），以代码为准。
- `supplement`：doc-json 无对应条目，代码新增。

## 词汇表（翻译产出用这些，不裸用指针/内存）

- **scalar**：int/float/bool/枚举。
- **Tensor**：`t.shape : List<int>`、`t.dtype`、`ndim(t):=len(t.shape)`、`dim(t,i):=t.shape[i]`、`is_null(t)`。
- **List<T>**：`len(l)`、`l[i]`、`max/min/sum`。
- **DType 枚举**：`INT8/INT4/FLOAT16/BFLOAT16/FLOAT/UINT64/INT64/INT32/...`（源码 `DT_*` 去 `DT_` 前缀）。
- **求值函数**：`len/max/min/sum` · `ndim/dim/dtype/is_null` · 算术 `+-*/%` · 比较 · 逻辑 `and/or/not` · 集合 `in` · 有界量词 `all(i for i in range(len(xs)))`/`any(...)`。

## 建模规则（sound-by-construction，关键）

- 指针→张量用属性/函数（`ndim(x)`、`x.dtype`），不建模内存地址。
- 张量列表→`List<Tensor>`，用 `len(xs)`/`xs[i]`。
- **判空**：能抽象成 Tensor/List 用 `is_null(t)`/`len(l)==0`；够不着的裸指针保 `ptr is not None`，**不漏**。
- **后一分支隐含前面判空/边界已过**——把前提**显式合取进 expr**（如 `not is_null(x) and ndim(x)>=2 and ...`），别丢。
- **枚举/具名常量用源码全名**：`GMMActType::GMM_ACT_TYPE_GELU_ERR_FUNC` → 写 `GMM_ACT_TYPE_GELU_ERR_FUNC`，不缩写。
- groupType 映射：`NO_SPLIT=-1, SPLIT_M=0, SPLIT_K=2`（文档“-1 不分组/0 m轴/2 k轴”）。
- 平台：`NpuArch::DAV_xxxx` → `platform_arch == DAV_xxxx`（由 soc 派生）。
- **int 标量参数取值用 `.range_value`**：跨参 expr 引用 int 型标量参数（如 `actType`、`groupType`、
  `splitItem`）的取值时**必须**用 `<param>.range_value`（如 `actType.range_value in [0,1,2,4,5]`、
  `groupType.range_value == -1`），与本仓库 `constraints.json` 的 expr 规范一致；**禁止**裸名
  （`actType in [...]`）。裸名在 Z3 编码/post_check 上会出错。
- **跨 sort 比较展开析取**：凡涉及「int 枚举码 attr 与 `tensor.dtype` 比较」的约束，**必须**展开成
  显式析取 `(attr==<int码> and tensor.dtype=="<DType名>")` 的析取，**禁止**直接写
  `attr == tensor.dtype`（Z3 sort mismatch，整条守卫会被 `add_constraint` 丢弃）。
  DType 名用大写规范名（预处理自动映成 Z3 DType enum 常量）。

## few-shot 示例

### 例1（✅ consistent）
输入：cca L61 合取项 `groupType ∈ {SPLIT_M, SPLIT_K, NO_SPLIT}` 且 `groupType != SPLIT_N`，outcome=success。
输出：
```json
{"branch_ref":"L61","category":"constraints_in_parameters","expr_type":"self_value_enum",
 "expr":"groupType.range_value in [-1, 0, 2]","relation_params":["groupType"],
 "outcome":"success","verdict":"consistent",
 "src_text":"cca L61: groupType ∈ {SPLIT_M, SPLIT_K, NO_SPLIT} 且 != SPLIT_N",
 "note":"doc-json[1] 等价（{0=m轴,2=k轴,-1=不分组}），SPLIT_N 被排除，保留。"}
```

### 例2（⚠️ code_stricter_replace）
输入：cca L61 `actType>=0 ∧ (actType==NONE ∨ (actType!=GELU_ERR_FUNC ∧ actType<END_ACT_TYPE_ENUM))`，outcome=success。doc-json[3] 写 `0<=actType<=5`。
输出：
```json
{"branch_ref":"L61","category":"constraints_in_parameters","expr_type":"self_value_enum",
 "expr":"actType.range_value in [0, 1, 2, 4, 5]","relation_params":["actType"],
 "outcome":"success","verdict":"code_stricter_replace",
 "src_text":"cca L61: actType>=0 ∧ (actType==NONE ∨ (actType!=GELU_ERR_FUNC ∧ actType<END_ACT_TYPE_ENUM))",
 "note":"doc-json[3] 允许 3，但 actType==3(GELU_ERR_FUNC) 不支持；以代码为准排除 3。"}
```

### 例3（❌ supplement + UB）
输入：cca 行为划分第3条 `weight->Size()!=0 ∧ (*weight)[0]->GetDataType()==DT_INT32 ∧ weight 有元素 GetDimNum()==0`，outcome=UB(UnpackB32ToB4 越界写)。
输出：
```json
{"branch_ref":"行为划分第3条","category":"ub_branches","expr_type":"value_dependency",
 "expr":"weight[0].dtype == INT32 and any(ndim(w) == 0 for w in weight)",
 "relation_params":["weight"],"outcome":"ub","verdict":"supplement","not_for_solver":true,
 "src_text":"cca 第3条: weight INT32 且有元素 GetDimNum()==0 → UnpackB32ToB4 越界",
 "note":"前置被破坏才触发，不喂 z3；UB 类 doc 完全无。"}
```

## 执行要求

1. **逐条 guard 都要产出一个对象**，不漏；嵌套子分支也各自产出。
2. **保留 ¬/∧/∨ 的否定结构**：cca 写 `¬(A ∧ B)` 就译 `not (A and B)`，可后续德摩根，但先对齐原文。
3. **拿不准就标 `verdict=supplement` + note 说明存疑**，别强行判 consistent。
4. 输出**纯 JSON 数组**（或“已分桶”对象），每元素如上 schema；不要输出别的文字。
