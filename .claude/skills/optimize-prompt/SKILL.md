---
description: 根据 constraint_extraction 分析结果产生下一版完整通用提示词。
---

# 提示词优化规范

前置条件：analysis.json 的 root_cause 必须是 constraint_extraction。

只修改由 specific_issues 支持的章节，保留原提示词整体结构和所有无关规则。输出
`prompt_v<N+1>.md` 与 `prompt_changes_v<N+1>.md`。变更说明逐项映射：失败 case、
文档证据、原规则缺陷、新规则。禁止写入当前算子名称的硬编码特例。

**模块化提示词（v4 起）**：提示词为 `prompts/operator_constraints_extract_v4.md`（基线）+ `prompts/modules/*.md`（按算子类按需装配的模块）。读取 `run_state.json` 的 `current_prompt_modules` 可知本轮命中的模块。在 `prompt_changes_v<N+1>.md` 中，逐项标注 specific_issues 指向的规则所属文件（基线章节或 `modules/<name>.md` 的 §<节>），便于后续将修复定位到 canonical 模块。当前仍沿用 per-iter `prompt_v<N+1>.md` 输出契约（round 2+ 使用该覆盖快照，不走模块装配）；将 canonical 模块直编作为后续优化方向。
