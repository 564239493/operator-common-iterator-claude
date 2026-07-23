---
description: 把 cca 行为划分(fn-*.md)翻译成补充约束，与文档 constraints.json 对账合并成最终文件。用于从代码侧补充/纠正文档抽取的入参约束。所有路径由参数给出，无写死路径。
---

# cca-translate：cca 行为划分 → 补充约束 → 合并最终 constraints.json

把 cca_analysis_result 里某算子入口（及子函数）的 fn-*.md 行为划分，翻译成生成器可消费的
Python-bool 约束，与文档 constraints.json 三分类对账（✅一致 / ⚠️代码更严 / ❌补充），
合并成**保持文档 constraints.json 原格式**的最终文件。

## 何时用

- 用户要把某算子（如 aclnnGroupedMatmulV5）的**文档约束**与**代码约束**对账补充。
- cca 分析树里已有该算子的 fn-*.md。若该算子无 cca 分析，本 skill 无法用（不要硬上）。

## 输入参数（全部由调用方给出，无写死路径）

| 参数 | 含义 | 例 |
|---|---|---|
| `CCA_DIR` | cca 分析树根目录（含 manifest.json + fn-*.md + eqclass-hint.md） | `/root/code/cann/opp/cca_analysis_result/_cca_analysis_result` |
| `DOC_CONSTRAINTS` | 文档分析的 constraints.json（输入格式 = 输出格式） | `runs/<run-id>/iter_001/constraints.json` |
| `OPERATOR` 或 `ENTRY_SUFFIX` | 算子名片段 / 入口 id 后缀，用于在 manifest 定位 fn-*.md | `aclnnGroupedMatmulV5` / `::aclnnGroupedMatmulV5GetWorkspaceSize` |
| `PRODUCT` | 要并入的产品键（constraints_in_parameters 为按平台分桶(dict)时必填；扁平 list 时可省） | `Atlas A2 训练系列产品/Atlas A2 推理系列产品` |
| `BATCH_FILES` | LLM 翻译产出的补充批次文件（可多个） | `runs/<run-id>/iter_001/cca_batchN.json` |
| `OUTPUT` | 最终 constraints.json 输出路径 | `runs/<run-id>/iter_001/constraints_final.json` |

确定性 Python 工具：`scripts/cca_translate_cli.py`（纯标准库，子命令
`locate / parse / reconcile / check / verify-coverage / build-final`）。从项目根目录调用。

## 工作流

> 思路：Python 负责“确定性”的解析/对账/合并/语法自检；LLM（我）只负责把 cca guard
> 翻译成 Python-bool（按 `translate_guard.md` prompt），不嵌套调用别的 LLM。

### 1. 定位函数分析文件
```bash
python scripts/cca_translate_cli.py locate \
  --cca-dir "$CCA_DIR" --entry-suffix "$ENTRY_SUFFIX"
# 或按算子名模糊定位入口+全部子函数：
python scripts/cca_translate_cli.py locate --cca-dir "$CCA_DIR" --operator "$OPERATOR"
```
拿到入口及子函数的 fn-*.md 路径（stdout 每行一个 JSON，含 `analysis_file`/`exists`）。
入口一般取 `*GetWorkspaceSize`；子函数（CheckFunctionParams / CheckParamXxx /
GetGMMResultByL0Api 等）同样用 `locate` 按 `--operator` 捞出来。

### 2. 解析行为划分 → IR
```bash
python scripts/cca_translate_cli.py parse --fn-md "$FN_MD" --out "$PARSE_IR"
```
得到分支树（guard / outcome_kind{成功,定义错误,UB} / total_partial / source / confidence）。
**guard 文本经 cca 内联展开后已把多层调用链前提累积为合取项**（入口层 CheckNotNull 前提 ∧
中间层 `if(cond)` 调用点前提 ∧ 末层谓词），第 4 步翻译与第 4.5 步自检都依赖它。

### 3. 参数共现对账（捞缺口）
```bash
python scripts/cca_translate_cli.py reconcile \
  --doc-constraints "$DOC_CONSTRAINTS" --fn-md "$FN_MD" \
  --product "$PRODUCT" --out "$RECON_JSON"
```
标 doc_only（文档有、代码 host 不查）/ code_only（代码有、文档缺 → 补充）候选。
注意：参数共现只看参数重合，不判语义；真缺口在语义层，要靠第 4 步人(我)译。
一个 fn-*.md 跑一次 reconcile；多个子函数各跑一次，汇总 code_only 候选。

### 4. 翻译 guard → Python-bool（我是 LLM，按 prompt 翻）
严格遵循本 skill 目录下的 **`translate_guard.md`**——其「词汇表」「建模规则」是**与 Z3 求解器
（`agent/generators/param_constraint_solve/`）实测对齐过的**，不是随意词汇，务必逐条遵守。
关键红线（违反则约束被求解器 `[FAIL]` 静默丢弃，生成的用例会违反约束意图）：

- **函数仅** `len/all/any/max/min/sum/prod`；**不** `is_null`/`ndim`/`dim`/`dtype()`。元素 ndim 写 `len(t.shape)`。
- **属性仅** `.shape/.dtype/.format/.range_value`（TensorList 另有 `.length`），无 `.ndim`。
- **变量名只能是 constraints.json `inputs` 里的入参**。`platform_arch`/`soc`/`m_nonzero`/`n_nonzero`/
  `k_axis`/`transposeX`/`transposeWeight`/`xDtype` 等平台/计算中间量**不是入参**，禁止裸名出现，
  否则 `get_or_create_var` 抛 `Unsupported dtype: 'None'` → `[FAIL]` 丢弃。这类高层语义保
  `stub=true` + `expr="TODO_..."`（`build-final` 自动跳过，不并入 z3 桶），note 写明缺什么。
- **TensorList 各元素共享 `elem_shape`**：“每个元素 ndim∈[a,b]”写 `a <= len(x.shape) <= b`（链式比较），
  **不要** `all(ndim(t)... for t in x)`（生成器循环变量不绑成元素代理，`t.shape` 取不到），
  也**不要** `len(x.shape) in [a,b]`（那是二元素列表成员判定，非区间）。
- **DType 比较用字符串字面量**（`x[0].dtype == "int8"`，规范名见 `DataMatchMap.DTYPE_SPECS`），
  **禁止裸名** `== INT8`。
- **门控/条件守卫前提必须保留为析取逃逸**（头号易错点）：代码 `if (cond) { CHECK(pred) }`
  翻成成功约束时写 `not cond or <pred>`（如 `groupType.range_value == 2 or <pred>`），
  **禁止**只取 `<pred>` 丢掉 `cond`——丢前提=过约束，Z3 仍 0 `[FAIL]` 查不到（见第 4.5 步兜底）。
- optional 判空用 `param is None`/`is not None`（编码为 `is_present`），**不**用 `is_null(...)`。

每条产一个 IR 对象：
```jsonc
{"branch_ref":"...","category":"constraints_in_parameters|error_branches|ub_branches|normalize_rules|unreachable",
 "expr_type":"...","expr":"<Python-bool>","relation_params":[...],
 "outcome":"success|defined_error|ub|unreachable|normalize",
 "verdict":"consistent|code_stricter_replace|supplement","src_text":"<cca 原文含¬∧∨>","note":"<与doc哪条比对>"}
```
分类规则：成功→`constraints_in_parameters`(喂z3)；定义错误→`error_branches`；UB→`ub_branches`
(`not_for_solver=true`)；副作用/原地改写→`normalize_rules`(when/rewrite)；不可达→`unreachable`。
拿不准 `verdict=supplement`+note 存疑。够不着高层语义的子函数内部前置保 stub
(`stub=true`, `expr=TODO_...`)。

**批次文件两种写法都接受**（合并器都吃）：
- “已分桶”：`{operator_name,product,constraints_in_parameters:[...],ub_branches:[...]}`，条目无
  `category`（桶名已表达）。
- “带 category”：`{items:[{category:"...", ...}]}`，每条带 `category` 字段。
小步快跑：一个函数或一组分支翻译完即落盘一个批次文件（`cca_batchN.json`）。

### 4.5 前提保留自检（固化，advisory，**第 4 步后必跑**）
```bash
python scripts/cca_translate_cli.py verify-coverage \
  --parse-ir "$PARSE_IR" --batch "$BATCH1" --batch "$BATCH2" ... \
  --doc-constraints "$DOC_CONSTRAINTS" --product "$PRODUCT" [--out "$VERIFY_JSON"]
```
对每条 IR 分支，抽 guard 里**入参值比较前提参数**（含多层 `if(cond)` 累积合取项，如
`groupType != SPLIT_K`），检查是否被链到该分支(按 callee 名)的 code-expr 保留；列出**未保留**项：
- 若是 call-site if 守卫 → 必补析取逃逸（回第 4 步改 expr）；
- 若是 callee 自身值集校验且 doc 已覆盖（标 `doc-covered`）→ 确认一致即可。
advisory：不阻断（exit 0），但**每条 ⚠ 必须有结论**（补前提 or note 标 doc 一致）。
**为何必跑**：丢前提=过约束，Z3 仍可满足、仍 0 `[FAIL]`，第 5/7 步门禁查不到，只能靠这一步兜底。

### 5. 自检（固化，仅语法）
```bash
python scripts/cca_translate_cli.py check --batch "$BATCH1" --batch "$BATCH2" ... [--out "$CHECK_JSON"]
```
每条非 stub / 非 TODO 的 expr 必须 `ast.parse(expr, mode="eval")` 通过。有失败 → 修，再跑。
退出码 0=全过，1=有失败，2=异常。
**注意：`check` 只查 Python 语法，不查 Z3 可编码性**——`is_null`/`platform_arch`/`m_nonzero`
都能过 `check` 却被求解器 `[FAIL]` 丢弃。是否真能编码必须靠第 7 步实跑求解器。

### 6. 合并批次 → 最终文件
```bash
python scripts/cca_translate_cli.py build-final \
  --original "$DOC_CONSTRAINTS" \
  --batch "$BATCH1" --batch "$BATCH2" ... \
  --product "$PRODUCT" \
  --output "$OUTPUT"
```
`build-final` 把补充的**成功路径**约束按原字段
`{expr_type,expr,relation_params,src_text,origin}` 并入目标产品桶（`origin="code"`，
`verdict`/`note` 折进 `src_text` 便于追溯），`expr` 去重；UB/normalize/error/unreachable/stubs
作**额外顶层键**保留（不丢信息；`stub=true` 或 `TODO_` 前缀的**不并入** z3 桶）。
其余产品/顶层字段（inputs/outputs/return_info/…）原样不动。
**输出与文档 constraints.json 同格式**（dict 按平台分桶则并入指定 product；扁平 list 则并入列表）。

### 7. Z3 求解器实测（必做，硬门禁）
`check`（第 5 步）只做 `ast.parse`，**不能**保证求解器能编码。合并后必须实跑生成器验证：
```bash
python scripts/generate_cases.py --constraints "$OUTPUT" \
  --output "$ITER_DIR/cases_check.json" --count 1 --iter-dir "$ITER_DIR"
```
检查日志/输出**无 `[FAIL] ...` 行**。`[FAIL]` = 该约束被 `add_constraint` 静默丢弃
（不进 solver），生成的用例会违反约束意图 → **必修**：回第 4 步按 `translate_guard.md`
红线改 expr，再 `check → build-final → 本步`，直到 0 `[FAIL]`。
`[PostCheck] expr eval error (fail-open, 不阻断)` 是另一类预存告警，连 doc 原 expr 也会命中，
非阻断、非本步骤引入，可忽略。
验证产生的 `cases_check.json`/`cases_*.json`/`jsonl_checkpoints`/`generation*.log` 是临时产物，验完删。

## 规则

- 小步快跑：每批翻译（一个函数或一组分支）即落盘 + `check` + `verify-coverage` 自检。
- 异常不吞：解析失败、`ast` 自检失败、Z3 求解器 `[FAIL]` 直接抛 / 退出码非 0；`[FAIL]` 不修不放过。
- **求解器红线**：变量名只能是 `inputs` 入参；函数仅 `len/all/any/max/min/sum/prod`；无 `is_null`/`ndim`；
  DType 用字符串字面量；平台/计算中间量保 stub；**门控 if 守卫前提保留为析取逃逸**。详见 `translate_guard.md`。
- **过约束盲区**：丢前提=约束变严，Z3 仍 0 `[FAIL]`，第 5/7 步查不到；第 4.5 步 `verify-coverage` 是唯一兜底，必跑且每条 ⚠ 必有结论。
- UB 不喂 z3：`ub_branches` 一律 `not_for_solver=true`。
- 不可达跳过：防生成不可能用例。
- 语义最终人 review + 求解器实测双门：AST 只查语法（不够）；翻译漏合取项/边界写反要人看 + 第 4.5 步结构自检，
  且**必须**跑第 7 步求解器确认 0 `[FAIL]`。
- 不假设固定路径：`CCA_DIR` / `DOC_CONSTRAINTS` / `PRODUCT` / `OUTPUT` 全部由调用方传入；
  本 skill 与 `scripts/cca_translate_cli.py` 内不得出现写死的算子路径或绝对路径。

## 参考样例（本目录下，few-shot 来源）

- `samples/v5_constraints_supplemented.json`：V5 入口合并后的补充产物
  (constraints/error/ub/normalize/unreachable/stubs 分桶)
- `samples/v5_constraints_final.json`：V5 合并原文档 constraints.json 的最终文件
  (保原字段 shape，origin="code" 标注)
- `samples/v5_supplemented_sample.json`：分批翻译过程样例
- `translate_guard.md`：翻译 prompt（词汇表 + few-shot + 求解器红线 + 门控守卫规则），第 4 步严格遵循
