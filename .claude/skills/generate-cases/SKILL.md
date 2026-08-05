---
description: 从 constraints.json 生成 ATK JSON、TTK ACLNN CSV 或 torch_npu TTK E2E CSV。
---

# 用例生成规范

先校验约束，再执行：

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <cases.json> --count <N> --test-framework atk
```

小规模任务优先以前台方式运行，tool timeout 可使用 600000 ms；该值只是单次前台等待
上限，不是生成器业务超时。多平台、大 case-count 或复杂 Z3 约束预计可能超过 10 分钟
时，允许 `run_in_background`，随后优先用 TaskOutput 阻塞等待，或用 Read 读取工具返回
的 output 文件。禁止构造 `while`/`ps`/`sleep` 轮询命令，不要通过 Bash/PowerShell
访问 Claude 的项目外临时任务目录，也不要重复启动尚未结束的同一生成任务。

生成过程中默认把每个成功用例立即写入
`<output-dir>/jsonl_checkpoints/<platform>/<operator>.jsonl` 并 flush；各平台目录隔离，
不会互相覆盖。可用 `--jsonl-save-path <dir>` 覆盖 checkpoint 根目录。

**重要：`--count` 是每个产品（platform）独立生成的数量，不是总数量。**
例如 `--count 100` 对 3 个产品会生成约 300 条用例（每个产品 100 条），
**禁止**将 count 除以产品数后再传入。脚本和 facade 内部已按 per-platform 处理，
调用方传入原始期望值即可。

随后执行 `python scripts/validate_artifacts.py cases <cases.json>`。禁止手工补造生成失败
的 case。保留 `<iter-dir>/generation_summary.json` 作为数量和平台摘要。ATK 路径下 cases
校验不通过即中断 GENERATE，不得因告警删除已生成用例或绕过校验继续。

若 `run_state.json.test_framework == "ttk"`，改为：

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <iter>/cases_ttk.csv --count <N> --test-framework ttk --hs-scenario-mode <run_state.hs_scenario_mode> --server-config servers.json
```

所有产品的 `cases_<platform>.json` 仍分别生成并保留；用于 `cases.json` 和
`cases_ttk.csv` 的 canonical 平台不再取 `product_support` 第一项，而是按
`servers.json` 中服务器顺序及各服务器 `platforms` 顺序，选择第一个已有用例桶的平台。
人工调试可用 `--platform <精确平台名>` 覆盖。选择结果和原因写入
`generation_summary.json.selected_platform/platform_selection_reason`。

torch_npu TTK 默认使用 `--hs-scenario-mode original`，完全使用原有
`agent/generators` 逻辑，不做 `tnd` / `bsnd` / `paged_attention`
场景拆分。只有用户显式选择场景拆分时才使用：

```text
--hs-scenario-mode planned
```

`planned` 才做场景拆分和投影。实际值必须从 `run_state.hs_scenario_mode`
透传；兼容旧 run，该字段缺失时使用 `original`，不得在 GENERATE 阶段重新决定。

TTK 与 ATK 一样，`count` 表示每个平台请求生成的统一中间用例数；实际数量以
`generation_summary.json` 为准，禁止复制相同 baseline 凑数。TTK 必须先由正式约束生成器产生 `<iter>/cases.json`；
CSV 只是该统一中间模型的框架 adapter 产物。同时检查 `ttk_conversion_audit.json`，
禁止手写 CSV 绕过 Z3 生成结果。`operator_family=hs` 默认不要求
`golden_manifest.json`；`operator_family=aclnn` 使用 TTK 原生 ACLNN runner，
同样不生成也不要求 E2E Golden plugin/manifest。

## 生成后诊断

`post_check_report.json` 不是必需产物，默认不创建。Z3 约束、Python 复检、
场景覆盖与 domain coverage 的问题可保留在 `generation.log`、
`generation_summary.json` 或转换 audit 中。

- **ATK**：`validate_artifacts.py cases` 不通过即阻断 GENERATE，不得降为 warning 继续。
- **TTK**：Golden 覆盖率、准确度、场景覆盖率只记 warning，不删除用例、不阻断功能流程；
  但所选执行平台 `semantically_clean_count=0` 时必须由生成器以
  `HS_SEMANTIC_GATE_FAILED` 阻断，禁止进入 TTK 转换/EXECUTE。`planned` 模式缺失其
  计划内必需场景时同样阻断。

当前正式工作流没有独立 post-check CLI，`post_check_report.json` 也不属于产物契约。
生成阶段只调用上述生成入口和 `validate_artifacts.py cases`。如以后正式增加复检，
应先实现项目入口、产物契约与测试，再更新本 Skill。

正式生成器调试日志按算子和平台分别写入
`logs/generate_case_<operator>_<platform>.log`。同一平台的分场景生成共用该平台日志，
不同平台不得混写到同一个 `generate_case_*.log`。
