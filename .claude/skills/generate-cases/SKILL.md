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

## 生成后 Python 侧约束复检（抓 Z3 伪 SAT）

Z3 对 `len(shape)`/`shape[-1]`/`shape[-2]` 等 SeqSort/ForAll 语义不完备：`solver.check()`
可能声称 sat 但实际用例违反该 expr。生成后须跑 Python 侧复检：读 `constraints_in_parameters[].expr`
与 `cases_<platform>.json`，用安全内置（`len`/`max`/`min`/`abs`/`sum`/`any`/`all`）逐 case `eval`，
expr 求值 False 即违反，落 `<iter-dir>/post_check_report.json` 供编排器判 `generator_bug`。

**命名空间约定**：每个 case 参数包成暴露 `.format`/`.dtype`/`.shape`/`.range_value` 的对象
（`__eq__` 比对 `range_value`，使 `<param> in [..]` 与 `<param> == N` 均可求值）。
**int 标量参数（`additionalDtype`/`dstFormat` 等）不得映射成裸 int**——必须同样包成该对象，
否则规范形 `additionalDtype.range_value == -1` 会 `AttributeError`（`int` 无 `.range_value`）。
约束 expr 一律用 `<param>.range_value` 引用 int 标量取值（对齐 `prompts/modules/acl_format_enum.md`
§C.4），复检命名空间须能 eval。
