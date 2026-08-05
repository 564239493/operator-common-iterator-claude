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

**SCENE_SCAN 子步骤**（EXTRACT 前，仅首轮；`--scene off` 跳过）：委派
`scene-scanner`（读 `inputs/<doc>.md` + `prompts/scan_scenes.md`，产
`inputs/scene_scan.json`，自跑 `python scripts/validate_artifacts.py scene_scan
inputs/scene_scan.json`）。完成后主协调器读 `scene_scan.json`：
- `has_quant_scenarios=false` → 跳过（无 directive，按全场景提取，行为不变）；
- `has_quant_scenarios=true` 且 `--scene all` → 跑
  `python scripts/render_scene_directive.py --scan <scene_scan.json> --run-dir <run-dir> --scope all`
  （scope=all，不剪枝、不弹窗）；
- `has_quant_scenarios=true` 且 `--scene auto`（默认）→ 主会话用 AskUserQuestion
  两段征询（**均单选**）：Q1 量化方式（single-select，选项=`scene_scan.quant_modes`，
  选了伪量化就不能选量化）→ 据 Q1 答案取所选方式的位宽列表（`quant_widths_by_mode`
  或 `valid_combos` 中该方式的 width 并集）→ Q2 位宽（single-select；列表为空如纯非量化
  或该方式无位宽细分则跳过 Q2，`quant_width=null`）→ 把单选答案写入 `selection.json`
  为 `{"quant_mode": <Q1>, "quant_width": <Q2 或 null>}`（单值）→ 跑
  `python scripts/render_scene_directive.py --scan <scene_scan.json> --selection <selection.json> --run-dir <run-dir> --scope subset`
  （校验选定 (mode,width) ∈ scan、算 valid_combos、写 `inputs/scene_directive.md`、回写
  `run_state.scene`；非法选择 exit 2 阻断，提示用户重选，不静默回退）。
EXTRACT 调度消息须把 `inputs/scene_directive.md`（若存在）路径一并传入
constraint-extractor；轮 2+ `optimize-prompt` 重写 `prompt_vN` 不动 directive，
屏蔽跨轮稳定。

5. 每轮按顺序委派。所有 Agent 均使用当前共享工作树，调用时禁止
   `isolation: worktree`，确保当前 run 的阶段产物可直接交接：
   - **EXTRACT（fork-join）**：当 `run_state.operator_src_snapshot` 非空时，
     **并行**委派 `constraint-extractor`（产 `constraints.json`）与 `source-analyst`
     （extract 域：产 `<iter>/source_raw.json` + `inputs/supplementary-doc.md` +
     `inputs/uncertain-doc.md` + `inputs/conflict-doc.md` +
     `inputs/conflict_candidates.json`）；两者只读文档快照、互不写对方产物，可并行。
     barrier（两者都完成）后进补充。`operator_src_snapshot` 为空时只委派
     `constraint-extractor`，退回纯文档驱动。
   - **CLASSIFY（EXTRACT barrier 后）**：主协调器跑
     `python scripts/classify_operator.py --doc <run>/inputs/<doc>.md`，读
     stdout JSON（`operator_category` + `evidence`），回写 `run_state.json` 的
     `execution_strategy`（`fusion_comm_compute` → `fusion`，否则 `default`）、
     `operator_category`、`operator_category_evidence`。分类不进 constraints.json、
     不依赖 constraint-extractor 自由文本。此步每轮 EXTRACT 后都执行（覆盖上轮分类）。
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
   - `case-executor`：
     - **default**（`run_state.execution_strategy != "fusion"`）：real 模式内部完成
       generate→`atc-cpu-golden-derivation` 推导→real-run 三子步骤；推导须清除
       `cases_executor.py` 中的 dummy 标记并通过语法检查，否则不得进 real-run。
     - **fusion**（`run_state.execution_strategy == "fusion"`）：先读 `run_state.json`
       取 `execution_strategy` 确认策略；generate 子步骤不变；**跳过** CPU golden
       推导（fusion 走 `_SPECIAL_TEMPLATES` 专属 `.tpl`，已是真实实现，无 dummy 标记，
       skill 天然无操作）；real-run 替换为 4 步流程（CPU 标杆→NPU 级联标杆→改名→
       精度对比），拼 `execute_cases.py --mode real --strategy fusion --num <case_count>`
       透传策略与用例数。精度对比结果记录性、不入成败；路径门禁失败写
       `engine_error` 终止流程。
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
9. **（终态前）提示词版本提升询问**：当本 run 内任意 iter 目录含
   `prompt_changes_v*.md` 时，主协调器在进入第 9 步报告格式前，按下列流程
   处理：

   1. 对每个 `iter_*/prompt_changes_v*.md`，定"是否有执行证据"：
      `iter_*/execution_result.json.total > 0 and .passed > 0` 即"初步有效"；
      否则视为"无验证"。
   2. 至少一个 `prompt_changes` 通过有效性门槛时，主协调器在主会话输出每条候选
      的**摘要表 + 有效性凭证段**（直接从 `prompt_changes` 读出 §1 / §3 段），
      然后调用 `AskUserQuestion` 询问用户，对**每条候选**给出 3 选项：
      - **提升到 prompts/ 全局基线**：调
        `python scripts/promote_prompt.py --from runs/<id>/iter_<N+1>/prompt_v<N+1>.md \
        --to-version <N+1> --run-dir runs/<id> \
        --changes runs/<id>/iter_<N+1>/prompt_changes_v<N+1>.md`；
      - **查看完整 diff 再决定**：把该 iter 的 `prompt_changes` 全文贴给用户，等下一步指示；
      - **本轮不提升**：仅保留在 iter 目录，不写 `prompts/`。

      未通过有效性门槛的候选**也**列出，但选项文案相应弱化为"未验证版本 · 默认推荐不提升"。
      用户可对不同候选独立选择不同动作。
   3. 用户选择"提升"分支时，主协调器调 `promote_prompt.py`；该脚本原子写
      `prompts/operator_constraints_extract_v<N+1>.md` 与 `prompts/CHANGELOG.md`
      并落 `iter_<N+1>/promotion_record.json`。
   4. **门卫未动**：`promote_prompt.py` 由主会话在 run 终态调用，无 active scope 绑定；
      `guard_project_writes.py` 与 `.claude/settings.json` 不允许 Agent 在 task 活跃期内
      自行写 `prompts/`——本步骤是用户显式批准的人工触发，不视为 Agent 越权。

   详见 `.claude/skills/optimize-prompt/SKILL.md` §5 与 `scripts/promote_prompt.py`
   的契约。
10. 每次委派前后都按 `CLAUDE.md` 的格式在主会话报告。所有交接必须落盘，
    不把一个 Agent 的未验证推理作为另一个 Agent 的事实。
11. 如果提供了 `--batch-dir`，本算子进入 `SUCCESS`、`BLOCKED`、`MAX_ITERATIONS`、
    `STOP_GENERATOR_BUG` 或 `STOP_EXECUTOR_BUG` 后，调用
    `python scripts/batch_state.py --batch-dir <batch-dir> complete`。如果 run 创建前即因
    文档消失等算子级问题阻断，则调用 `complete --terminal-state BLOCKED --message <原因>`。
    不得把真实执行配置缺失静默记为算子失败；目录批次初始化时应先统一校验该配置。

不要在主协调器中亲自完成专职 Agent 的工作，不要并行运行存在数据依赖的阶段。
