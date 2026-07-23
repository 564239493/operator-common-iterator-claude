---
description: 对单轮 constraints、cases、execution 和 analysis 产物执行非阻塞基础检查。
---

# 基础可运行性检查

当前目标是先生成并执行基础用例。只把下列情况作为 blocking issue：

- 必需产物不存在或不可读；
- 没有任何可执行用例；
- CSV/JSON 缺少执行器定位 API 所需的基础字段；
- 执行器自身报错，导致用例没有实际运行。

约束 AST、参数语义、场景覆盖率、Golden 覆盖率、准确度、统计一致性和记录回溯等
检查当前均为可选诊断；即使发现问题也只写入 `checks[].warnings`，不得删除已生成用例、
不得阻止进入执行。TTK E2E 可使用现有 Golden，但精度失败不阻塞；ACLNN 不要求 Golden。

可以写入 `quality_gate.json` 记录检查结果，但它不是生成或执行的前置产物。只有上述
基础可运行性错误使 `blocking_issues` 非空；其他 warning 不改变下一状态。
