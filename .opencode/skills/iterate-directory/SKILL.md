---
name: iterate-directory
description: 串行扫描并迭代目录中的全部算子文档，支持失败后继续、批次汇总和中断恢复。
argument-hint: <算子文档目录> [--glob pattern] [--recursive] [--scenes "<场景描述>"] [--prompt path] [--supplement-constraints path] [--max-iterations N] [--constraint-check-rounds N] [--case-count N] [--mode real|mock] [--server-config path] [--operator-family auto|aclnn|hs|torch_npu] [--test-framework auto|atk|ttk|constraints] [--hs-scenario-mode original|planned] [--continue-on-error|--fail-fast] [--batch-dir path]
---

# 目录级算子迭代

参数：`$ARGUMENTS`

先完整读取 `iterate-operator` skill、`docs/WORKFLOW.md` 和
`docs/ARTIFACT_CONTRACTS.md`，然后严格执行：

1. 解析参数。默认 glob=`*.md`、不递归；未传 `--prompt` 时保持逐文档 family 自动
   选择：ACLNN 使用 `operator_constraints/base.md` + `knowledge/aclnn`，torch_npu 使用
   `torch_npu_constraints_extract_vN.md`，并各自装配隔离知识；
   max-iterations、constraint-check-rounds、case-count、mode、server-config、scenes 和
   hs-scenario-mode 与 `/iterate-operator` 相同；
   默认 `--continue-on-error`。
2. 新批次调用：

   ```text
   python scripts/init_batch.py <directory> [其余目录参数]
   ```

   如果用户提供 `--batch-dir`，不要重新扫描或创建批次；调用
   `python scripts/batch_state.py --batch-dir <batch-dir> show` 并恢复现有批次。
   `--batch-dir` 不能与目录扫描参数混用；`--scenes` 例外——批次尚未确认场景时，可把
   它作为恢复时的场景文本用于第 4 步 resolve。
3. 展示批次目录、文档总数、执行策略、单算子
   参数和终止条件。目录外输入只读；每个单算子 run 仍由 `/iterate-operator` 创建
   自己的输入快照。
4. **批次设备选择（强制，认领前完成）**：调用

   ```text
   python scripts/batch_state.py --batch-dir <batch-dir> claim
   ```

   - `action=needs_scene`：**不得继续认领算子**。若本次调用带了 `--scenes`，或
     claim 返回的 `scenes` 非空（`scenes_from_cli=true`），先用该文本运行下方
     resolve 做设备识别与支持性检查；文本中若还带量化场景/参数，只作为后续单算子
     run 的预填文本，**批次阶段不要求选场景**。仅当 resolve 返回
     `DEVICE_REQUIRED` / `DEVICE_AMBIGUOUS` 或用户需要补充时才询问。
     若没有可用的场景文本，用返回的 `inventory.devices` 向用户展示全部设备（设备族 +
     别名 + 支持/不支持/未列出的算子数量），并说明“批处理先确认设备，量化场景在
     每个算子文档内再选择”。使用 question 工具询问一次：
     - 选项列出每个设备，用户选设备 = 只按设备收窄；
     - 用户也可在自定义回答里直接输入完整场景文本（如
       `A2非量化groupListType等于2`），作为后续单算子 run 的场景预填；
     - 用户拒绝提供设备时确认是否取消批次，不得静默跳过场景步骤。
   - 用户确认后运行（`--scenes` 为用户原文；只选设备时用设备名）：

     ```text
     python scripts/batch_scene.py --batch-dir <batch-dir> resolve --scenes "<场景原文>" --device "<设备族>"
     ```

     若用户输入了完整设备全称，可省略 `--device` 让脚本匹配；脚本返回
     `DEVICE_REQUIRED` / `DEVICE_AMBIGUOUS` / `DEVICE_NOT_FOUND` 时把候选重新展示给
     用户，不自行猜测；返回 `NO_DEVICE_INFORMATION`（全部文档都没有可解析的设备表）
     时说明无法在批次级校验支持性，询问用户是否改用单算子 `/iterate-operator --scenes`
     或取消批次。
   - **支持性提示（强制）**：检查 resolve 返回的
     `unsupported_or_unknown_documents`、`warnings` 和逐文档 `status`：
     - `unsupported`：文档明确不支持该设备；
     - `not_listed`：文档设备表未列出该设备；
     - `partial`：同一设备族仅部分变体支持；
     - `unparsed`：文档没有可解析的设备表，无法判断。
     存在任一情况时，向用户列出算子名与原因，并询问：
     `跳过不支持的文档（推荐）` / `仍然全部执行` / `取消批次`。
     选“跳过”时重新运行 resolve 并追加 `--skip-unsupported`；选“全部执行”则保留
     原结果继续（后续失败按正常迭代处理）；选“取消”则停止批次。
   - **逐算子量化场景（强制）**：批次只确认设备；每个单算子 run 创建后，由该 run
     读取自己的文档识别**已选设备下**的量化场景并征询用户选择（见
     `iterate-operator` SCENE_SCAN 第 2 段）。用户也可在批次阶段直接给出完整场景
     文本，此时内层 run 只做匹配与回显确认；匹配不上仍回落到逐算子征询，不得跳过。
   - `action=start` / `action=resume`：设备已确认，继续认领流程。
5. 处理 `action=start`：使用 skill 工具调用 `iterate-operator`，参数为返回的
   `operator_doc_source` 与 `--batch-dir <batch-dir>`；透传 claim 返回的
   `operator_family`、`test_framework`、`scenes`（**始终透传
   `--scenes "<scenes>"`**，值为该算子文档的设备别名 + 用户场景原文；内层 run
   复用设备，并在文本未含可用量化场景时读本文档征询该设备下的场景）。仅当
   `prompt_explicit=true` 时透传
   `--prompt <prompt>`，自动模式不得把某一 family 的 baseline 当作显式 prompt
   传给另一 family；始终透传 `--hs-scenario-mode <hs_scenario_mode>`；若
   `supplement_constraints` 非空，一并透传 `--supplement-constraints <path>`；
   始终透传 claim 返回的
   `--constraint-check-rounds <constraint_check_rounds>`，使每个算子的首轮 EXTRACT 与后续
   UPDATE_CONSTRAINTS 新版本都执行同样的 check/repair 门禁；默认透传
   `--human-checkpoint-round 0`（批处理默认禁用人工补充检查点；场景征询不属于该
   检查点，仍按 SCENE_SCAN 执行）。
   - `action=resume`：若已有 `run_dir`，读取其 `run_state.json`，按
     `/iterate-operator` 的恢复协议从最后完成状态继续；若尚无 `run_dir`，按 start 处理
     （使用 claim 返回的 `scenes`）。
   - `action=complete`：停止循环并展示 `batch_summary.json`。
6. 每个算子必须串行完成，禁止同时启动多个 `/iterate-operator`。内层 skill 会在
   run 创建后关联批次，并在算子到达终态后更新批次。
7. 单算子返回后再次执行 claim。若返回 `needs_scene`（例如手工改过批次状态），回到
   第 4 步重新确认。默认策略下，`BLOCKED`、`MAX_ITERATIONS`、
   `STOP_GENERATOR_BUG` 和 `STOP_EXECUTOR_BUG` 只记入失败并继续下一个；
   `SKIPPED_UNSUPPORTED_DEVICE` 单独计入跳过，不算失败；`--fail-fast` 下批次进入
   STOPPED 后结束。
8. 所有文档处理完毕后，报告总数、成功数、失败数、跳过数，以及每个失败/跳过算子的
   终态和 run 目录。不得把“已执行完毕”表述为“全部成功”。

批次脚本只负责确定性的目录扫描、设备表解析与状态管理；不要让 Python 启动 Claude、
调用 LLM，也不要复制或绕过单算子的 Agent、质量门禁和产物校验流程。
