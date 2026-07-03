---
description: 编排算子约束提取、用例生成、执行、诊断和提示词优化闭环。用户要求运行或迭代算子测试流程时使用。
argument-hint: <项目内或外部算子文档路径> [--prompt path] [--max-iterations N] [--case-count N] [--mode real|mock] [--server-config path] [--batch-dir path]
---

# 算子闭环迭代

参数：`$ARGUMENTS`

先读 `docs/WORKFLOW.md` 与 `docs/ARTIFACT_CONTRACTS.md`，然后严格执行：

1. 解析参数。算子文档支持绝对路径、项目相对路径和包含 `..` 的外部相对路径。
   未传 `--prompt` 时，由 `init_run.py` 自动选择
   `prompts/operator_constraints_extract_vN.md` 中数值版本 N 最大的文件；
   max-iterations=5，case-count=10，mode=real，server-config=`servers.json`。
2. 调用 `python scripts/init_run.py` 创建 run。`--batch-dir` 是目录批次内部参数，
   不传给 `init_run.py`。该命令会把外部文档只读复制到 run 的 `inputs/` 目录，
   后续 Agent 必须使用返回的 `operator_doc_snapshot`。
   如果提供了 `--batch-dir`，创建成功后必须立刻调用
   `python scripts/batch_state.py --batch-dir <batch-dir> attach-run --run-dir <run-dir>`，
   再进入 EXTRACT；这样会话中断时目录批次可以定位并恢复该 run。
3. 若默认真实模式缺少服务器配置或配置字段不完整，立即停止并把命令返回的
   `message`、`server_config` 和 `errors` 提示给用户。不得自动切换到 mock。
   只有用户显式传入 `--mode mock` 才能执行 Mock。
4. 在主会话展示完整计划、可用 Agents、每阶段输入/输出和终止条件。
5. 每轮按顺序委派：
   - `constraint-extractor`
   - `case-generator`
   - `case-executor`（real 模式内部完成 generate→`atc-cpu-golden-derivation` 推导→real-run
     三子步骤；推导须清除 `cases_executor.py` 中的 dummy 标记并通过语法检查，否则不得进 real-run）
   - `quality-reviewer`
6. 若门禁确认全部通过，更新 run_state 为 SUCCESS 并结束。
7. 若有用例失败，委派 `failure-analyst`：
   - constraint_extraction：委派 `prompt-optimizer`，将新 prompt 送入下一轮。
   - generator_bug：状态设为 STOP_GENERATOR_BUG，停止。
   - executor_bug：状态设为 STOP_EXECUTOR_BUG，停止。
8. 达到上限后状态设为 MAX_ITERATIONS。
9. 每次委派前后都按 `CLAUDE.md` 的格式在主会话报告。所有交接必须落盘，
   不把一个 Agent 的未验证推理作为另一个 Agent 的事实。
10. 如果提供了 `--batch-dir`，本算子进入 `SUCCESS`、`BLOCKED`、`MAX_ITERATIONS`、
    `STOP_GENERATOR_BUG` 或 `STOP_EXECUTOR_BUG` 后，调用
    `python scripts/batch_state.py --batch-dir <batch-dir> complete`。如果 run 创建前即因
    文档消失等算子级问题阻断，则调用 `complete --terminal-state BLOCKED --message <原因>`。
    不得把真实执行配置缺失静默记为算子失败；目录批次初始化时应先统一校验该配置。

不要在主协调器中亲自完成专职 Agent 的工作，不要并行运行存在数据依赖的阶段。
