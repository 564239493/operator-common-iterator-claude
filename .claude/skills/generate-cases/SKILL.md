---
description: 使用确定性 Python 生成器从 constraints.json 生成用例（atk 走 generate_cases.py，ttk 走 constraints_to_ttk_csv.py）。
---

# 用例生成规范

先校验约束，再 Read 当前轮的 `../run_state.json` 取 `toolchain`（缺省视为 `atk`），按值分支执行。

## atk 路线（默认）

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <cases.json> --count <N>
```

生成过程中默认把每个成功用例立即写入
`<output-dir>/jsonl_checkpoints/<platform>/<operator>.jsonl` 并 flush；各平台目录隔离，
不会互相覆盖。可用 `--jsonl-save-path <dir>` 覆盖 checkpoint 根目录。

随后执行 `python scripts/validate_artifacts.py cases <cases.json>`。禁止手工补造生成失败
的 case。保留 `<iter-dir>/generation_summary.json` 作为数量和平台摘要。

## ttk 路线（`run_state.toolchain == "ttk"`）

```text
python scripts/constraints_to_ttk_csv.py --constraints <constraints.json> --output <iter-dir>/cases.csv --count <N> --iter-dir <iter-dir>
```

确定性枚举 constraints.json 的离散组合 + eval 约束 expr 过滤 + 按模板构造代表 shape，
无 LLM、无 Z3。产出每平台 `<output-dir>/cases_<platform>.csv` + `generation_summary.json`。

- **CSV 非 JSON，不跑** `validate_artifacts.py cases`；产物正确性由脚本内置（每行可回溯命中
  放行它的 expr）。
- `--count` 为每平台行数上限（0 或不传 = 穷举全部合法离散组合）；同样按 per-platform，不除以产品数。
- 保留 `<iter-dir>/generation_summary.json`（`case_format=csv`、`toolchain=ttk`）。
- **ttk 闭环止于 GENERATE**：CSV 产出成功即本轮终结，主协调器据此置 SUCCESS，
  **不**派发 case-executor / quality-reviewer；失败报告 generator_bug 候选 → STOP_GENERATOR_BUG。

## 通用（两路线共同）

**重要：`--count` 是每个产品（platform）独立生成的数量，不是总数量。**
例如 `--count 100` 对 3 个产品会生成约 300 条用例（每个产品 100 条），
**禁止**将 count 除以产品数后再传入。atk 的 facade 与 ttk 的枚举脚本内部均按
per-platform 处理，调用方传入原始期望值即可。

生成器异常时保留日志并报告 generator_bug 候选，不得伪造或手工补造用例。
