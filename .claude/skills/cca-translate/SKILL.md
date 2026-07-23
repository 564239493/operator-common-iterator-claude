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
`locate / parse / reconcile / check / build-final`）。从项目根目录调用。

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
严格遵循本 skill 目录下的 **`translate_guard.md`**：词汇表用 cca 树里的 `eqclass-hint.md`
（Tensor.shape/dtype/ndim/is_null、List len、DType 枚举），sound-by-construction（显式合取前提、
枚举全名、不漏判空）。每条产一个 IR 对象：
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

### 5. 自检（固化）
```bash
python scripts/cca_translate_cli.py check --batch "$BATCH1" --batch "$BATCH2" ... [--out "$CHECK_JSON"]
```
每条非 stub / 非 TODO 的 expr 必须 `ast.parse(expr, mode="eval")` 通过。有失败 → 修，再跑。
退出码 0=全过，1=有失败，2=异常。

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
作**额外顶层键**保留（不丢信息）。其余产品/顶层字段（inputs/outputs/return_info/…）原样不动。
**输出与文档 constraints.json 同格式**（dict 按平台分桶则并入指定 product；扁平 list 则并入列表）。

## 规则

- 小步快跑：每批翻译（一个函数或一组分支）即落盘 + `check` 自检。
- 异常不吞：解析失败、`ast` 自检失败直接抛 / 退出码非 0。
- UB 不喂 z3：`ub_branches` 一律 `not_for_solver=true`。
- 不可达跳过：防生成不可能用例。
- 语义最终人 review：AST 只查语法；翻译漏合取项/边界写反要人看。
- 不假设固定路径：`CCA_DIR` / `DOC_CONSTRAINTS` / `PRODUCT` / `OUTPUT` 全部由调用方传入；
  本 skill 与 `scripts/cca_translate_cli.py` 内不得出现写死的算子路径或绝对路径。

## 参考样例（本目录下，few-shot 来源）

- `samples/v5_constraints_supplemented.json`：V5 入口合并后的补充产物
  (constraints/error/ub/normalize/unreachable/stubs 分桶)
- `samples/v5_constraints_final.json`：V5 合并原文档 constraints.json 的最终文件
  (保原字段 shape，origin="code" 标注)
- `samples/v5_supplemented_sample.json`：分批翻译过程样例
- `translate_guard.md`：翻译 prompt（词汇表 + few-shot），第 4 步严格遵循
