# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本项目是 CANN 算子迭代测试的 Claude Code CLI 原生编排器。Claude Code 是顶层运行时；
Python 只承担确定性业务（校验、用例生成、执行适配、调度留痕），不再嵌套调用 LLM。

## 核心流程

状态机：`PLAN → EXTRACT → GENERATE → EXECUTE → GATE`
- 每次 EXTRACT（含 re-EXTRACT）内部固定执行：完整提取 → 可选 SUPPLEMENT/冲突合并 →
  独立 `constraint-checker` / `constraint-repairer` 语义检查修复循环；默认
  `--constraint-check-rounds 3`，通过可提前结束，达到上限仍有问题 → `BLOCKED`
- 全部通过 → `SUCCESS`
- 有失败 → `DIAGNOSE`；只有根因为 `constraint_extraction` 时才进入 `OPTIMIZE → EXTRACT` 循环
- `generator_bug` / `executor_bug` → 立即止损
- 达到 max-iterations → `MAX_ITERATIONS`
- `constraint_extraction` 迭代到 `--human-checkpoint-round`（默认 3，0=禁用）轮仍失败时，下一轮
  开始前弹人工补充检查点（AskUserQuestion 三选一：人工补充 / 自主迭代 / 立即停止）；
  人工补充 append 进 `inputs/supplement_constraints.md` 复用 SUPPLEMENT 管线；"立即停止" → `STOPPED_BY_USER`

每轮产物只通过 `runs/<run-id>/` 下的文件交接，禁止跨 Agent 的隐式上下文污染。

> EXTRACT 前可选触发场景扫描（`--scene auto` 默认；`all`/`off` 可选）：
> `scene-scanner` 读文档按**设备类型 → 量化模板 → 特性参数**三级提取（**不设"通用"组**，
> 无设备标注内容合并到每个具体设备组下；特性参数只提取枚举/分档可选项），产
> `<run-dir>/inputs/scene_scan.json`（委派时必须把 `<run-dir>` 绝对路径和该唯一写入目标
> 显式传给 scene-scanner，禁止按仓库 cwd 解析相对 `inputs/`）；主协调器据此 AskUserQuestion
> **Q1→Q2→Q3 三轮顺序征询**（每轮一次调用，必须分三轮：Q2 问题集依赖 Q1 选中设备、
> Q3 问题集依赖 Q2 选中模板，同调用内并行作答拿不到前轮答案）——Q1 设备类型
> （multiSelect 真实设备、不设"全部设备"聚合项；`device_types` 仅 1 个时直接默认选中、
> 跳过 Q1 进 Q2）→ Q2 逐设备量化模板（multiSelect 真实模板、不设"全部模板"聚合项；
> 某设备仅 1 模板自动选中、跳过该设备 Q2）→ Q3 逐（设备,模板）特性参数（**single-select**，
> 每问 2 预设 + Other：选项1「保持自动/继承文档约束（未填写）」→`null`、选项2「全部固定
> 默认值」→`"fix_all_default"`、Other（可自定义输入参数特性配置）→**任意格式**输入
> （值级 JSON / `param=value` 串 / 自然语言均可，如 `groupType=-1,0; splitItem=0~3`），
> 主协调器按 scene_scan feature_params 表识别+组装为标准 `{param:[values]}` dict 写入
> `selection.json`（识别不了的参数/取值当场提示用户澄清）；单值→fix、多值→expand 子集、
> 未列参数→按文档和已选场景自动适配；question 文本含完整 feature_params 编号表 + Other 提示语「Other
> （可自定义输入参数特性配置）= 贴入任意格式配置，主协调器识别组装；选保持自动（未填写）
> → 保持自动/继承文档约束」），`scripts/render_scene_directive.py` 做最终严格校验、
> 解析用户明确选择的参数为每设备每参数的 `param_modes`（`{"expand": [取值清单]}`
> 清单=用户明确选择的子集 / `{"fix": X}` 单值=
> 用户单值输入或 values[0]）；缺键参数继续按算子文档和已选场景提取适配，已选场景
> 明确禁止的 Optional 参数必须显式生成 `param is None`。随后渲染
> `inputs/scene_directive.md`（含机读块 `device_types`/`selection`/`param_modes`/`selection_policy`；
> `selection` 保留逐设备选中的模板，避免“保持自动”时场景信息丢失）
> 并回写 `run_state.scene`，constraint-extractor 据此按 `param_modes` 收窄并按
> `device_types` 收窄 `product_support`——设备类型为"产品支持情况"具体设备名，
> 直接与 √ 行取交集（无"通用"展开）；`product_support` 随后驱动用例生成
> （`generate_cases.py` 按 `product_support` 逐平台生成，不读场景）。为独立子步骤而非
> 新状态，文档无场景即跳过（零回归）；仅有量化参数信号而未提取到模板时只写
> `scan_notes`（`quant_signal_no_template`）警告，不补造。

## 常用命令

### 算子迭代
```text
/iterate-operator operator_docs/aclnnFoo.md --max-iterations 3 --case-count 10
/iterate-operator operator_docs/aclnnFoo.md --constraint-check-rounds 3  # 每个 EXTRACT 内最多 3 次语义 check
/iterate-operator operator_docs/aclnnFoo.md --max-iterations 5 --human-checkpoint-round 3  # 第3轮仍失败弹人工补充检查点（0=禁用）
/iterate-operator D:\operator_docs\aclnnFoo.md  # 支持项目外路径
/iterate-operator operator_docs/aclnnFoo.md --scene auto  # EXTRACT 前扫描量化场景并征询（默认）；--scene all 取全场景不问；--scene off 跳过
/iterate-directory operator_docs --max-iterations 3  # 串行执行目录中全部算子
/iterate-directory --batch-dir runs/batches/<batch-id>  # 恢复中断的批次
/show-workforce  # 查看可用 Skills、Agents 和调度拓扑
```

### 确定性 Python 工具（scripts/）

所有脚本从项目根目录执行，Python 需先激活 `.venv`：

> Agent 使用 Bash/PowerShell 工具时禁止执行 `source`、`activate` 或
> `Activate.ps1`。直接调用虚拟环境解释器：Windows 使用
> `.venv/Scripts/python.exe`，Linux/macOS/WSL2 使用 `.venv/bin/python`。
> 下面的激活命令仅供用户在交互终端准备环境。
>
> Agent 禁止使用 `python -c`、`python -` 或临时内联代码；应直接运行项目内已有
> `.py` 入口（不限于 `scripts/`，受保护目录中的脚本也允许执行）。文件内容与路径检查
> 优先使用 Read/Glob/Grep；Shell 工具已返回 exit code，无需追加状态探针。
> Agent 也不得在 `runs/<current-run>/` 中生成一次性辅助 `.py`（例如
> `gen_constraints.py`、`check_*.py`）再执行或删除；结构化产物直接通过 Write/Edit 落盘，
> 确定性处理只调用项目已有正式入口。确实缺少通用能力时，应作为独立开发任务新增并
> 审查正式项目脚本，而不是在运行任务中临时造脚本。
> Agent 在迭代任务中禁止执行 `pip install`、`python -m pip`、`uv add` 或其他依赖
> 安装/升级命令。出现 `ModuleNotFoundError` 时停止当前阶段，报告缺失模块和失败命令；
> 依赖变更只能由用户在环境准备或显式维护任务中决定。不得根据猜测安装依赖。
>
> `init_run.py` 已创建首轮目录。后续轮次只在当前 run 中创建目录，路径由 Hook 校验。
>
> 迭代用 Python CLI 的短任务优先前台运行并设置足够的 tool timeout。已知可能超过
> 前台上限的长任务可以使用 `run_in_background`，但只能通过 TaskOutput 阻塞等待或
> Read 读取工具返回的 output 文件；禁止用 `while`/`ps`/`sleep`/`grep` 轮询，禁止
> 用 Shell 读取项目外的 Claude 临时任务目录，也禁止重复启动仍在运行的同一任务。

```powershell
.\.venv\Scripts\Activate.ps1
python scripts/init_run.py --doc operator_docs/aclnnFoo.md --max-iterations 3
# 源码分析约束知识默认关闭；仅 ACLNN 且按算子名精准命中后加载
python scripts/init_run.py --doc operator_docs/aclnnGroupedMatmulV5.md --source-analysis-knowledge
python scripts/validate_artifacts.py constraints runs/.../iter_001/constraints.json
python scripts/validate_artifacts.py constraint_check runs/.../iter_001/constraint_check.json
python scripts/validate_artifacts.py cases runs/.../iter_001/cases.json
python scripts/validate_artifacts.py execution runs/.../iter_001/execution_result.json
python scripts/validate_artifacts.py analysis runs/.../iter_001/analysis.json
python scripts/validate_artifacts.py executor runs/.../iter_001/cases_executor.py
# 前台直接运行：命令中包含 python 和 generate_cases.py
python scripts/generate_cases.py --constraints .../constraints.json --output .../cases.json --count 10
# 后台 launch：`--` 后只放 generate_cases.py 的参数，不能重复放上面两个命令项
python scripts/generation_progress.py launch --output-dir runs/.../iter_001 -- --constraints .../constraints.json --output .../cases.json --count 10
python scripts/normalize_constraints.py .../constraints.json  # 原地规范化
```

### 环境配置
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item servers.example.json servers.json
# 编辑 servers.json 填写真实执行机连接信息
claude  # 启动 Claude Code CLI
```

Linux / macOS / WSL2：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp servers.example.json servers.json
claude
```

## Agent 调度表

所有流水线 Agent 必须在主会话当前工作树中运行，共享同一个 `runs/<run-id>`。调用
Agent 时不得设置 `isolation: worktree`，也不得使用 `EnterWorktree`；临时 worktree
会在 Agent 结束后清理，导致 `constraints.json` 等阶段产物无法交接。并行仅用于写入
互不重叠产物的阶段，不以文件系统隔离实现。

| 阶段 | Agent | 预加载 Skill | 主要产物 |
|---|---|---|---|
| 场景扫描（条件，EXTRACT 前） | `scene-scanner` | `scan-scenes` | `<run-dir>/inputs/scene_scan.json` |
| 约束提取 | `constraint-extractor` | `extract-constraints` | `constraints.json` |
| 源码分析（条件） | `source-analyst` | `analyze-source` | `source_raw.json` + `supplementary/uncertain/conflict-doc.md` + `conflict_candidates.json` |
| 约束补充（条件） | `constraint-supplementer` | `supplement-constraints` | `constraints_patch.json` |
| 约束语义检查（每轮 EXTRACT） | `constraint-checker` | `check-constraints` | `constraint_check.json` |
| 约束精准修复（检查发现问题） | `constraint-repairer` | `repair-constraints` | 修改当前 `constraints.json` |
| 用例生成 | `case-generator` | `generate-cases` | `cases.json` + `generation_summary.json` |
| 用例执行 | `case-executor` | `execute-cases`、`atc-cpu-golden-derivation` | `execution_result.json` + `cases_executor.py` + `cases_expanded.json` |
| 根因诊断 | `failure-analyst` | `diagnose-failure` | `analysis.json` |
| 提示词优化 | `prompt-optimizer` | `optimize-prompt` | `prompt_vN.md` |
| 质量门禁 | `quality-reviewer` | `validate-run` | `quality_gate.json` |

## 架构分层

### Claude Code 编排层（.claude/）
- `.claude/agents/*.md` — 专职 Agent 定义（角色、上下文、产物格式）
- `.claude/skills/*/SKILL.md` — 流程和阶段 Skill（`iterate-operator`、`iterate-directory`、各阶段 Skill）
- `.claude/hooks/` — `trace_hook.py`（调度事件 JSONL）、`guard_project_writes.py`（Bash 写入守卫）
- `.claude/settings.json` — default 回退模式 + Hook 动态授权 + sandbox 配置
- `.claude/runtime/schedule.jsonl` — 运行时调度事件审计（不入库）

> EXTRACT 后可选触发约束补充（`--supplement-constraints` 非空时）：
> `constraint-supplementer` 产 `constraints_patch.json`，
> `scripts/apply_supplement_constraints.py` 确定性合并并重跑 normalize+validate，
> 失败阻断、不进 GENERATE。为独立子步骤而非新状态，空即跳过。
>
> SUPPLEMENT 后强制触发 EXTRACT 内部 CHECK/REPAIR：checker 每轮完整对照文档和明确
> 补充证据，只写 `constraint_check.json`；repairer 使用隔离上下文仅修改报告中的
> open/unfixed 问题，随后由新 checker 复检。当前 iteration 未 passed 不得进 GENERATE。

- 全部通过：`SUCCESS`
- 有失败：`DIAGNOSE`
- `constraint_extraction`：`OPTIMIZE -> EXTRACT`，进入下一轮
- `constraint_extraction` 到 `human_checkpoint_round` 轮仍失败：人工补充检查点（补充/自主/停止）
- `generator_bug`：`STOP_GENERATOR_BUG`
- `executor_bug`：`STOP_EXECUTOR_BUG`
- 达到最大轮数：`MAX_ITERATIONS`
- 检查点"立即停止"：`STOPPED_BY_USER`

### Python 确定性层

**agent/generators/** — 保留的正式用例生成器（Z3 约束求解 + pairwise 组合）：
- `facade.py` → `TestCaseGenerator` 是公共入口，委托 `single_operator_handle` 按 platform 生成
- `common_model_definition.py` → `OperatorRule` Pydantic 模型，constraints.json 必须满足此校验
- `operator_handle_main.py` → `single_operator_handle` 正式生成逻辑
- `param_constraint_solve/z3_expression_solver_utils.py` → Z3 solver

**executer/** — 执行适配层（SSH + ATK 上传运行）：
- `runner.py` → `RunRequest` + `run_cases(mock|real|generate)` 三种模式
- `ssh.py` → asyncssh 连接、SFTP 上传、远程 ATK 执行
- `resources/generator.py` → 生成 `cases_executor.py`（含 dummy CPU golden 占位）
- `report_parser.py` → ATK xlsx 结果解析

**scripts/** — 确定性 CLI 工具，不调用 LLM：
- `init_run.py` — 创建 run 目录 + `run_state.json`；校验文档和 servers.json
- `init_batch.py` — 初始化批次目录
- `batch_state.py` — 批次状态迁移
- `generate_cases.py` — 调 facade 生成用例
- `execute_cases.py` — 调 executer 执行用例
- `normalize_constraints.py` — 原地规范化 constraints.json（Tensor format、dtype 等）
- `validate_artifacts.py` — 全阶段产物结构校验 + constraints 语义校验（含 `scene_scan` 校验）
- `validate_project.py` — 项目级校验
- `runtime_config.py` — 路径解析、prompt 版本发现、servers.json 校验
- `render_scene_directive.py` — 校验三级场景选择、解析显式参数的 `param_modes`、渲染 `inputs/scene_directive.md`、回写 `run_state.scene`
- `select_prompt.py` — ACLNN 提示词装配入口：manifest 路由 `base + 命中知识` → 冻结 `prompt_v1.md`+`prompt_preanalysis.json`+`prompt_assembly.json`
- `select_torch_npu_prompt.py` — torch_npu 装配入口，镜像 `select_prompt.py`（manifest 路由 + 冻结三产物 + 平台契约校验）
- `route_aclnn_knowledge.py` / `route_torch_npu_knowledge.py` — manifest 驱动知识路由（正向 trigger + `reject_on` 负向否决 + `depends_on` 依赖闭包）
- `validate_aclnn_knowledge.py` / `validate_torch_npu_knowledge.py` — 知识完整性预校验（manifest 字段、默认集、依赖闭包、跨 family 隔离、`reject_on` 合法性）
- `validate_prompt_assembly.py` — 校验冻结装配记录的全部 sha256 与模块顺序标记
- `validate_prompt_update_proposal.py` — 校验运行内沉淀的 `prompt_update_proposal.json`、目标边界（base_prompt / knowledge_* / torch_npu / no_update / run_only）与证据门禁
- `record_prompt_update_decision.py` — 仅在用户显式确认（`--confirmed-by-user`）后记录 approve/reject/defer，不改 canonical
- `record_prompt_update_application.py` — 记录已批准提案的 canonical 应用结果（`sha256_after`）与重跑校验，proposal 状态 approved→applied
- `classify_operator.py` — 算子分类（含融合/通算等执行路径判定）
- `collect_operator_source.py` — 算子源码闭包收集（include 不动点 + manifest + 报告）
- `extract_source_constraints.py` — 从源码快照提取确定性约束事实
- `locate_operator_source.py` — 在 operators-src 树定位算子源码
- `apply_supplement_constraints.py` — 补充约束 patch 确定性合并（重跑 normalize + validate）
- `apply_conflict_resolution.py` — 人工冲突裁决的机读合并层（不替用户决定胜负）
- `diag_fusion_step1.py` — 融合执行诊断第一步（step1 产物检查）
- `show_registry.py` — 展示 Skills/Agents 注册表

### 提示词与知识演进

ACLNN 与 torch_npu 现同构：`prompts/<family>_constraints/base.md` 为 **canonical
直接编辑**的提示词（只保留流程与未迁移规则，不复制 Pydantic schema）；`v4`/`v3`
为历史来源（provenance only），一次性机械拆分已完成、不再作为生成源（原迁移工具
`build_*_prompt_base.py` 已退场归档于 `archive/builders/`，仅留审计、不再 gate）。
再由 manifest 驱动的知识路由在 run
初始化（PLAN）阶段装配 `base + 命中知识模块`，并冻结为 `prompt_v1.md` +
`prompt_preanalysis.json` + `prompt_assembly.json`（含 sha256）。两 family 知识根
相互隔离（`knowledge/aclnn` / `knowledge/torch_npu`，由各自 validator 禁跨 family
引用）；extractor 只读冻结快照，不重走路由。v1-v4（torch v1-v3）仅作历史来源。
迭代优化只在 run 内写候选、变更说明和 `prompt_update_proposal.json`，按
base/common/feature/exact-operator/torch_npu/no-update 选择最小目的地。任务终态由
主协调器展示证据、适用范围和试验结果并逐条询问用户；只有明确批准后才能修改
canonical 文件（提升后须重跑 `validate_*_knowledge` + 路由 + 组装冻结校验，即
`init_run` + `validate_prompt_assembly.py --record`）。
详细契约见 `docs/PROMPT_ASSEMBLY.md` 与 `docs/PROMPT_EVOLUTION.md`。

### 产物目录结构

```text
runs/<operator>-<timestamp>/
  run_state.json           # 唯一真相源：状态、轮次、参数
  inputs/                  # 只读快照（算子文档 + prompt）
  iter_001/                # 第一轮产物
    constraints.json       # 必须满足 OperatorRule
    generation_summary.json
    cases.json             # 紧凑表示；执行阶段展开为 cases_expanded.json
    cases_executor.py      # ATK 执行脚本（含 CPU golden）
    execution_result.json  # passed+failed=total
    quality_gate.json      # next_state 决定流程走向
    analysis.json          # root_cause ∈ {constraint_extraction, generator_bug, executor_bug}
    prompt_v2.md           # 仅 constraint_extraction 根因时产出
```

批次目录：`runs/batches/<batch-id>/batch_state.json`

## 安全边界

- 禁止读取 `.env`；允许执行流程读取 `servers.json`，但禁止修改或输出其中的秘密
- 默认 `mode=real`；`servers.json` 缺失或不完整时停止并提示，禁止静默回退 Mock
- 算子文档可来自项目外路径；先只读复制到 `runs/<run-id>/inputs/`，后续 Agent 只用项目内快照
- `executer/` 与 `agent/generators/` 只读、可导入执行，禁止新增、修改或删除任何文件和子目录
- 活动任务中 Edit/Write/删除/移动/重定向写入只能作用于当前
  `runs/<run-id>/`（`guard_project_writes.py` Hook 强制）
- 活动任务不得读取其他 `runs/<other-run-id>/`；批次仅在前一 run 终态后切换
- Agent 业务产物只能写当前 `runs/<run-id>/`；canonical 文件（`prompts/**/base.md`、
  `knowledge/**`）修改只在任务终态后经用户**显式逐条批准**（AskUserQuestion），按
  `prompt_update_proposal.json` 的 `change.content` 由主协调器应用、
  `record_prompt_update_decision.py` 记裁决、`record_prompt_update_application.py` 记应用
  与重跑校验，随后重跑 `validate_*_knowledge` + `init_run` +
  `validate_prompt_assembly.py --record`。用户沉默、运行成功或批处理模式均不构成批准
  （详见 `docs/PROMPT_EVOLUTION.md` 与 `.claude/skills/iterate-operator/SKILL.md` 第 10 步）
- 不自动提交、推送或删除文件
- 约束、用例、执行结果和分析结果必须先过 `scripts/validate_artifacts.py`

## 调度可见性

每次委派前输出：`调度 -> <agent> | 输入: ... | 预期产物: ...`
每次委派后输出：`完成 <- <agent> | 结论: ... | 产物: ...`

运行时观测：
- `/agents` — 查看运行中和最近完成的 Agent
- `/hooks` — 查看 Hooks 配置
- `.claude/runtime/schedule.jsonl` — 每行一个调度事件 JSON

## 重要约定

- `constraints.json` 的 `allowed_range_value.type=range` 不允许 null 端点；开区间写 `constraints_in_parameters` 不等式
- `type=enum` 允许 null 作为离散候选；`expr` 中裸 null 规范化为 Python `None`
- `cases.json` 是紧凑表示；带 `length` 的列表类输入在执行阶段展开为 `cases_expanded.json`
- 诊断用例格式问题必须同时检查 `cases.json` 和 `cases_expanded.json`
- `execution_result.json` 的 `engine_error` 非空时不能宣称业务成功
- `analysis.json` 的 `root_cause` 只能为 `constraint_extraction`、`generator_bug`、`executor_bug`
- `quality_gate.json` 的 `blocking_issues` 非空时 status 必须为 blocked，主协调器不得越过门禁
- 质量门禁 Agent 不修复其他 Agent 的产物，避免职责串味

完整设计见 docs/WORKFLOW.md，产物字段见 docs/ARTIFACT_CONTRACTS.md，可观测性见 docs/OBSERVABILITY.md，权限见 docs/PERMISSIONS.md。
