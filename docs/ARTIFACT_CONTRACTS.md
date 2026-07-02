# 运行产物契约

## 目录

```text
runs/<operator>-<timestamp>/
  run_state.json
  inputs/
    <原算子文档文件名>.md
    prompt_v1.md
  iter_001/
    constraints.json
    generation_summary.json
    cases.json
    execution_result.json
    quality_gate.json
    analysis.json
    prompt_v2.md
    prompt_changes_v2.md
```

## run_state.json

必须包含 `run_id`、`operator_doc_source`、`operator_doc`、`current_prompt_source`、
`current_prompt`、`mode`、`server_config`、`max_iterations`、
`case_count`、`current_iteration`、`state`、`history` 和时间戳。state 只能取
WORKFLOW.md 定义的状态。

`operator_doc_source` 可以指向项目外部，只允许读取；`operator_doc` 必须指向 run
目录内的快照，后续 Agent 只使用快照。

## constraints.json

必须满足 `agent.generators.common_model_definition.OperatorRule`。关键字段包括
operator_name、product_support、parameters 和 constraints_in_parameters。每个约束
应来自原文，不用聊天内容补充。

`allowed_range_value.type=range` 的区间端点必须为实际数值，不允许用 `null` 表示
无界；单边或开区间写入 `constraints_in_parameters`，使用不等式表达。
`type=enum` 允许 `null` 作为明确的离散候选。`expr` 中允许裸 `null`，校验和求解前
会规范化为 Python `None`，但只能用于空值/存在性判断，不能参与数值大小比较。

## cases.json

JSON 数组，每项为生成器 CaseConfig 的 model_dump 结果。禁止 Agent 手工伪造。

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
