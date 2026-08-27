---
description: 对单轮 constraints、constraint_check、cases、execution 和 analysis 产物执行独立质量门禁。
---

# 质量门禁

调用 `scripts/validate_artifacts.py` 分别校验已存在的阶段产物，再核对：

- 当前 `<iter>/constraint_check.json` 存在并通过
  `python scripts/validate_artifacts.py constraint_check <report>`；其 `iteration` 必须等于
  `run_state.current_iteration`、`max_rounds` 必须等于
  `run_state.constraint_check.max_rounds`、`status` 必须为 `passed`。缺失、失配或未通过
  都是 blocking issue，不能用后续用例执行成功绕过；
- constraints 中所有非空 expr 通过规范化后的 Python AST 校验；
- `allowed_range_value.type=range` 不含 `null` 边界，`type=enum` 可包含 `null`；
- 数值范围 expr 使用不等式而不是 `.range_value in [[min, max]]`；
- cases 数量与 generation_summary 一致；
- passed + failed = total；
- execution records 中 case id 可回溯到 cases；
- analysis 的根因属于固定枚举；
- 下一状态与根因/通过统计一致。
- 一段式算子（`function_signature` 不含 `GetWorkspaceSize`）合法；其 `outputs` 可含标量指针输出（`type` 为 `uint64_t`/`int64_t` 等、`format=N/A`、`dimensions=[]`），不得判为"缺失 GetWorkspaceSize"或误标框架参数。`is_single_function_mode` 字段已废弃，命中即阻断。

real 模式额外追加 CPU golden 推导门禁：对 `iter_dir/cases_executor.py` 运行
`python scripts/validate_artifacts.py executor <iter>/cases_executor.py`，命中
`_dummy_output` / `# [FALLBACK]` / `# TODO: CPU_GOLDEN` 任一标记或语法错误 →
`blocking_issues` 非空。dummy 拋留说明 `atc-cpu-golden-derivation` skill 未真正执行
或未生效，real 上传的会是 `torch.ones` 假参考，passed/failed 无精度语义，必须阻断。

写入 quality_gate.json。任何 blocking_issues 非空时 status 必须为 blocked。
质量门禁只确认阻断事实，不得跳过 failure-analyst 直接把表达式解析失败判成
`generator_bug`。约束语义或表达式有误时，next_state 应进入 DIAGNOSE，由
failure-analyst 判定是否为 `constraint_extraction`。

## TTK 例外（`test_framework == "ttk"`）

TTK 路径下，以下检查降级为非阻断诊断（写入 `checks[].warnings`，不使 status=blocked、
不删除已生成用例、不阻止进入执行）：

- Golden 覆盖率与准确度：TTK E2E 可使用现有自主推导或源码 Golden，`golden_manifest.json`
  不作为门禁，精度失败只记录；
- ACLNN 经 TTK 原生 runner 执行时同样不要求 E2E Golden plugin/manifest。

但下列仍为 TTK 路径的**阻断**项（基础可运行性）：

- 必需产物（`cases_ttk.csv` / `constraints.json` / `constraint_check.json`）不存在或不可读；
- 没有任何可执行用例；
- CSV/JSON 缺少执行器定位 API 所需的基础字段；
- 执行器自身报错，导致用例没有实际运行。

TTK 仅完成 command preparation、尚无 NPU 执行结果时，next_state 为 EXECUTE（等待真实
运行），不记为质量失败。读取 `run_state.json.test_framework` 区分 ATK/TTK：ATK 校验
`cases.json` 与标准执行统计；TTK 校验 `cases_ttk.csv`。
