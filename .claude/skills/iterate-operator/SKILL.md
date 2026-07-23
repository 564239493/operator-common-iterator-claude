---
description: 编排算子约束提取、用例生成、执行、诊断和提示词优化闭环。用户要求运行或迭代算子测试流程时使用。
argument-hint: <项目内或外部算子文档路径> [--src path] [--prompt path] [--supplement-constraints path] [--max-iterations N] [--case-count N] [--mode real|mock] [--server-config path] [--operator-family auto|aclnn|hs|torch_npu] [--test-framework auto|atk|ttk|constraints] [--batch-dir path]
---

# 算子闭环迭代

参数：`$ARGUMENTS`

先读 `docs/WORKFLOW.md` 与 `docs/ARTIFACT_CONTRACTS.md`，然后严格执行：

1. 解析参数。算子文档支持绝对路径、项目相对路径和包含 `..` 的外部相对路径。
   `operator-family=auto`、`test-framework=auto`；未传 `--prompt` 时由
   `init_run.py` 按文档类型选择并装配最新 ACLNN prompt 或隔离的 torch_npu prompt；
   `torch_npu` 是内部 family 名 `hs` 的显式 CLI 别名。
   auto 仅对已有 TTK adapter 的六个重点算子选择 `ttk`；其余 torch_npu API 选择
   `constraints`，只运行约束提取/补充/校验，不误入必然失败的用例生成。
   max-iterations=5，case-count=10，mode=real，server-config=`servers.json`。
   `--src` 可选，指定算子源码目录（项目内或外部）；未提供时可用
   `python scripts/locate_operator_source.py --aclnn-name <算子名>` 定位后再传。
   省略 `--src` 则跳过源码分析，退回纯文档驱动流程。
2. 调用 `python scripts/init_run.py` 创建 run（透传 `--src`、
   `--supplement-constraints`、`--operator-family`、`--test-framework` 等参数，
   `--batch-dir` 是目录批次内部参数不传）。该命令把外部文档只读复制到 run 的 `inputs/` 目录，
   后续 Agent 必须使用返回的 `operator_doc_snapshot`。若传入 `--src`，把算子
   源码关键文件浅快照到 `inputs/src_snapshot/`，写入 `run_state.operator_src_snapshot`
   （为空则第 5 步跳过 source-analyst，退回纯文档驱动）。若传入
   `--supplement-constraints`，只读复制到 `inputs/supplement_constraints.md`，
   写入 `run_state.supplement_constraints`。
   如果提供了 `--batch-dir`，创建成功后必须立刻调用
   `python scripts/batch_state.py --batch-dir <batch-dir> attach-run --run-dir <run-dir>`，
   再进入 EXTRACT；这样会话中断时目录批次可以定位并恢复该 run。
3. full scope 若默认真实模式缺少服务器配置或配置字段不完整，立即停止并把命令返回的
   `message`、`server_config` 和 `errors` 提示给用户。不得自动切换到 mock。
   只有用户显式传入 `--mode mock` 才能执行 Mock。constraints-only 不执行远端，
   不要求服务器配置。
4. 在主会话展示完整计划、可用 Agents、每阶段输入/输出和终止条件。
5. `init_run.py` 成功后 state 已是 EXTRACT；必须立即委派，不能仅创建 run 后结束。
   每轮按顺序委派：
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
     **不阻塞**；full scope 继续进 `case-generator`，constraints-only 在记录提示后按
     下一条终止。用户在任意时刻回
     `inputs/conflict_resolution.json`（`[{conflict_id, winner: "source"|"doc"}]`），
     下轮 re-supplement 前运行
     `python scripts/apply_conflict_resolution.py <iter>/constraints.json --candidates <inputs>/conflict_candidates.json --resolution <inputs>/conflict_resolution.json`
     把 source-wins 并入（replace patch + revalidate）。
   - **constraints-only 终止**：若 `run_state.test_framework="constraints"`，在 EXTRACT
     和可能的 SUPPLEMENT 完成后运行 constraints normalize/validate；通过则把
     `run_state.state` 更新为 `SUCCESS`，history 记录 `CONSTRAINTS_ONLY_SUCCESS`，并明确
     报告成功范围仅为约束提取。跳过 case-generator、executor、Golden 和执行质量门禁。
   - `case-generator`
   - `case-executor`（ATK real 模式内部完成 generate→`atc-cpu-golden-derivation` 推导→real-run
     三子步骤；推导须清除 `cases_executor.py` 中的 dummy 标记并通过语法检查，否则不得进 real-run）
   - `quality-reviewer`
6. 若基础产物可读、至少生成一条用例且执行器已完成运行，更新 run_state 为 SUCCESS
   并结束。Golden 覆盖率、准确度、场景覆盖率和语义审计 warning 当前不作为门禁。
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

## 框架分流（强制）

- 每个 Agent 委派前读取 `run_state.json` 的 `operator_family` 与 `test_framework`。
- `atk`：产物为每平台 compact JSON，沿用原 ACLNN 生成和 ATK executor。
- `ttk`：先产出统一 `cases.json`，再适配为 `cases_ttk.csv`；generator 命令必须带
  `--test-framework ttk`。`operator_family=hs` 默认加载可用的自主推导或源码 Golden，
  但不以 Golden manifest 或精度结果阻塞流程；只有用户明确要求完全跳过 Golden 时
  才使用 `--no-golden`。`operator_family=aclnn` 直接走原生 `ttk aclnn`。两者均不得调用
  ATK golden 推导。
- `constraints`：只产出并校验 `constraints.json`，不调用任何 case/executor 命令；
  SUCCESS 必须注明 `run_scope=constraints_only`，不能表述成用例或精度闭环成功。
- EXTRACT 阶段与测试框架无关，任何 framework 都必须先产生非空且校验通过的
  `constraints.json`。如果 state 仍为 PLAN 或文件不存在，说明未委派提取器，不能报告
  “约束为空”。

不要在主协调器中亲自完成专职 Agent 的工作，不要并行运行存在数据依赖的阶段。
