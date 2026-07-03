# operator-common-iterator-claude

这是 `operator-common-iterator` 的全新 Claude Code CLI 原生版本。原项目保持不变；
这里不再由 Python 编排器嵌套调用 LLM，而是让 Claude Code 直接发现并调度
Skills 与 Subagents，Python 只承担确定性业务工具。

## 你能直接看到什么

- 启动时：项目 Skills、Agents 和调度拓扑清单。
- 运行时：每次 Agent 的 `START / STOP` 终端消息。
- 会话内：`/agents` 查看正在运行及已完成的 Agent。
- 配置层：`/hooks` 查看调度观测 Hooks。
- 文件层：`.claude/runtime/schedule.jsonl` 保存完整调度事件。
- 产物层：`runs/<run-id>/run_state.json` 和各轮目录保存状态与交接文件。

## 快速开始

要求 Python 3.10+，Claude Code 建议 2.1.172+。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item servers.example.json servers.json
# 编辑 servers.json，填写真实执行机连接信息
claude
```

`torch` 是保留下来的正式用例生成器依赖，安装体积较大；如组织内部使用专用
PyTorch/昇腾镜像，请按内部源安装后再执行其余依赖。

进入 Claude Code 后：

```text
/show-workforce
/iterate-operator operator_docs/aclnnAlltoAllMatmul.md --max-iterations 3 --case-count 10
```

串行执行一个目录中的全部算子文档：

```text
/iterate-directory operator_docs --max-iterations 3 --case-count 10
```

默认某个算子失败后继续执行下一个；需要首个失败即停止时增加 `--fail-fast`，需要扫描
子目录时增加 `--recursive`。会话中断后可用批次目录恢复：

```text
/iterate-directory --batch-dir runs/batches/<batch-id>
```

默认执行真实用例。如果 `servers.json` 缺失或字段不完整，流程会停止并提示配置，不会
自动降级 Mock。仅需演练编排时显式传入 `--mode mock`。

算子文档也可以位于其他目录：

```text
/iterate-operator D:\operator_docs\aclnnFoo.md
/iterate-operator ..\other-project\docs\aclnnFoo.md
```

外部文档只读，并会复制到本次 `runs/<run-id>/inputs/`；后续 Agent 使用项目内快照。

也可以用非交互模式，并查看完整流式调度：

```powershell
claude -p "/iterate-operator D:\operator_docs\aclnnFoo.md --max-iterations 3" `
  --output-format stream-json --verbose
```

常用观察入口：

```text
/agents
/hooks
/show-workforce
```

完整设计见 [docs/WORKFLOW.md](docs/WORKFLOW.md)，Agent/Skill 可观测方式见
[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)，产物字段见
[docs/ARTIFACT_CONTRACTS.md](docs/ARTIFACT_CONTRACTS.md)，无确认权限边界见
[docs/PERMISSIONS.md](docs/PERMISSIONS.md)。

## 与旧项目的关键差异

| 维度 | 旧项目 | 本项目 |
|---|---|---|
| 顶层编排 | `orchestrator.py` | Claude Code 主会话 + `/iterate-operator` |
| LLM 调用 | Python backend/API/CLI 子进程 | Claude Code Agent 原生上下文 |
| 专家隔离 | 手写 Session A/B | `.claude/agents/*.md` 独立上下文 |
| 流程能力 | Python 函数 | `.claude/skills/*/SKILL.md` |
| 调度观察 | 日志中推断 | CLI Agent 面板 + Hooks + JSONL |
| 阶段交接 | Python 内存对象为主 | 明确的 JSON/Markdown 产物契约 |

## 目录

```text
.claude/
  agents/              # 6 个专职 Agent
  skills/              # 主流程及阶段 Skills
  hooks/               # CLI 生命周期调度观测
  settings.json        # 项目级权限与 Hooks
docs/                  # 流程、观测和产物契约
agent/
  generators/          # 原项目确定性用例生成逻辑
operator_docs/         # 输入算子文档
prompts/               # 初始与迭代提示词
scripts/               # 确定性工具，不调用 LLM
runs/                  # 运行产物（不入库）
```
