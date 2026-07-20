# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本项目是 CANN 算子迭代测试的 Claude Code CLI 原生编排器。Claude Code 是顶层运行时；
Python 只承担确定性业务（校验、用例生成、执行适配、调度留痕），不再嵌套调用 LLM。

## 核心流程

状态机：`PLAN → EXTRACT → GENERATE → EXECUTE → GATE`
- 全部通过 → `SUCCESS`
- 有失败 → `DIAGNOSE`；只有根因为 `constraint_extraction` 时才进入 `OPTIMIZE → EXTRACT` 循环
- `generator_bug` / `executor_bug` → 立即止损
- 达到 max-iterations → `MAX_ITERATIONS`

每轮产物只通过 `runs/<run-id>/` 下的文件交接，禁止跨 Agent 的隐式上下文污染。

## 常用命令

### 算子迭代
```text
/iterate-operator operator_docs/aclnnFoo.md --max-iterations 3 --case-count 10
/iterate-operator D:\operator_docs\aclnnFoo.md  # 支持项目外路径
/iterate-directory operator_docs --max-iterations 3  # 串行执行目录中全部算子
/iterate-directory --batch-dir runs/batches/<batch-id>  # 恢复中断的批次
/show-workforce  # 查看可用 Skills、Agents 和调度拓扑
```

### 确定性 Python 工具（scripts/）

所有脚本从项目根目录执行，Python 需先激活 `.venv`：

```powershell
.\.venv\Scripts\Activate.ps1
python scripts/init_run.py --doc operator_docs/aclnnFoo.md --max-iterations 3
python scripts/validate_artifacts.py constraints runs/.../iter_001/constraints.json
python scripts/validate_artifacts.py cases runs/.../iter_001/cases.json
python scripts/validate_artifacts.py execution runs/.../iter_001/execution_result.json
python scripts/validate_artifacts.py analysis runs/.../iter_001/analysis.json
python scripts/validate_artifacts.py executor runs/.../iter_001/cases_executor.py
python scripts/generate_cases.py --constraints .../constraints.json --output .../cases.json --count 10
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

## Agent 调度表

| 阶段 | Agent | 预加载 Skill | 主要产物 |
|---|---|---|---|
| 约束提取 | `constraint-extractor` | `extract-constraints` | `constraints.json` |
| 源码分析（条件） | `source-analyst` | `analyze-source` | `source_raw.json` + `supplementary/uncertain/conflict-doc.md` + `conflict_candidates.json` |
| 约束补充（条件） | `constraint-supplementer` | `supplement-constraints` | `constraints_patch.json` |
| 用例生成 | `case-generator` | `generate-cases` | `cases.json` + `generation_summary.json` |
| 用例执行 | `case-executor` | `execute-cases`、`atc-cpu-golden-derivation` | `execution_result.json` + `cases_executor.py` + `cases_expanded.json` |
| 根因诊断 | `failure-analyst` | `diagnose-failure` | `analysis.json` |
| 提示词优化 | `prompt-optimizer` | `optimize-prompt` | `prompt_vN.md` |
| 质量门禁 | `quality-reviewer` | `validate-run` | `quality_gate.json` |

## 架构分层

### Claude Code 编排层（.claude/）
- `.claude/agents/*.md` — 6 个专职 Agent 定义（角色、上下文、产物格式）
- `.claude/skills/*/SKILL.md` — 流程和阶段 Skill（`iterate-operator`、`iterate-directory`、各阶段 Skill）
- `.claude/hooks/` — `trace_hook.py`（调度事件 JSONL）、`guard_project_writes.py`（Bash 写入守卫）
- `.claude/settings.json` — `dontAsk` 权限 + sandbox + Hooks 配置
- `.claude/runtime/schedule.jsonl` — 运行时调度事件审计（不入库）

> EXTRACT 后可选触发约束补充（`--supplement-constraints` 非空时）：
> `constraint-supplementer` 产 `constraints_patch.json`，
> `scripts/apply_supplement_constraints.py` 确定性合并并重跑 normalize+validate，
> 失败阻断、不进 GENERATE。为独立子步骤而非新状态，空即跳过。

- 全部通过：`SUCCESS`
- 有失败：`DIAGNOSE`
- `constraint_extraction`：`OPTIMIZE -> EXTRACT`，进入下一轮
- `generator_bug`：`STOP_GENERATOR_BUG`
- `executor_bug`：`STOP_EXECUTOR_BUG`
- 达到最大轮数：`MAX_ITERATIONS`

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
- `validate_artifacts.py` — 全阶段产物结构校验 + constraints 语义校验
- `validate_project.py` — 项目级校验
- `runtime_config.py` — 路径解析、prompt 版本发现、servers.json 校验

### 提示词版本化

`prompts/operator_constraints_extract_vN.md`，N 为整数版本号。`init_run.py` 按数值 N
（而非文件名字典序）自动选择最新版本，并复制快照到 run 目录。迭代优化时 `prompt-optimizer`
生成 `prompt_v(N+1).md`，写入 `prompts/` 和当前 run 的 iter 目录。

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

- 不读取或输出 `.env`、`servers.json` 中的秘密（deny 规则已配置）
- 默认 `mode=real`；`servers.json` 缺失或不完整时停止并提示，禁止静默回退 Mock
- 算子文档可来自项目外路径；先只读复制到 `runs/<run-id>/inputs/`，后续 Agent 只用项目内快照
- Edit/Write/删除/移动/重定向写入只能作用于本项目目录（`guard_project_writes.py` Hook 强制）
- Agent 业务产物只能写当前 `runs/<run-id>/` 和提示词版本文件
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
