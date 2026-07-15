---
description: 从 constraints.json 生成 ATK JSON 或海思 TTK E2E CSV。
---

# 用例生成规范

先校验约束，再执行：

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

随后执行 `python scripts/validate_artifacts.py cases <cases.json>`。禁止手工补造生成失败
的 case。保留 `<iter-dir>/generation_summary.json` 作为数量和平台摘要。

若 `run_state.json.test_framework == "ttk"`，改为：

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <iter>/cases_ttk.csv --count <N> --test-framework ttk
python scripts/validate_artifacts.py ttk_cases <iter>/cases_ttk.csv
```

TTK 与 ATK 一样，`count` 表示每个平台请求生成的统一中间用例数；实际数量以
`generation_summary.json` 为准，禁止复制相同 baseline 凑数。TTK 必须先由正式约束生成器产生 `<iter>/cases.json`；
CSV 只是该统一中间模型的框架 adapter 产物。同时检查 `ttk_conversion_audit.json`、
`golden_manifest.json`，禁止手写 CSV 绕过 Z3 生成结果。
