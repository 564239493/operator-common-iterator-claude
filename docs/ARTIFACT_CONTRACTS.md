# 运行产物契约

## 目录

```text
runs/<operator>-<timestamp>/
  run_state.json
  inputs/
    <原算子文档文件名>.md
    prompt_v1.md
    src_snapshot/              # 可选：--source-root 时只读复制（op_host/**/*.{cpp,h,hpp,json} + docs/aclnn*.md）
  iter_001/
    constraints.json
    source_raw.json            # 可选：源码启用时，extract_source_constraints.py 确定性产物
    source_evidence.json       # 可选：source-analyst 产（hard_constraints + cross_check + doc_error）
    constraints_patch.json     # 可选：source-analyst 产，由 apply_constraints_patch.py 机械应用
    generation_summary.json
    cases.json
    execution_result.json
    quality_gate.json
    analysis.json
    prompt_v2.md
    prompt_changes_v2.md
```

## run_state.json

必须包含 `run_id`、`operator_doc_source`、`operator_doc`、`operator_src_source`、
`operator_src_snapshot`、`current_prompt_source`、`current_prompt`、
`current_prompt_modules`、`mode`、`server_config`、`max_iterations`、`case_count`、
`current_iteration`、`state`、`history` 和时间戳。state 只能取 WORKFLOW.md 定义的状态。
`operator_src_source`/`operator_src_snapshot` 在 `--source-root` 未提供或为空时为空串，
此时源码校验全程跳过。

`operator_doc_source` 可以指向项目外部，只允许读取；`operator_doc` 必须指向 run
目录内的快照，后续 Agent 只使用快照。

`current_prompt_source` 指向项目内 `prompts/operator_constraints_extract_vN.md` 基线
（v4 起为模块化基线）；`current_prompt` 指向 run 内 `inputs/prompt_v1.md` 快照。
默认（未传 `--prompt`）由 `scripts/select_prompt.py` 按算子文档特征装配基线 + 命中的
`prompts/modules/*.md` 模块写入该快照，`current_prompt_modules` 记录命中的模块名清单
（可为空）；显式 `--prompt` 为逃生口，原样复制指定文件、`current_prompt_modules=[]`。
constraint-extractor 始终只读 `current_prompt` 快照，不感知装配过程。

## constraints.json

必须满足 `agent.generators.common_model_definition.OperatorRule`。关键字段包括
operator_name、product_support、parameters 和 constraints_in_parameters。每个约束
应来自原文，不用聊天内容补充。每条约束带 `origin` 字段：`doc`（文档提取，默认）或
`source_analysis`（源码校验 patch 写入）；`origin=source_analysis` 的约束只能由
`scripts/apply_constraints_patch.py` 机械写入，source-analyst 不直接写 constraints.json。

`allowed_range_value.type=range` 的区间端点必须为实际数值，不允许用 `null` 表示
无界；单边或开区间写入 `constraints_in_parameters`，使用不等式表达。
`type=enum` 允许 `null` 作为明确的离散候选。`expr` 中允许裸 `null`，校验和求解前
会规范化为 Python `None`，但只能用于空值/存在性判断，不能参与数值大小比较。

## source_evidence.json（可选，源码启用时）

source-analyst 在每轮 EXTRACT 后产出。必含 `operator_name`、`aclnn_interfaces`、
`platform_matrix`、`hard_constraints`（每项 constraint_id/expr_type/expr/
relation_params/source_location/error_string/src_text）、`cross_check`（mismatch_overbroad/
mismatch_overnarrow）、`doc_error`（源码否决文档的条目列表，可为空）。`hard_constraints.expr_type`
必须属 `InterConstraintsRuleType` 枚举，`expr` 对齐当前提示词（v3）§6 语法。不产
诊断/预检类额外字段。`validate_artifacts.py source_evidence` 校验上述字段存在性，
quality-reviewer 读 `cross_check.mismatch_overbroad` 残留作 blocking。

## constraints_patch.json（可选，源码启用时）

source-analyst 产出的补丁建议数组，由 `scripts/apply_constraints_patch.py` 机械应用
（单轮内仅一次，失败回滚不重试）。每项 `op` 为 `add_constraint` 或 `narrow_param_range`；
`basis_type` 为 `doc_quote`（文档有但 constraints 漏）或 `source_authoritative`（源码强制
但文档无）；`origin` 为 `doc` 或 `source_analysis`。`apply_constraints_patch.py` 写回后
重跑 `OperatorRule` 校验，不通过则不写输出、返回结构化错误。`validate_artifacts.py
constraints_patch` 校验 op/basis_type/origin/proposed 取值受控。

## cases.json

JSON 数组，每项为生成器 CaseConfig 的 model_dump 结果。禁止 Agent 手工伪造。

`cases.json` 是执行前的紧凑表示。对于带 `length` 的列表类输入，只保留一个输入
描述，由执行阶段生成 `cases_expanded.json`：

- `range_values` 为标量时，表示列表中每个元素共用该取值规格；
- `range_values` 为列表且长度等于 `length` 时，表示逐元素取值规格；
- 生成阶段不得为了匹配 `length`，在 `ListVar.resolve_model()` 中把标量复制成列表。

诊断用例格式问题时必须同时检查 `cases.json` 和 `cases_expanded.json`。如果紧凑
表示已被正确展开，不能把标量 `range_values` 判为 generator_bug；如果展开过程
本身有误，应归入执行适配层的 executor_bug。

## execution_result.json

至少包含：

```json
{
  "status": "success | failed | error | timeout",
  "mode": "mock | real",
  "passed": 0,
  "failed": 0,
  "total": 0,
  "records": [],
  "engine_error": ""
}
```

必须满足 passed + failed = total。engine_error 非空时不能宣称业务成功。

## analysis.json

root_cause 只能为 constraint_extraction、generator_bug、executor_bug。每项
specific_issues 应关联 case id、日志或文档证据。

## quality_gate.json

至少包含 status、checks、blocking_issues、next_state。blocking_issues 非空时
status 必须为 blocked，主协调器不得越过门禁。

## 目录批次产物

```text
runs/batches/<batch-id>/
  batch_state.json
  batch_summary.json
```

`batch_state.json` 必须冻结 source_directory、glob、recursive、prompt、
max_iterations、case_count、mode、server_config、continue_on_error 和有序 operators。
每个 operator 包含原文档绝对路径、PENDING/RUNNING/COMPLETED 状态、单算子 run_id、
run_dir 与 terminal_state。任意时刻最多只能有一个 RUNNING 项。

`batch_summary.json` 是由批次状态确定性生成的只读汇总视图，包含 total、pending、
running、completed、success 和 failed。仅 `SUCCESS` 计入 success；`BLOCKED`、
`MAX_ITERATIONS`、`STOP_GENERATOR_BUG` 和 `STOP_EXECUTOR_BUG` 计入 failed。
