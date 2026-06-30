---
description: 使用确定性 Python 生成器从 constraints.json 生成 cases.json。
---

# 用例生成规范

先校验约束，再执行：

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <cases.json> --count <N>
```

随后执行 `python scripts/validate_artifacts.py cases <cases.json>`。禁止手工补造生成失败
的 case。保留 `<iter-dir>/generation_summary.json` 作为数量和平台摘要。

