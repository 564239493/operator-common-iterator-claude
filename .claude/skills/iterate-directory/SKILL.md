---
description: 串行扫描并迭代目录中的全部算子文档，支持失败后继续、批次汇总和中断恢复。
argument-hint: <算子文档目录> [--glob pattern] [--recursive] [--prompt path] [--max-iterations N] [--case-count N] [--mode real|mock] [--server-config path] [--continue-on-error|--fail-fast] [--batch-dir path]
---

# 目录级算子迭代

参数：`$ARGUMENTS`

先完整读取 `iterate-operator` Skill、`docs/WORKFLOW.md` 和
`docs/ARTIFACT_CONTRACTS.md`，然后严格执行：

1. 解析参数。默认 glob=`*.md`、不递归；未传 `--prompt` 时自动选择
   `prompts/operator_constraints_extract_vN.md` 中数值版本 N 最大的文件；
   max-iterations、case-count、mode 和 server-config 与 `/iterate-operator` 相同；
   默认 `--continue-on-error`。
2. 新批次调用：

   ```text
   python scripts/init_batch.py <directory> [其余目录参数]
   ```

   如果用户提供 `--batch-dir`，不要重新扫描或创建批次；调用
   `python scripts/batch_state.py --batch-dir <batch-dir> show` 并恢复现有批次。
   `--batch-dir` 不能与目录扫描参数混用。
3. 展示批次目录、文档总数、执行策略、单算子参数和终止条件。目录外输入只读；
   每个单算子 run 仍由 `/iterate-operator` 创建自己的输入快照。
4. 调用以下命令认领工作：

   ```text
   python scripts/batch_state.py --batch-dir <batch-dir> claim
   ```

   - `action=start`：使用 Skill 工具调用 `iterate-operator`，参数为返回的
     `operator_doc_source`、批次冻结的单算子参数，以及 `--batch-dir <batch-dir>`。
   - `action=resume`：若已有 `run_dir`，读取其 `run_state.json`，按
     `/iterate-operator` 的恢复协议从最后完成状态继续；若尚无 `run_dir`，按 start 处理。
   - `action=complete`：停止循环并展示 `batch_summary.json`。
5. 每个算子必须串行完成，禁止同时启动多个 `/iterate-operator`。内层 Skill 会在
   run 创建后关联批次，并在算子到达终态后更新批次。
6. 单算子返回后再次执行 claim。默认策略下，`BLOCKED`、`MAX_ITERATIONS`、
   `STOP_GENERATOR_BUG` 和 `STOP_EXECUTOR_BUG` 只记入失败并继续下一个；
   `--fail-fast` 下批次进入 STOPPED 后结束。
7. 所有文档处理完毕后，报告总数、成功数、失败数，以及每个失败算子的终态和 run
   目录。不得把“已执行完毕”表述为“全部成功”。

批次脚本只负责确定性的目录扫描与状态管理；不要让 Python 启动 Claude、调用 LLM，
也不要复制或绕过单算子的 Agent、质量门禁和产物校验流程。
