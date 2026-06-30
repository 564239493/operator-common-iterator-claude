---
description: 对单轮 constraints、cases、execution 和 analysis 产物执行独立质量门禁。
---

# 质量门禁

调用 `scripts/validate_artifacts.py` 分别校验已存在的阶段产物，再核对：

- cases 数量与 generation_summary 一致；
- passed + failed = total；
- execution records 中 case id 可回溯到 cases；
- analysis 的根因属于固定枚举；
- 下一状态与根因/通过统计一致。

写入 quality_gate.json。任何 blocking_issues 非空时 status 必须为 blocked。

