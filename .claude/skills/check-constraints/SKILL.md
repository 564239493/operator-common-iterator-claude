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
- `operator_family=aclnn` 时：步骤 0 产出的 `<iter-dir>/relation_examples.json`
  （Z3 正反例取证，见「步骤 0」与检查规则第 10 条）；
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

## 步骤 0：正反例取证（仅 operator_family=aclnn）

开始本轮检查前先执行（产物每轮覆盖写，repairer 刚修改过 constraints.json 也能
取到新证据）：

`python scripts/verify_relation_exprs.py <iter-dir>/constraints.json`

- 产物默认写同目录 `relation_examples.json`，读入后按检查规则第 10 条核对；
- exit 2 = 存在 syntax_error：按报告 `syntax_errors` 清单逐条记 open issue
  （expr 在 null 归一与关键词替换后仍非法），然后继续本轮完整检查；
- 脚本崩溃或无法运行：如实记录阻断原因并返回，不得跳过正反例核对宣称
  passed；
- torch_npu（hs）不执行本步骤（Z3 类型映射未适配），检查规则第 10 条不适用，
  行为与现状一致。

## 检查规则

1. 每轮都完整扫描当前 constraints，而不只是复核旧问题。
2. 算子文档是文档约束的主事实源；场景指令只允许收窄文档场景；补充/冲突证据只覆盖
   其明确说明的约束，不能成为无关推断依据。
   未裁决的 `conflict-doc.md` 仍按现有异步人工通道处理，不自动选边，也不因此制造
   blocking issue；只有 `conflict_resolution.json` 中已裁决结果可作为检查依据。
3. 至少检查：参数存在性、dtype、format、shape/dimensions、值域、平台、确定性标记、
   跨参数关系、约束条目 `id`（每条存在、全局唯一、`C-<NNN>` 格式、编号连续），
   以及遗漏、错提和无依据新增。
   同时核对每条 `constraints_in_parameters` 条目的 `src_txt_line`：字段存在、升序非空、
   且行号处的文档快照原文与该条目 `src_text` 摘录一致；缺失、行号错位或摘录与原文
   不符都记为 issue（`src_txt_line` 缺失/错位按普通 issue 报告，由 repairer 补正）。
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
10. **正反例核对（仅 aclnn，relation_examples.json 存在时）**：机器已证明每条
    expr 在 Z3 世界「接受什么、拒绝什么」，你负责判断该行为是否符合文档意图：
    - `ok_with_witnesses`：对照 `src_text` 核对正反例——`satisfy_example`（正例，
      expr 接受的实例组合）必须是文档允许的组合、`violate_example`（反例，expr
      拒绝的实例组合）必须是文档禁止的组合；语义反向或边界不符记 open issue，
      引用具体实例值作为证据；
    - `tautology`：先判成因。参数卡 `allowed_range_value` 已收窄导致的冗余
      （如场景 fix 后 expr 与域重叠、前件在当前域永假）语义正确，不记 issue——
      域与 expr 一致本身就是场景屏蔽自洽的佐证；表达式写法本身恒真（与取值域
      无关）记 open issue；
    - `unsatisfiable`（参数卡域内不可满足）、`bucket_status=contradiction`
      （整桶矛盾，引用 `unsat_core`）→ open issue；
    - `unconvertible`：按 `error` 归因。error 为 `'NoneType' object has no
      attribute 'get_z3_expr'` 表示参数未被成功声明——常见于
      `allowed_range_value=[null]`（"仅支持传 nullptr"编码）的 presence 参数，
      属脚本声明能力限制：确认参数卡存在且 `param is None` 语义与文档一致后
      不记 issue；其余转换错误（sort 错配、不支持的操作数/操作符）说明写法超出
      表达式语言、生成时同样难以消费，能定位错误记 open issue 并给出改写建议；
    - `undeclared_params` 非空：核对 expr 引用的参数名是否真的存在于
      inputs/outputs 参数卡；拼错或漏卡记 open issue；
    - `skipped_todo` 不记 issue；`unknown` 退回人工目视检查（同规则 3）。

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
