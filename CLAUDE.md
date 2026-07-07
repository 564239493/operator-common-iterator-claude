# Claude Code 项目指令

本项目由 Claude Code CLI 原生编排。你是主协调器，不是隐藏在 Python
脚本里的第二个 LLM 调用层。业务推理必须通过项目 Skill 与专职 Agent 完成；
Python 只负责确定性的校验、用例生成、执行适配和调度留痕。

## 每次会话的第一原则

1. 先读取 `docs/WORKFLOW.md` 和 `docs/ARTIFACT_CONTRACTS.md`。
2. 用户要求查看能力时，执行 `/show-workforce`。
3. 用户要求迭代算子时，使用 `/iterate-operator`，不要临时发明另一套流程。
   用户要求处理目录中的全部算子时，使用 `/iterate-directory`，由它串行复用
   `/iterate-operator`。
4. 每次委派前在主会话输出：
   `调度 -> <agent> | 输入: ... | 预期产物: ...`
5. 每次委派完成后输出：
   `完成 <- <agent> | 结论: ... | 产物: ...`
6. 所有阶段只通过 `runs/<run-id>/` 下的文件交接，避免跨 Agent 的隐式上下文污染。

## Agent 调度表

| 阶段 | Agent | 预加载 Skill | 主要产物 |
|---|---|---|---|
| 约束提取 | `constraint-extractor` | `extract-constraints` | `constraints.json` |
| 源码校验（可选，EXTRACT 后每轮一次） | `source-analyst` | `analyze-source` | `source_evidence.json`、`constraints_patch.json` |
| 用例生成 | `case-generator` | `generate-cases` | `cases.json` |
| 用例执行 | `case-executor` | `execute-cases`、`atc-cpu-golden-derivation` | `execution_result.json` |
| 根因诊断 | `failure-analyst` | `diagnose-failure` | `analysis.json` |
| 提示词优化 | `prompt-optimizer` | `optimize-prompt` | `prompt_vN.md` |
| 质量门禁 | `quality-reviewer` | `validate-run` | `quality_gate.json` |

## 状态机

`PLAN -> EXTRACT -> GENERATE -> EXECUTE -> GATE`

- 全部通过：`SUCCESS`
- 有失败：`DIAGNOSE`
- `constraint_extraction`：`OPTIMIZE -> EXTRACT`，进入下一轮
- `generator_bug`：`STOP_GENERATOR_BUG`
- `executor_bug`：`STOP_EXECUTOR_BUG`
- 达到最大轮数：`MAX_ITERATIONS`

主协调器必须显式维护当前轮次和状态，不能跳过质量门禁。并行只用于同一阶段内
彼此无写冲突的只读检查；主流水线阶段有数据依赖，默认串行。

## 安全边界

- 不读取或输出 `.env`、`servers.json` 中的秘密。
- 默认使用 real 执行。缺少或无法解析 `servers.json` 时必须停止并提示用户配置，
  禁止静默回退 Mock；只有用户显式指定 `--mode mock` 才能使用 Mock。
- 算子文档可来自项目外路径；先只读复制到 `runs/<run-id>/inputs/`，所有 Agent
  使用项目内快照，禁止修改外部原文档。
- 项目权限采用 `dontAsk`：已批准操作直接执行，不弹出确认。
- 可读取项目外文件；Edit/Write/删除/移动/重定向写入只能作用于本项目目录。
- Agent 业务产物只能写当前 `runs/<run-id>/` 和提示词版本文件。
- 不自动提交、推送或删除文件。
- 约束、用例、执行结果和分析结果必须先过 `scripts/validate_artifacts.py`。
- 源码分析是可选增强项：仅当 `--source-root` 提供且非空时，`init_run.py` 把算子源码
  只读复制到 `runs/<run-id>/inputs/src_snapshot/`，由 `source-analyst` 在每轮 EXTRACT
  后校验约束类型/范围/表达式。`--source-root` 缺失或为空绝不阻断闭环，退回纯文档驱动。
  源码定位用 `scripts/locate_operator_source.py`，确定性抽取用
  `scripts/extract_source_constraints.py`，patch 机械应用用
  `scripts/apply_constraints_patch.py`（单轮内仅一次，失败回滚不重试）。
- 约束条目的 `origin` 字段标记来源：`doc`（文档提取，默认）或 `source_analysis`
  （源码校验 patch 写入）。源码与文档冲突时以源码为准并在 `source_evidence.json`
  标注 `doc_error`，但 `constraints.json` 的最终写入仍只由
  `apply_constraints_patch.py` 机械完成，source-analyst 不直接写约束。
