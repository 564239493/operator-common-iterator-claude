---
description: 使用确定性 Python 生成器从 constraints.json 生成 cases.json。
---

# 用例生成规范

先校验约束，再执行：

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <cases.json> --count <N>
```

**重要：`--count` 是每个产品（platform）独立生成的数量，不是总数量。**
例如 `--count 100` 对 3 个产品会生成约 300 条用例（每个产品 100 条），
**禁止**将 count 除以产品数后再传入。脚本和 facade 内部已按 per-platform 处理，
调用方传入原始期望值即可。

随后执行 `python scripts/validate_artifacts.py cases <cases.json>`。禁止手工补造生成失败
的 case。保留 `<iter-dir>/generation_summary.json` 作为数量和平台摘要。

