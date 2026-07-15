---
description: 基于落盘证据将失败分类为 constraint_extraction、generator_bug 或 executor_bug。
---

# 失败诊断规范

按顺序读取当前提示词、原始文档、constraints.json、cases.json、存在时的
cases_expanded.json、execution_result.json。先检查 engine_error，再检查生成用例
是否违反已提取约束，最后检查约束是否遗漏或误解文档。

在归类 `generator_bug` 前，必须先完成约束语义与表达式检查：

- 将参数功能描述和取值说明合并阅读，检查是否漏掉可可靠推导的语义约束。例如
  `epsilon`/`eps` 被明确描述为“除0保护值”时，应满足严格正值；若另有建议上界，
  应形成 `0 < epsilon.range_value <= upper`。
- `allowed_range_value.type=range` 不允许以 `null` 充当数值边界；
  `type=enum` 允许 `null` 作为一个离散候选。
- `expr` 中裸 `null` 合法，按 Python `None` 解释，但只能用于空值/存在性判断，
  不能参与数值大小比较。
- 数值范围必须写为不等式，`.range_value in [[min, max]]` 属于提取表达错误。
- 只要约束遗漏、语义误解或表达式不合法足以解释失败，主根因应归为
  `constraint_extraction`；生成器没有友好报错可记录在 `generator_issue`，但不能
  因此覆盖上游主因。

还必须核对生成阶段和执行阶段的数据边界：

- `cases.json` 是紧凑表示；列表类输入以单个输入描述加 `length` 表示，展开由执行
  阶段生成 `cases_expanded.json` 完成。
- 带 `length` 的输入允许 `range_values` 为标量，语义是每个元素共用该取值规格。
  不得仅凭 `range_values` 是标量就判定 generator_bug，也不得建议在
  `ListVar.resolve_model()` 中按 `length`/`seq_len` 复制为列表。
- 必须对照同一 case 在 `cases.json` 与 `cases_expanded.json` 中的表示，并结合
  异常栈确认实际失败参数。紧凑表示已正确展开时，应继续查找真实根因；展开逻辑
  本身错误时归为 executor_bug。

写 `analysis.json`：

```json
{
  "root_cause": "constraint_extraction | generator_bug | executor_bug",
  "analysis": "根因摘要",
  "specific_issues": ["带 case id 或文档证据的问题"],
  "modified_sections": [],
  "generator_issue": "",
  "executor_issue": ""
}
```

**源码证据与两级补救**（当 `run_state.operator_src_snapshot` 非空）：
- 读 `<iter-dir>/source_evidence.json`（source-analyst diagnose 域产）。它已把
  error_string 命中的 uncertain 关系追加到 `inputs/supplementary-doc.md`，并给出
  `suggested_root_cause`（仅供参考，最终根因仍由本 agent 下）。
- root_cause=constraint_extraction 时两级补救：
  1. `source_evidence.log_match` 非空（补充已扩充）→ analysis 标注
     "补充已扩充，re-EXTRACT + re-SUPPLEMENT"，**不走 prompt-optimizer**。
  2. `log_match` 为空 → 自己根据错误日志 + 原算子文档尽力推约束关系，写入
     `<iter-dir>/supplement_additions.md`（标 `origin=diagnose_inferred`）；推不出
     → analysis 标注回退 prompt-optimizer。
- 读 `inputs/conflict-doc.md` + `inputs/conflict_resolution.json`：失败命中未裁决
  conflict → `specific_issues` 提示用户先裁决（冲突永远走人工通道）。

证据不足时不得猜测；列出缺失证据并保守归入 executor_bug。
