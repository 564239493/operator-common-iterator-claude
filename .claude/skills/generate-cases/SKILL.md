---
description: 从 constraints.json 生成 ATK JSON、TTK ACLNN CSV 或 torch_npu TTK E2E CSV。
---

# 用例生成规范

读取约束后直接执行：

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <cases.json> --count <N> --test-framework atk
```

生成过程中默认把每个成功用例立即写入
`<output-dir>/jsonl_checkpoints/<platform>/<operator>.jsonl` 并 flush；各平台目录隔离，
不会互相覆盖。可用 `--jsonl-save-path <dir>` 覆盖 checkpoint 根目录。

**重要：`--count` 是每个产品（platform）独立生成的数量，不是总数量。**
例如 `--count 100` 对 3 个产品会生成约 300 条用例（每个产品 100 条），
**禁止**将 count 除以产品数后再传入。脚本和 facade 内部已按 per-platform 处理，
调用方传入原始期望值即可。

保留 `<iter-dir>/generation_summary.json` 作为数量和平台摘要。校验器产出的
问题可写入摘要/审计，但不得因语义或覆盖类告警删除用例或中断执行。

若 `run_state.json.test_framework == "ttk"`，改为：

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <iter>/cases_ttk.csv --count <N> --test-framework ttk --server-config servers.json
```

所有产品的 `cases_<platform>.json` 仍分别生成并保留；用于 `cases.json` 和
`cases_ttk.csv` 的 canonical 平台不再取 `product_support` 第一项，而是按
`servers.json` 中服务器顺序及各服务器 `platforms` 顺序，选择第一个已有用例桶的平台。
人工调试可用 `--platform <精确平台名>` 覆盖。选择结果和原因写入
`generation_summary.json.selected_platform/platform_selection_reason`。

torch_npu TTK 默认使用 `--hs-scenario-mode planned`。当用户明确要求不做
`tnd` / `bsnd` / `paged_attention` 场景拆分、完全使用原有
`agent/generators` 逻辑时，在生成命令追加：

```text
--hs-scenario-mode original
```

`original` 不做场景拆分和投影。HS 语义、场景覆盖和 TTK 转换审计仅作
诊断记录，不阻断用例产出。

TTK 与 ATK 一样，`count` 表示每个平台请求生成的统一中间用例数；实际数量以
`generation_summary.json` 为准，禁止复制相同 baseline 凑数。TTK 必须先由正式约束生成器产生 `<iter>/cases.json`；
CSV 只是该统一中间模型的框架 adapter 产物。同时检查 `ttk_conversion_audit.json`，
禁止手写 CSV 绕过 Z3 生成结果。`operator_family=hs` 默认不要求
`golden_manifest.json`；`operator_family=aclnn` 使用 TTK 原生 ACLNN runner，
同样不生成也不要求 E2E Golden plugin/manifest。

## 生成后诊断

`post_check_report.json` 不是必需产物，默认不创建。Z3 约束、Python 复检、
场景覆盖与 domain coverage 的问题可保留在 `generation.log`、
`generation_summary.json` 或转换 audit 中，但不得作为删除 case/拒绝执行的门禁。

正式生成器调试日志按算子和平台分别写入
`logs/generate_case_<operator>_<platform>.log`。同一平台的分场景生成共用该平台日志，
不同平台不得混写到同一个 `generate_case_*.log`。
