---
description: 使用确定性 Python 生成器从 constraints.json 生成 cases.json。
---

# 用例生成规范

先校验约束，再执行：

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <cases.json> --count <N>
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
的 case。保留 `<iter-dir>/generation_summary.json` 作为数量和平台摘要。

当前正式工作流没有独立 post-check CLI，`post_check_report.json` 也不属于产物契约。
生成阶段只调用上述生成入口和 `validate_artifacts.py cases`。如以后正式增加复检，
应先实现项目入口、产物契约与测试，再更新本 Skill。
