# Prompt: 翻译 cca 行为划分 guard → 补充约束 IR

> 用途：把 cca `fn-*.md` 的 `## 行为划分` 里每条 `(guard, outcome)` 翻译成生成器可消费的
> Python-bool `expr`，并判定与 doc-json 的 ✅/⚠️/❌。本 prompt 是 Recon-2b 的可复用模板。
> 词汇表与建模规则**必须与 Z3 求解器实际支持一致**（见下「词汇表」），否则约束被 `[FAIL]` 静默丢弃。
> few-shot 取自 `samples/v5_constraints_supplemented.json`。

---

## 任务

输入：一条 cca 行为划分分支（guard 原文 + outcome 类型 + 来源行号）。
输出：一个 JSON 对象，字段如下：

```jsonc
{
  "branch_ref": "cca 分支路径(IR DFS 序) + 行号/分支序号",
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

## 词汇表（必须与 Z3 求解器实际支持一致；否则约束被 `[FAIL]` 静默丢弃）

> 求解器（`expression_preprocess_utils.py` 的 `ASTtoZ3Converter` + `z3_expression_solver_utils.py`）
> 实际只支持下面这些。**`check` 只做 `ast.parse`（纯语法）**，`is_null`/`platform_arch`/`m_nonzero`
> 都能过 `check` 却被求解器 `[FAIL]` 丢弃——故翻译完必须实跑 `generate_cases.py` 验证（见「执行要求」）。

- **支持的函数（仅这 7 个 + 量词）**：`len / all / any / max / min / sum / prod`。
  **不支持** `is_null`/`ndim`/`dim`/`dtype()` 等任何别的函数名——写了即 `[FAIL]`。
- **属性（仅这些）**：Tensor/TensorList 的 `.shape` / `.dtype` / `.format` / `.range_value`；
  TensorList 另有 `.length`。**没有 `.ndim`**——元素 ndim 写 `len(t.shape)`。
- **scalar**：int/float/bool/枚举；int 标量取值用 `.range_value`（见下「int 标量参数取值用 .range_value」）。
- **List<T>**：`len(l)`、`l[i]`、`max/min/sum`。
- **DType 枚举比较**：用**字符串字面量**且为规范名
  `int4/int8/int16/int32/int64/uint4/uint8/uint16/uint32/uint64/bf16/fp16/fp32/fp64/double/float/bool/string/...`
  （见 `DataMatchMap.DTYPE_SPECS`）：写 `x[0].dtype == "int8"`。**禁止裸名** `== INT8`——
  预处理器（`ACL_DTYPE_TRANSFER_TENSOR_MAP`）只把 `INT8` 正则替换成 `int8`，仍是未声明裸名 → KeyError。
- **算术/逻辑/集合**：`+-*/%` · 比较 `==/!=/</<=/>/>=/in/not in` · `and/or/not` ·
  有界量词 `all(... for ... in range(...))` / `any(...)`。

## 建模规则（sound-by-construction，关键）

- 指针→张量用属性（`x.dtype`、`x.shape`），不建模内存地址。**元素 ndim 写 `len(t.shape)`，不要写 `ndim(t)`（求解器无此函数）**。
- 张量列表（TensorList）→用 `len(xs)`（= `.length`）、`xs[i]`（首元素 `x[0]` 是元素代理，可 `.shape/.dtype/.format/.range_value`）。
  **各元素共享 `elem_shape`**，故“每个元素 ndim∈[a,b]”写 `a <= len(x.shape) <= b`（区间用链式比较；
  **`in [a,b]` 是二元素列表成员判定，非区间**，不要用），
  **不要**写 `all(ndim(t) in [a,b] for t in x)`——生成器循环变量**不**绑成元素代理，
  `all/any` 内的 `t.shape`/`t.dtype` 取不到元素属性，会报错或静默失效。
- **判空/存在性**：optional 入参用 `param is None` / `param is not None`（编码为 `is_present` 布尔）；
  列表空用 `len(l) == 0`。**不要用 `is_null(...)`（求解器无此函数，会 `[FAIL]`）**。
- **后一分支隐含前面判空/边界已过**——把前提**显式合取进 expr**
  （如 `x is not None and len(x.shape) >= 2 and ...`），别丢。
- **门控/条件守卫检查必须保留前提为析取逃逸（关键，违反即过约束）**：当代码形如
  `if (cond) { CHECK(predicate) }`——某 CHECK 仅在特定参数取值下才执行——cca guard 会含
  **外层 if 守卫合取项**（如 `groupType != gmm::SPLIT_K`）。把该失败分支翻成成功路径约束时，
  **禁止**只取 `predicate` 内层谓词而丢弃守卫合取项；必须写成析取逃逸
  `not cond or <predicate>`（cond 不成立→该约束不适用→析取真）。cond 取反时按枚举值：
  `groupType != SPLIT_K` → 守卫为「groupType==SPLIT_K(2) 时跳过」→ 翻成
  `groupType.range_value == 2 or <predicate>`。**丢前提=约束变严**：Z3 仍可满足、仍 0 `[FAIL]`，
  求解器门禁查不到（过约束盲区），只能靠 `verify-coverage` 与人 review 兜底。
- **多层调用链前提是累积合取**：入口 `aclnnFoo` → 中间 `Common` → 末层 `CheckEmptyTensor`，
  每层调用点的 `if(cond)` 都会在 cca 内联展开后**累积进同一条 guard** 的合取项（cca 已替你内联，
  guard 文本里 `x != nullptr`(入口 CheckNotNull 层) ∧ `groupType != SPLIT_K`(Common 层 if) ∧ 末层谓词）。
  故“前提”= guard 里**所有**入参值比较合取项的并集，逐项决定「保留为析取逃逸」还是「doc 已覆盖为值集」。
  末层若 cca 未展开（标“旧版本事实/取自…”，可信度 中）→ 够不着，保 stub，别硬编。
- **变量名只能是 constraints.json `inputs` 里声明的入参**。求解器对每个裸名调 `get_or_create_var`，
  未声明名按 `tensor` + `dtype=None` 建模 → `_infer_element_sort` 抛 `Unsupported dtype: 'None'`
  → `add_constraint` 记 `[FAIL]` 并**静默丢弃该约束**（不进 solver），生成的用例会违反该约束意图。因此：
  - **禁止**出现非入参的裸名：`platform_arch`/`soc`/`arch`/`npu_arch`（平台不是入参，无 `.range_value`）、
    `m_nonzero`/`n_nonzero`/`k_axis`/`transposeX`/`transposeWeight`/`xDtype`（运行时计算中间量）等。
  - 这类平台/计算中间量依赖的高层语义 → 保 `stub=true` + `expr="TODO_..."`（`build-final` 自动跳过 stub/TODO，
    不并入 z3 桶），note 写明缺什么（如“constraints.json 无 platform 输入参数，需按 product→arch 特化后方可入 z3”）。
- **枚举/具名常量用源码全名**：`GMMActType::GMM_ACT_TYPE_GELU_ERR_FUNC` → 写 `GMM_ACT_TYPE_GELU_ERR_FUNC`，不缩写。
- groupType 映射：`NO_SPLIT=-1, SPLIT_M=0, SPLIT_K=2`（文档“-1 不分组/0 m轴/2 k轴”）。
- 平台：`NpuArch::DAV_xxxx` 是**平台条件**，不是入参——**不要**写成 `platform_arch == DAV_xxxx`
  进 z3 桶；按上一条保 stub（除非 constraints.json 显式有 platform/soc 入参）。
- **int 标量参数取值用 `.range_value`**：跨参 expr 引用 int 型标量参数（如 `actType`、`groupType`、
  `splitItem`）的取值时**必须**用 `<param>.range_value`（如 `actType.range_value in [0,1,2,4,5]`、
  `groupType.range_value == -1`），与本仓库 `constraints.json` 的 expr 规范一致；**禁止**裸名
  （`actType in [...]`）。裸名在 Z3 编码/post_check 上会出错。
- **跨 sort 比较展开析取**：凡涉及「int 枚举码 attr 与 `tensor.dtype` 比较」的约束，**必须**展开成
  显式析取 `(attr==<int码> and tensor.dtype=="<DType名>")` 的析取，**禁止**直接写
  `attr == tensor.dtype`（Z3 sort mismatch，整条守卫会被 `add_constraint` 丢弃）。
  DType 名用**字符串字面量**且为 `DTYPE_SPECS` 规范名（如 `"int8"`/`"bf16"`），不要裸名。

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
 "expr":"weight[0].dtype == \"int32\" and len(weight.shape) == 0",
 "relation_params":["weight"],"outcome":"ub","verdict":"supplement","not_for_solver":true,
 "src_text":"cca 第3条: weight INT32 且有元素 GetDimNum()==0 → UnpackB32ToB4 越界",
 "note":"前置被破坏才触发，不喂 z3；UB 类 doc 完全无。dtype 用字符串字面量；TensorList 各元素共享 elem_shape，‘有元素 ndim==0’即 len(weight.shape)==0。"}
```

### 例4（❌ stub：够不着的高层语义，别硬编码进 z3）
输入：cca L61 `groupListType != SPARSE_M ∨ (NpuArch∈{DAV_2201,DAV_3510} ∧ groupType==SPLIT_M)`，outcome=success。constraints.json 无 platform/soc 入参。
输出：
```json
{"branch_ref":"cca L61 groupListType×arch(SPARSE_M)","category":"constraints_in_parameters",
 "expr_type":"value_dependency",
 "expr":"TODO_sparse_m_platform(groupListType, groupType, platform_arch)",
 "relation_params":["groupListType","groupType"],"outcome":"success","verdict":"supplement","stub":true,
 "src_text":"cca L61: groupListType != SPARSE_M ∨ (NpuArch∈{DAV_2201,DAV_3510} ∧ groupType==SPLIT_M)",
 "note":"STUB：groupListType==2(SPARSE_M) 需特定 arch。constraints.json 无 platform/soc 入参，Z3 无法声明 platform_arch（get_or_create_var 抛 Unsupported dtype:None）。需按 product→arch 映射特化后方可入 z3，TODO。"}
```

### 例5（⚠️ 门控守卫前提必须保留为析取逃逸——本 skill 头号易错点）
输入：cca Common 内联 CheckEmptyTensor 失败分支，guard =
`x != nullptr ∧ weight != nullptr ∧ groupType != gmm::SPLIT_K ∧ (存在 i 使 (*x)[i]==nullptr 或 GetViewShape().GetDimNum()∉[2,6])`，
outcome=定义错误。源码 `aclnn_grouped_matmul.cpp:2419` 为 `if(groupType != gmm::SPLIT_K) { CHECK_COND(CheckEmptyTensor(x,weight)==...); }`。
**错误翻法**（丢前提，过约束，Z3 仍 0 `[FAIL]` 查不到）：
```jsonc
// ❌ "expr":"2 <= len(x.shape) <= 6"   // 丢了 groupType != SPLIT_K，SPLIT_K 时也强加 ndim
```
**正确翻法**（保留 call-site if 守卫为析取逃逸）：
```json
{"branch_ref":"cca Common 内联 CheckEmptyTensor L2419+L71","category":"constraints_in_parameters",
 "expr_type":"shape_value_dependency",
 "expr":"groupType.range_value == 2 or 2 <= len(x.shape) <= 6",
 "relation_params":["x","groupType"],"outcome":"success","verdict":"supplement",
 "src_text":"cca Common 内联 L2419(if(groupType!=SPLIT_K))+L71 CheckEmptyTensor: groupType!=SPLIT_K ∧ 存在 i 使 (*x)[i]==nullptr 或 DimNum()∉[2,6] → 定义错误",
 "note":"代码 2419 行仅 groupType!=SPLIT_K(2) 时才调 CheckEmptyTensor；故 groupType==2 时无 ndim 要求(析取真)，其余要求 x 元素 ndim∈[2,6]。call-site if 守卫必须保留为析取逃逸，否则 SPLIT_K 用例被过约束。"}
```

## 执行要求

1. **逐条 guard 都要产出一个对象**，不漏；嵌套子分支也各自产出。
2. **保留 ¬/∧/∨ 的否定结构**：cca 写 `¬(A ∧ B)` 就译 `not (A and B)`，可后续德摩根，但先对齐原文。
3. **拿不准就标 `verdict=supplement` + note 说明存疑**，别强行判 consistent。
4. 输出**纯 JSON 数组**（或“已分桶”对象），每元素如上 schema；不要输出别的文字。
5. **Z3 求解器实测是唯一硬门禁**：`check` 只做 `ast.parse`（纯语法），`is_null`/`platform_arch`/`m_nonzero`
   等都能过 `check` 却被求解器 `[FAIL]` 静默丢弃。故每批落盘后、以及 `build-final` 合并后，必须实跑：
   `python scripts/generate_cases.py --constraints <最终 constraints.json> --output <iter>/cases_check.json --count 1 --iter-dir <iter>`
   检查日志**无 `[FAIL]` 行**。`[FAIL]` = 该约束被 `add_constraint` 丢弃（不进 solver），生成的用例会违反约束意图，**必修**。
   （`[PostCheck] expr eval error (fail-open, 不阻断)` 是另一类预存告警，连 doc 原 expr 也会命中，非阻断、非本步骤引入，可忽略。）
   有 `[FAIL]` → 回“翻译 guard”按「词汇表」「变量名只能是入参」规则改 expr，再 `check → build-final → 本步`，直到 0 `[FAIL]`。
6. **过约束盲区靠 `verify-coverage` 兜底**：丢前提=约束变严，Z3 仍 0 `[FAIL]` 查不到。故每批落盘后必须跑
   `python scripts/cca_translate_cli.py verify-coverage --parse-ir <IR> --batch <批> --doc-constraints <doc> --product <产品>`
   逐条复核 ⚠「前提未保留」：若是 call-site if 守卫（如 `groupType != SPLIT_K`）→ 必补析取逃逸；
   若是 callee 自身值集校验且 doc 已覆盖（标 doc-covered）→ 确认一致即可。advisory 不阻断，但每条 ⚠ 必须有结论。
