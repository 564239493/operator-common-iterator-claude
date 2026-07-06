---
description: 对单轮 constraints、cases、execution 和 analysis 产物执行独立质量门禁。
---

# 质量门禁

调用 `scripts/validate_artifacts.py` 分别校验已存在的阶段产物，再核对：

- constraints 中所有非空 expr 通过规范化后的 Python AST 校验；
- `allowed_range_value.type=range` 不含 `null` 边界，`type=enum` 可包含 `null`；
- 数值范围 expr 使用不等式而不是 `.range_value in [[min, max]]`；
- cases 数量与 generation_summary 一致；
- passed + failed = total；
- execution records 中 case id 可回溯到 cases；
- analysis 的根因属于固定枚举；
- 下一状态与根因/通过统计一致。
- 一段式算子（`is_single_function_mode=true` 或 `function_signature` 不含 `GetWorkspaceSize`）合法；其 `outputs` 可含标量指针输出（`type` 为 `uint64_t`/`int64_t` 等、`format=N/A`、`dimensions=[]`），不得判为“缺失 GetWorkspaceSize”或误标框架参数。

real 模式额外追加 CPU golden 推导门禁：对 `iter_dir/cases_executor.py` 运行
`python scripts/validate_artifacts.py executor <iter>/cases_executor.py`，命中
`_dummy_output` / `# [FALLBACK]` / `# TODO: CPU_GOLDEN` 任一标记或语法错误 →
`blocking_issues` 非空。dummy 残留说明 `atc-cpu-golden-derivation` skill 未真正执行
或未生效，real 上传的会是 `torch.ones` 假参考，passed/failed 无精度语义，必须阻断。

写入 quality_gate.json。任何 blocking_issues 非空时 status 必须为 blocked。
质量门禁只确认阻断事实，不得跳过 failure-analyst 直接把表达式解析失败判成
`generator_bug`。约束语义或表达式有误时，next_state 应进入 DIAGNOSE，由
failure-analyst 判定是否为 `constraint_extraction`。
