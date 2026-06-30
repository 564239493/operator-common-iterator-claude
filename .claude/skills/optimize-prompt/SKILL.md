---
description: 根据 constraint_extraction 分析结果产生下一版完整通用提示词。
---

# 提示词优化规范

前置条件：analysis.json 的 root_cause 必须是 constraint_extraction。

只修改由 specific_issues 支持的章节，保留原提示词整体结构和所有无关规则。输出
`prompt_v<N+1>.md` 与 `prompt_changes_v<N+1>.md`。变更说明逐项映射：失败 case、
文档证据、原规则缺陷、新规则。禁止写入当前算子名称的硬编码特例。

