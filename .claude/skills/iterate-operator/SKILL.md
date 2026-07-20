---
description: 编排算子约束提取、用例生成、执行、诊断和提示词优化闭环。用户要求运行或迭代算子测试流程时使用。
argument-hint: <项目内或外部算子文档路径> [--src path] [--prompt path] [--supplement-constraints path] [--max-iterations N] [--case-count N] [--mode real|mock] [--server-config path] [--batch-dir path]
---

# 算子闭环迭代

参数：`$ARGUMENTS`

先读 `docs/WORKFLOW.md` 与 `docs/ARTIFACT_CONTRACTS.md`，然后严格执行：

1. 解析参数。算子文档支持绝对路径、项目相对路径和包含 `..` 的外部相对路径。
   未传 `--prompt` 时，由 `init_run.py` 自动选择
   `prompts/operator_constraints_extract_vN.md` 中数值版本 N 最大的文件；
   max-iterations=5，case-count=10，mode=real，server-config=`servers.json`。
   `--src` 可选，指定算子源码目录（项目内或外部）；未提供时可用
   `python scripts/locate_operator_source.py --aclnn-name <算子名>` 定位后再传。
   省略 `--src` 则跳过源码分析，退回纯文档驱动流程。
2. 调用 `python scripts/init_run.py` 创建 run（透传 `--src` 等参数，`--batch-dir`
   是目录批次内部参数不传）。该命令把外部文档只读复制到 run 的 `inputs/` 目录，
   后续 Agent 必须使用返回的 `operator_doc_snapshot`。若传入 `--src`，把算子
   源码关键文件浅快照到 `inputs/src_snapshot/`，写入 `run_state.operator_src_snapshot`
   （为空则第 5 步跳过 source-analyst，退回纯文档驱动）。若传入
   `--supplement-constraints`，只读复制到 `inputs/supplement_constraints.md`，
   写入 `run_state.supplement_constraints`。
   如果提供了 `--batch-dir`，创建成功后必须立刻调用
   `python scripts/batch_state.py --batch-dir <batch-dir> attach-run --run-dir <run-dir>`，
   再进入 EXTRACT；这样会话中断时目录批次可以定位并恢复该 run。
3. 若默认真实模式缺少服务器配置或配置字段不完整，立即停止并把命令返回的
   `message`、`server_config` 和 `errors` 提示给用户。不得自动切换到 mock。
   只有用户显式传入 `--mode mock` 才能执行 Mock。
4. 在主会话展示完整计划、可用 Agents、每阶段输入/输出和终止条件。
5. 每轮按顺序委派：
   - **EXTRACT（fork-join）**：当 `run_state.operator_src_snapshot` 非空时，
     **并行**委派 `constraint-extractor`（产 `constraints.json`）与 `source-analyst`
     （extract 域：产 `<iter>/source_raw.json` + `inputs/supplementary-doc.md` +
     `inputs/uncertain-doc.md` + `inputs/conflict-doc.md` +
     `inputs/conflict_candidates.json`）；两者只读文档快照、互不写对方产物，可并行。
     barrier（两者都完成）后进补充。`operator_src_snapshot` 为空时只委派
     `constraint-extractor`，退回纯文档驱动。
   - **SUPPLEMENT**：当 `supplementary-doc.md` 或 `supplement_constraints.md`
     任一非空时，委派 `constraint-supplementer`（读两者 + `constraints.json`，产
     `constraints_patch.json`），随后运行
     `python scripts/apply_supplement_constraints.py <iter>/constraints.json <iter>/constraints_patch.json`
     （内部重跑 normalize + validate，失败则阻断，不得进 `case-generator`）。两者都
     空则跳过本步。每轮 EXTRACT 后都重新触发 source-analyst + 补充。
   - **conflict 异步提示**：若 `inputs/conflict-doc.md` 非空，主协调器输出结构化
     `requires_user_action` 提示（`code=CONFLICT_REQUIRES_REVIEW`，列出冲突条目），
     **不阻塞**，继续进 `case-generator`。用户在任意时刻回
     `inputs/conflict_resolution.json`（`[{conflict_id, winner: "source"|"doc"}]`），
     下轮 re-supplement 前运行
     `python scripts/apply_conflict_resolution.py <iter>/constraints.json --candidates <inputs>/conflict_candidates.json --resolution <inputs>/conflict_resolution.json`
     把 source-wins 并入（replace patch + revalidate）。
   - `case-generator`
   - `case-executor`（real 模式内部完成 generate→`atc-cpu-golden-derivation` 推导→real-run
     三子步骤；推导须清除 `cases_executor.py` 中的 dummy 标记并通过语法检查，否则不得进 real-run）
   - `quality-reviewer`
6. 若门禁确认全部通过，更新 run_state 为 SUCCESS 并结束。
7. 若有用例失败：当 `operator_src_snapshot` 非空时，先委派 `source-analyst`
   diagnose 域（读 execution_result + uncertain-doc + source_raw，error_string
   匹配，命中的 uncertain 追加到 `inputs/supplementary-doc.md`，产
   `<iter>/source_evidence.json`），再委派 `failure-analyst`（读 source_evidence
   下根因）。`operator_src_snapshot` 为空时直接委派 `failure-analyst`。
   - constraint_extraction + 补充已扩充（`source_evidence.log_match` 非空，或
     failure-analyst 产了 `supplement_additions.md`）：**不走 prompt-optimizer**，
     直接 re-EXTRACT + re-SUPPLEMENT + re-GENERATE + re-EXECUTE 进下一轮。
   - constraint_extraction + 补充无可提取：委派 `prompt-optimizer`，将新 prompt
     送入下一轮。
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
