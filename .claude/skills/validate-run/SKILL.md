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

real 模式额外追加 CPU golden 推导门禁：对 `iter_dir/cases_executor.py` 运行
`python scripts/validate_artifacts.py executor <iter>/cases_executor.py`，命中
`_dummy_output` / `# [FALLBACK]` / `# TODO: CPU_GOLDEN` 任一标记或语法错误 →
`blocking_issues` 非空。dummy 残留说明 `atc-cpu-golden-derivation` skill 未真正执行
或未生效，real 上传的会是 `torch.ones` 假参考，passed/failed 无精度语义，必须阻断。

写入 quality_gate.json。任何 blocking_issues 非空时 status 必须为 blocked。

