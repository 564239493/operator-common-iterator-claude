---
description: 根据 constraint_extraction 失败生成 run-local 候选和分层知识沉淀提案，不自动修改 canonical 文件。
---

# 提示词 / 知识优化规范

先完整阅读 `docs/PROMPT_EVOLUTION.md`。只有 `analysis.json.root_cause` 严格等于
`constraint_extraction`、`analysis.json.supplement_decision.has_explicit_additions=false`、
`analysis.json.prompt_optimization.eligible=true`，且 `specific_issues` 有 case、日志、
当前文档证据以及可定位的 Prompt 规则缺口时才能执行。generator/executor bug、源码
补充已足够、只有约束事实而没有规则缺口、证据不足均不得用提示词规则掩盖。

## 输入

读取 `run_state.json`、当前 prompt、当前算子文档、`prompt_preanalysis.json`、
`prompt_assembly.json`、命中的 `current_prompt_modules`、`analysis.json` 与执行结果。
按 `operator_family` 隔离 ACLNN 和 torch_npu，禁止跨知识根迁移。

## run-local 输出

1. `prompt_candidate_v<N+1>.md`：仅供下一轮试验的完整候选；
2. `prompt_changes_v<N+1>.md`：逐项映射失败 case、证据、原规则缺陷、候选 diff；
3. `prompt_update_proposal.json`：每项只选一个 destination：`base_prompt`、
   `knowledge_common`、`knowledge_feature`、`knowledge_operator`、`torch_npu` 或
   `no_update`，并写精确 canonical 目标、命中/适用边界、试验状态和回滚方式。

初始 `status=trial_pending`。下一轮验证通过可改为 `pending_user`；失败改为
`trial_failed`。运行成功只允许发起询问，绝不等于批准。

## 归类原则

- 稳定流程、证据边界或校验门禁才进入 ACLNN base；
- dimensions/allowed-range/表达式等全算子默认规则进入 common；
- 需要文档关键词或结构信号才成立的进入 feature；
- 任意算子常量、专属参数名、专属场景表或真机经验进入 exact operator；
- 已有模块足以覆盖、偶发环境问题或无法复现时选择 `no_update`。

## 禁止

- 不得直接修改 `prompts/` 或 `knowledge/` canonical 文件；
- 不得把单算子事实塞进 base/common，也不得把 ACLNN 规则写进 torch_npu；
- 不得自行调用全局提升脚本或从用户沉默推断批准。

终态由主协调器向用户逐项展示“目标、摘要、证据、适用范围、试验结果”，询问
“应用 / 暂缓 / 拒绝”。只有明确选择应用后才修改 canonical，并重跑 base 构建、路由和
组装冻结校验。
