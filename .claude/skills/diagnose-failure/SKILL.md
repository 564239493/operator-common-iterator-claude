---
description: 基于落盘证据将失败分类为 constraint_extraction、generator_bug 或 executor_bug。
---

# 失败诊断规范

按顺序读取当前提示词、原始文档、constraints.json、cases.json、
execution_result.json。先检查 engine_error，再检查生成用例是否违反已提取约束，最后
检查约束是否遗漏或误解文档。

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

证据不足时不得猜测；列出缺失证据并保守归入 executor_bug。
