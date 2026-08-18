---
description: 编排算子约束提取、用例生成、执行、诊断和提示词优化闭环。用户要求运行或迭代算子测试流程时使用。
argument-hint: <项目内或外部算子文档路径> [--src path] [--prompt path] [--supplement-constraints path] [--source-analysis-knowledge] [--max-iterations N] [--case-count N] [--mode real|mock] [--server-config path] [--operator-family auto|aclnn|hs|torch_npu] [--test-framework auto|atk|ttk|constraints] [--hs-scenario-mode original|planned] [--batch-dir path]
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
   human-checkpoint-round=3（0=禁用）；迭代到该轮仍以 constraint_extraction 失败时，
   在下一轮开始前弹人工补充检查点（AskUserQuestion 三选一）。需 `max-iterations > 该值` 才有意义。
   `hs-scenario-mode=original`；只有用户显式传入
   `--hs-scenario-mode planned` 时，torch_npu + TTK 才启用 TND/BSND/
   paged-attention 场景拆分和投影。该参数对 ACLNN/ATK 不生效。
   `--src` 可选，指定算子源码目录（项目内或外部）；未提供时可用
   `python scripts/locate_operator_source.py --aclnn-name <算子名>` 定位后再传。
   省略 `--src` 则跳过源码分析，退回纯文档驱动流程。
   `--source-analysis-knowledge` 默认关闭；显式传入时，仅对 ACLNN 自动提示词装配
   启用 `source_analysis` 类知识，并继续要求 `operator_name_eq` 精准命中。不得与
   `--prompt` 同用，也不得用于 torch_npu。
2. 调用 `python scripts/init_run.py` 创建 run（透传 `--src`、
   `--supplement-constraints`、`--source-analysis-knowledge`、`--operator-family`、`--test-framework`、
   `--hs-scenario-mode` 等参数，
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
5. `init_run.py` 成功后 state 为 `PLAN`；主协调器必须继续推进到 EXTRACT，不能仅创建 run 后结束。
   **委派 constraint-extractor 之前**，主协调器必须先把 `run_state.json` 的 `state` 推进为
   `EXTRACT`（写 `"state": "EXTRACT"`，并 append history `{"state": "EXTRACT", "at": <ISO8601>}`）。
   extract-constraints 在写 `constraints.json` 前会校验 state 已是 `EXTRACT`，未推进会被拦截并
   空跑一轮。re-EXTRACT（OPTIMIZE→EXTRACT）轮同理：委派提取器前先把 state 推进为 `EXTRACT`。

**SCENE_SCAN 子步骤**（EXTRACT 前，仅首轮；`--scene off` 跳过）：委派
`scene-scanner`。委派消息必须显式传入当前 run 的绝对路径 `<run-dir>`、只读输入
`<run-dir>/inputs/<doc>.md` 和唯一写入目标 `<run-dir>/inputs/scene_scan.json`；禁止只传
`inputs/scene_scan.json` 让子 Agent 按仓库 cwd 解析。scene-scanner 读取
`prompts/scan_scenes.md`，按**设备类型 → 量化模板 → 特性参数**三级提取，并自跑
`python scripts/validate_artifacts.py scene_scan <run-dir>/inputs/scene_scan.json`）。
完成后主协调器读 `scene_scan.json`：
- `has_scenarios=false` → 跳过（无 directive，按全场景提取，行为不变）。`scan_notes`
  含 `quant_signal_no_template` warning 时仅记录性提示用户"文档含量化参数信号但未提取到
  模板，可能遗漏剪枝"，**不**置 `has_scenarios`、**不**阻断、**不**补造场景。
- `has_scenarios=true` 且 `--scene all` → 跑
  `python scripts/render_scene_directive.py --scan <run-dir>/inputs/scene_scan.json --run-dir <run-dir> --scope all`
  （scope=all：全部设备全部模板全部特性参数取值分支全展开，不剪枝、不弹窗）。
- `has_scenarios=true` 且 `--scene auto`（默认）→ 主会话按 **Q1 → Q2 → Q3 三轮顺序
  征询**，每轮一次 AskUserQuestion 调用、其内问题并行作答（每问选项≤4，超出部分在
  question body 编号列全；支持 Other 自定义输入，Other 输入须落在已识别列表内，否则
  提示重新输入符合的）。**必须分三轮而非一次调用**：AskUserQuestion 同次调用内所有
  问题并行作答，后问拿不到前问答案；Q2 的问题集（逐设备）依赖 Q1 选中的设备、Q3 的
  问题集（逐 (设备,模板) 对）依赖 Q2 选中的模板，且预枚举全部 (设备,模板) 组合会组合
  爆炸并超每调用≤4 问上限，故只能逐级等上轮答案回来再发起下轮。
  - **Q1 设备类型**（1 个 multiSelect 问题）：选项 = `scene_scan.device_types`
    全部（≤4 个直接列全；>4 个列前 3 + Other 自定义，question body 按编号列出全部
    `device_types`，用户可按编号 Other 输入选中列表外的设备）。**不设"全部设备"聚合项**
    ——要全选就逐个勾选（multiSelect）。`device_types` 为文档"产品支持情况"具体设备名，
    **无"通用"通配符**。**若 `device_types` 仅 1 个设备 → 直接默认选中该设备、跳过 Q1
    征询，直接进 Q2**（无选择意义时不打扰用户）。question 正文首行须含 Other 提示语：
    『Other（自定义）= 按下方编号表输入列表外的设备类型名』（无"通用"通配符，须给真实设备名）。
  - **Q2 逐设备量化模板**（对 Q1 选中的每个设备各 1 个 multiSelect 问题，**批量 ≤4 问/
    次**，超出分多次调用）：选项 = 该设备 `devices[].templates` 全部（≤4 直接列全；>4
    前 3 + Other，body 编号列全）。**不设"全部模板"聚合项**——要全选就逐个勾选。各设备
    模板可不同（v3 无"通用"组，无标注内容已合并到各具体设备组下）。模板名编码量化方式
    （如 `非量化`/`全量化-A8W8`/`全量化-GQA`）。**某设备仅 1 个模板 → 自动选中该模板、
    跳过该设备 Q2**（与 Q1 单设备跳过同原则）。等 Q1 答案回来确定选中设备后再发起本轮。
    question 正文首行须含 Other 提示语：『Other（自定义）= 按下方编号表输入列表外的
    量化模板名』。
  - **Q3 逐（设备,模板）特性参数**（对 Q2 选中的每个 (device,template) 各 1 个
    **single-select** 问题，**批量 ≤4 问/次**，超出分多次调用）。每问固定 2 个预设选项 +
    Other 自定义输入（AskUserQuestion 工具契约 ≥2 选项且自动提供 Other，无法零选项）：
    - 选项 1「保持自动 / 继承文档约束（未填写）」→ 该模板 `null`（全展开不剪枝）
    - 选项 2「全部固定默认值（最小覆盖）」→ 该模板 `"fix_all_default"`（每参数取 `values[0]`）
    - **Other（可自定义输入参数特性配置）** → 接受**任意格式**输入，不限于 JSON 对象。合法示例：
      值级 JSON `{"groupType":[-1,0],"splitItem":[0,1,2,3]}`；`param=value` 串
      `groupType=-1,0; splitItem=0~3`；自然语言「groupType 取 -1 和 0，splitItem 取 0/1/2/3」。
    question 文本必须包含：(a) Other 提示语「Other（可自定义输入参数特性配置）= 贴入任意
      格式的参数取值配置，主协调器会识别并组装成取值清单；选保持自动（未填写）→ 保持自动/
      继承文档约束」；(b) 该 (device,template) **完整 feature_params 编号表**（从
      `scene_scan.json` 读出，每参数列出 `name / 取值 values / description / constraint`，
      由主协调器现场渲染，不新造脚本）；(c) 多格式示例 + 说明「未列参数=继承文档约束（全展开）；
      单值如 `[-1]`=固定该值；多值如 `[-1,0]`=展开该子集」。
    答案→selection：选项1→`null`；选项2→`"fix_all_default"`；Other→主协调器**按 scene_scan
      feature_params 表把任意格式输入识别+组装为标准 `{param:[values]}` dict**（参数名与取值
      须落在 scan 的 `values` 内、类型感知；识别不了的参数或取值当场提示用户澄清，不静默丢弃）。
      组装后的 dict 写入 `selection.json`，由 `render_scene_directive.py` 做最终严格校验（非法
      exit 2 阻断、提示重输）。等 Q2 答案
    回来确定选中 (device,template) 对后再发起本轮。
  - 汇总答案写入 `selection.json`（**值级**形态）
    `{"device_types": [<...>], "selection": {<device>: {<template>: <tpl_value>}}}`
    其中 `<tpl_value>` ∈ `null`（选项1/未填写，按文档和已选场景自动适配）| `"fix_all_default"`（选项2）|
    `{<param>: [<values>]}`（Other 任意格式，主协调器组装为该 dict：单值→fix、多值→expand 子集、未列参数→按文档和已选场景自动适配）；
    缺模板键 = 该模板未选（Q2 未选）→ 跑
    `python scripts/render_scene_directive.py --scan <run-dir>/inputs/scene_scan.json --selection <run-dir>/inputs/selection.json --run-dir <run-dir> --scope subset`
    （校验设备/模板/param 名/值 ∈ scan、解析用户明确选择参数的 `param_modes`、写
    `inputs/scene_directive.md`（含机读块
    `<!-- scene: {device_types, selection, param_modes, selection_policy} -->`，其中
    `selection` 保留逐设备选中的模板，使“保持自动”且 `param_modes` 为空时仍能机器判定场景，
    `param_modes[device][param]` ∈ `{"expand": [用户明确选择的取值子集]}` |
    `{"fix": X}`；缺键按文档和已选场景自动适配，已选场景禁止的 Optional 参数显式
    生成 `param is None`）、
    回写 `run_state.scene`；非法选择 exit 2 阻断，提示用户重选，不静默回退）。
    EXTRACT 时 constraint-extractor 读 directive 的 `device_types` 收窄 `product_support`
    （设备类型为具体设备名，直接与文档 √ 行取交集，无"通用"展开）；按 `param_modes`
    产 `allowed_range_value`（`expand` 用机读块取值清单、`fix` 单值、缺键按文档和已选
    场景适配）。已选场景禁止的 Optional 参数必须产出 `param is None`。该
    `product_support` 随后驱动 `generate_cases.py` 逐平台
    生成——**设备选择经约束提取驱动生成，不直接改生成逻辑**。
EXTRACT 调度消息须把 `inputs/scene_directive.md`（若存在）路径一并传入
constraint-extractor；轮 2+ `optimize-prompt` 重写 `prompt_vN` 不动 directive，
屏蔽跨轮稳定。

6. 每轮按顺序委派：
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
   - `case-generator`：读取 `run_state.hs_scenario_mode`，调用
     `generate_cases.py` 时原样透传 `--hs-scenario-mode`；旧 run 缺少该字段时使用
     `original`，不得自行改成 `planned`。
   - **生成的等待由主协调器负责，不由 case-generator 子 Agent 负责**（关键）：case-generator
     是子 Agent，寿命只有 ~1-2 分钟，只能用**前台** `Bash` 跑 `scripts/generation_progress.py launch`
     （~1 秒、exit 0）后报告生成子进程 `pid`/`<iter>` 路径/cases 路径/count/platforms 后**结束本轮**
     （不等待、不"等通知"、不 read-poll；详见 case-generator 与 generate-cases skill）。该 launcher 用
     `CREATE_BREAKAWAY_FROM_JOB|CREATE_NEW_PROCESS_GROUP`（Windows）/ `start_new_session=True`（POSIX）
     把 `generate_cases.py` 拉成**脱离会话 job/session 的子进程**后自身立即退出——从此无长寿命 bg 任务
     可被会话生命周期（中断/重启/上下文压缩，无 60 分钟上限）杀死；唯一长寿命进程是脱离的
     `generate_cases.py`，`scripts/probe_breakaway.py` 已证其在 launcher 退出后存活到完成。
     当 case-generator 报告"生成在脱离会话的进程里跑（pid=…）"时，主协调器**接管等待**：
       1. 优先启动一个 `Monitor`，其 command 必须是无 shell 包装的单条绝对路径命令：
          `<venv-python-absolute> <repo-absolute>/scripts/generation_progress.py watch --output-dir <iter-absolute> --interval 60`。
          `watch` 立即采样、每约 60 秒输出一行 JSON，并在 `complete`/`failed` 时自动退出；Monitor
          中断不影响已经脱离会话的生成子进程。不能使用 Monitor 时，才每 ~60 秒**前台**运行一次同样
          使用绝对路径的 `generation_progress.py status --output-dir <iter-absolute>`。
          **Monitor command 禁止包含**变量赋值/展开、`cd`、管道、命令替换、shell
          `while`/`case`/`sleep`/`grep`/`head`/`ps`；这些结构会触发非业务安全审批询问。
       2. **每次 `watch`/`status` 采样返回后必须向用户报告** `per_platform` 各平台 `done`/`total`/`elapsed`/
          `pid_alive`（`done` 递增、`pid_alive=true` 即活跃）——输出这四项是**强制**，**不得**用"平台名
          看起来不对"之类的旁支判断替换进度数字。这样用户每 ~60 秒看到一次进度，而不是长时间空白。
       2a. **轮询回合排他（强制，防进度丢失）**：`state=running` 期间，主协调器每回合的**唯一**动作
          是——跑一次 `status` → 报告进度 → 决定下一回合。**禁止**在轮询回合发起探查性
          `Read`/`Grep`/源码或平台选择调查/记忆回溯/长思考；任何旁支疑虑推迟到 `state=complete`/`failed`
          之后，或先发完本次进度、下一回合再处理，**绝不可**用调查取代一次轮询。进度展示一旦从某回合起
          长时间空白，根因几乎都是"本该轮询的回合被旁支调查/长考占用"——这是"有时有进度、有时没进度"
          的唯一可控根因，必须在本层杜绝。轮询节奏由本回合主动发起 `status` 保证，不依赖模型"想起来才轮询"。
       3. `state=running` 期间即使 `per_platform` 暂时空或残缺也是平台间过渡的正常现象
          （某平台一完成其 JSONL 即被 convert 删掉转成 `cases_<plat>.json`、从进度里"消失"），
          **绝不**据此停掉生成进程。**`per_platform` 语义（关键，防误判调查）**：running 期间 `per_platform`
          列出的是**当前正在被生成的目标平台**（`generate_platform_outputs` 按 `product_support` 顺序逐平台
          生成全部平台，每个产 `cases_<plat>.json`），**不是执行/canonical 平台**；canonical/CSV 平台是在
          生成**全部完成后**由 `_select_ttk_platform` 按 `servers.json` 服务器顺序及各 `platforms` 顺序选定的，
          与 running 期间 `per_platform` 出现哪个平台无关。故 `per_platform` 里出现 `servers.json` 未覆盖的
          平台（如 A3 训练/推理）属**正常**、不是选错平台，**禁止**据此调查 `generate_cases.py` 平台选择逻辑
          或 kill 重启——平台是否选对只在 `state=complete` 后、`generation_summary.json.selected_platform`
          不符 `servers.json` 时才处理（EXECUTE 前的事）。
       4. `state=complete`（`generation_summary.json` 已产出）后跑
          `python scripts/validate_artifacts.py cases <cases 路径>`，通过再进 EXECUTE；
          `state=failed` 读 `status` JSON 的 `error` 字段（已有界摘录）报告 `generator_bug`，不自行解析日志。
       5. **绝不在已有 `cases_<plat>.json` 时重跑 `generate_cases.py`**（`generate_platform_outputs:192`
          先 `target.unlink` 删 `cases_<plat>.json` 再生成，重跑=丢弃已完成平台数小时成果）；
          脱离进程被异常中止（部分平台有 cases、部分没有、缺 `generation_summary.json`）时
          **报告现状**让用户定夺，不自行重跑。
     （无论长短，case-generator 都只脱离启动+交棒，不前台跑长任务、不等待；主协调器统一 `status`
     轮询 + `validate_artifacts.py cases` 校验。）
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
7. 若基础产物可读、至少生成一条用例且执行器已完成运行，更新 run_state 为 SUCCESS
   并结束。Golden 覆盖率和准确度 warning 当前不作为门禁。HS+TTK 所选执行平台
   `semantically_clean_count=0`，或 `planned` 模式缺失计划内必需场景时，生成器必须
   以 `HS_SEMANTIC_GATE_FAILED` 停在 GENERATE，不得进入 EXECUTE；其他部分语义
   warning 仍按非阻断处理。
8. 若有用例失败：当 `operator_src_snapshot` 非空时，先委派 `source-analyst`
   diagnose 域（读 execution_result + uncertain-doc + source_raw，error_string
   匹配，命中的 uncertain 追加到 `inputs/supplementary-doc.md`，产
   `<iter>/source_evidence.json`），再委派 `failure-analyst`（读 source_evidence
   下根因）。`operator_src_snapshot` 为空时直接委派 `failure-analyst`。
   - **HUMAN_CHECKPOINT 门（人工补充检查点；分析完成后、自主分支决策前）**：当且仅当
     **全部**满足时触发——`analysis.json.root_cause == constraint_extraction`、
     `run_state.human_checkpoint_round > 0`、`current_iteration >= human_checkpoint_round`
     且 `current_iteration < max_iterations`（还有下一轮可用补充）、
     `human_checkpoint_resolved_iteration < current_iteration`（本轮尚未决断，防上下文压缩
     后对同一轮重复询问）。触发后：
     1. 读当前轮 `<iter>/analysis.json`（`root_cause`/`analysis` 摘要/`specific_issues`
        带 case id 与文档证据）+ 当前 `constraints.json` 摘要 + 已尝试轮次，结构化展示给用户。
     2. `AskUserQuestion` 三选一（首次触发与后续每轮重复询问用同一 prompt）：
        - **提供人工补充**：随后在主会话以文本请用户输入自由补充（思路/约束/线索）；收到后
          **append** 到 `inputs/supplement_constraints.md`，带分节标题
          `## 人工补充（第 <N> 轮失败后，<ISO8601>）`（用 markdown 分节与初始
          `--supplement-constraints` 内容区分 provenance）；置
          `run_state.human_checkpoint_resolved_iteration = current_iteration`；进下一轮
          re-EXTRACT（下轮 SUPPLEMENT 步自动拾取该补充，无需新代码）。
        - **继续自主迭代**：置 `human_checkpoint_resolved_iteration = current_iteration`，
          落回下列自主分支。
        - **立即停止**：`run_state.state = STOPPED_BY_USER`，append history
          `{"state":"STOPPED_BY_USER","at":<ISO8601>}`，终止流程（不进下一轮）。
     3. `generator_bug`/`executor_bug` 根因**不进本门**（立即止损，见下）。
   - constraint_extraction + 补充已扩充（`source_evidence.log_match` 非空，或
     failure-analyst 产了 `supplement_additions.md`，或上一步人工补充已 append 进
     `supplement_constraints.md`）：**不走 prompt-optimizer**，直接 re-EXTRACT +
     re-SUPPLEMENT + re-GENERATE + re-EXECUTE 进下一轮。
   - constraint_extraction + 补充无可提取：委派 `prompt-optimizer`，将新 prompt 送入下一轮。
   - generator_bug：状态设为 STOP_GENERATOR_BUG，停止。
   - executor_bug：状态设为 STOP_EXECUTOR_BUG，停止。
   - **轮次簿记（关键，原为缺口）**：凡分支决定"进下一轮"，主协调器须先把
     `run_state.current_iteration += 1` 并 append history `{"state":"ITER_<N>","at":<ISO8601>}`，
     随即检查 `current_iteration > max_iterations` → 置 `MAX_ITERATIONS` 终止（不再 re-EXTRACT）。
9. 达到上限（`current_iteration > max_iterations`）后状态设为 `MAX_ITERATIONS`。
10. **（终态前）分层沉淀询问**：若存在 `prompt_update_proposal.json`，先按
   `docs/PROMPT_EVOLUTION.md` 核验试验结果，再逐条展示目标 canonical 文件、摘要、
   失败/文档证据、适用范围与候选 diff，向用户询问“应用 / 暂缓 / 拒绝”。
   - 未验证或验证失败的提案默认只允许暂缓/拒绝；
   - ACLNN 提案必须归入 base/common/feature/exact operator 之一，torch_npu 只能进入
     自己的 prompt/knowledge 根；
   - 只有用户明确选择应用后，主协调器才能修改 canonical 文件；用户沉默、运行成功
     或批处理模式均不构成批准；
   - 应用后重跑 `validate_aclnn_knowledge`、路由测试（`init_run`）和
     `validate_prompt_assembly.py --record`（`build_*_prompt_base.py` 已退场归档于
     `archive/builders/`，base 直接编辑后不再 `--check`），并把决定与验证结果落入当前 run。
11. 每次委派前后都按 `CLAUDE.md` 的格式在主会话报告。所有交接必须落盘，
   不把一个 Agent 的未验证推理作为另一个 Agent 的事实。
12. 如果提供了 `--batch-dir`，本算子进入 `SUCCESS`、`BLOCKED`、`MAX_ITERATIONS`、
    `STOP_GENERATOR_BUG`、`STOP_EXECUTOR_BUG` 或 `STOPPED_BY_USER` 后，调用
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
