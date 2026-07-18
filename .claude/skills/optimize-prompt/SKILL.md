---
description: 根据 constraint_extraction 分析结果产生下一版完整通用提示词。
---

# 提示词优化规范

前置条件：analysis.json 的 root_cause 必须是 constraint_extraction。

先读取 `run_state.json.operator_family`。只修改由 specific_issues 支持的章节，保留原
提示词整体结构和所有无关规则。输出 `prompt_v<N+1>.md` 与
`prompt_changes_v<N+1>.md`。变更说明逐项映射：失败 case、文档证据、原规则缺陷、
新规则。

## family 隔离

- `operator_family=aclnn`：canonical 来源只能是
  `prompts/operator_constraints_extract_vN.md` 与 `prompts/modules/*.md`。禁止把
  torch_npu 的 Python 签名、layout、TensorList/返回槽规则写入 ACLNN 模块；禁止为
  单一算子在通用基线中硬编码名称特例。
- `operator_family=hs`（torch_npu）：canonical 来源只能是
  `prompts/torch_npu_constraints_extract_vN.md` 与
  `knowledge/torch_npu/**/*.md`。禁止引用或修改 `prompts/modules/*.md`，也禁止引入
  ACLNN workspace/GetWorkspaceSize/两段式 API 假设。通用缺陷定位到 torch_npu 基线或
  family 模块；仅对某个算子成立且有该文档证据的修复，可定位到其精确算子知识模块，
  不得写进通用模块。

读取 `run_state.current_prompt_modules` 了解当前快照命中的模块。在
`prompt_changes_v<N+1>.md` 中逐项标注规则的 canonical 文件与章节。当前仍沿用
per-iter `prompt_v<N+1>.md` 输出契约：round 2+ 使用完整覆盖快照，不重新装配模块；
优化器只生成 run 内迭代产物，不直接改 canonical 文件。
