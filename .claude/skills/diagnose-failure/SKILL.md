---
description: 基于落盘证据将失败分类为 constraint_extraction、generator_bug 或 executor_bug。
---

# 失败诊断规范

按顺序读取当前提示词、原始文档、constraints.json、cases.json、
execution_result.json。先检查 engine_error，再检查生成用例是否违反已提取约束，最后
检查约束是否遗漏或误解文档。

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

