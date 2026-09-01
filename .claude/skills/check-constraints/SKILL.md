---
description: 对照算子文档及本轮明确补充证据检查最终 constraints.json，维护简洁的 constraint_check.json，供 constraint-checker 使用。
---

# 约束语义检查

## 输入

调度消息必须给出绝对路径：

- 当前 run 的 `run_state.json`；
- `run_state.operator_doc` 指向的算子文档快照；
- 当前 `<iter-dir>/constraints.json`（首轮已完成 SUPPLEMENT/冲突合并，或反馈轮已完成
  constraint update；两者均已通过结构校验）；
- 当前 `<iter-dir>/constraint_check.json`（第 2 轮起存在）；
- 若存在：`inputs/scene_directive.md`、`inputs/supplementary-doc.md`、
  `inputs/supplement_constraints.md`、`inputs/conflict_candidates.json`、
  `inputs/conflict_resolution.json`、触发本轮更新的上一轮 `analysis.json`、
  当前轮 `constraint_update.json` 或 `constraints_patch.json`；
- 首轮（初始化 EXTRACT 后）还必须读取：当前 `<iter-dir>/extraction_provenance.json`
  与 `inputs/prompt_preanalysis.json`（做必载知识审计，见检查规则第 9 条）。

只读上述输入与理解结构所必需的 schema/校验代码；其他 run、历史产物、memory 与
Agent 对话的完整隔离禁令以 constraint-checker agent 定义为唯一权威，此处不再
复述。允许通过 Skill 工具加载 `.claude/skills/` 下按 family 匹配的知识 skill
（`aclnn-*` / `torch-npu-*`）复核对应规则是否被正确应用。

## 检查规则

1. 每轮都完整扫描当前 constraints，而不只是复核旧问题。
2. 算子文档是文档约束的主事实源；场景指令只允许收窄文档场景；补充/冲突证据只覆盖
   其明确说明的约束，不能成为无关推断依据。
   未裁决的 `conflict-doc.md` 仍按现有异步人工通道处理，不自动选边，也不因此制造
   blocking issue；只有 `conflict_resolution.json` 中已裁决结果可作为检查依据。
3. 至少检查：参数存在性、dtype、format、shape/dimensions、值域、平台、确定性标记、
   跨参数关系，以及遗漏、错提和无依据新增。
4. `validate_artifacts.py constraints` 通过只说明结构/确定性规则合法，不能替代本检查。
5. 使用 Read 显示的实际行号记录错误；`line` 必须指向当前约束中最直接的错误行。
6. 第 2 轮起逐条复核原有 open/unfixed：已正确则 fixed，仍错误则 unfixed 并更新
   `last_checked_round`。新问题追加新 id，不能复用旧 id。
7. fixed 历史项保留在同一个报告中，不删除。
8. 当前轮有诊断 finding/update/patch 时，逐条核对 finding_ids、basis 与 expected_effect；新增
   约束必须能拒绝/修正对应失败 case，同时不得与文档明确合法样例冲突。没有覆盖、效果
   不成立或 patch 只是等价 noop 时记为 blocking issue。
9. **必载知识审计（仅首轮，extraction_provenance.json 存在时）**：对照
   `prompt_preanalysis.json` 路由命中集（即 `extraction_provenance.required_modules`）
   逐模块审计：
   - 模块在 provenance 中缺失，或 `modules_applied` 未覆盖 → 记 open issue
     （"命中未加载"，属约束提取遗漏的高危信号）；
   - `status=not_applicable` 的理由不成立（模块信号在当前文档中确实存在）→ 记
     open issue；
   - `status=applied` 但约束实际未体现模块规则（可加载对应知识 skill 复核）→ 记
     open issue。
   该审计不替代第 1 条的完整扫描。

## 唯一报告

当前轮只维护 `<iter-dir>/constraint_check.json`：

```json
{
  "schema_version": "1.0",
  "iteration": 1,
  "max_rounds": 3,
  "current_round": 1,
  "status": "needs_repair",
  "constraints_file": "<constraints.json 绝对路径>",
  "issues": [
    {
      "id": "CR-001",
      "found_round": 1,
      "last_checked_round": 1,
      "line": 86,
      "constraint": "groupType.allowed_range_value",
      "problem": "文档仅支持 0 和 1，当前错误表达为连续范围。",
      "suggestion": "改为 type=enum、value=[0,1]。",
      "status": "open"
    }
  ],
  "summary": {
    "total": 1,
    "open": 1,
    "fixed": 0,
    "unfixed": 0
  }
}
```

状态规则：

- 无 open/unfixed → `passed`；允许保留 fixed 历史。
- 有 open/unfixed 且 `current_round < max_rounds` → `needs_repair`。
- 有 open/unfixed 且 `current_round == max_rounds` → `failed`。

每次写完运行：

`python scripts/validate_artifacts.py constraint_check <iter-dir>/constraint_check.json`

只有返回码为 0 才能交给协调器。
