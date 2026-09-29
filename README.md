# operator-common-iterator（opencode 原生版）

这是 `operator-common-iterator` 的 **opencode 原生编排版本**。opencode 是顶层运行时，
直接发现并调度技能（skills）与子智能体（agents）；Python 只承担确定性业务工具
（校验、用例生成、执行适配），不调用 LLM。

## 你能直接看到什么

- 启动时：`/show-workforce` 列出项目技能、子智能体、命令与调度拓扑。
- 运行时：每次委派前后的「调度 -> / 完成 <-」消息 + task 工具调用记录。
- 产物层：`runs/<run-id>/run_state.json` 和各轮目录保存状态与交接文件。

## 快速开始

海思 `torch_npu.*` + TTK 兼容流程见
[docs/HS_TTK_WORKFLOW.md](docs/HS_TTK_WORKFLOW.md)。原 ACLNN/ATK 流程仍是默认；
六个已适配的重点海思算子会自动启用 TTK，其余 torch_npu API 默认只执行约束提取；
也可以用 `--test-framework` 显式覆盖。
torch_npu 全量文档审计、提示词分层和 schema 缺口见
[docs/TORCH_NPU_CONSTRAINT_PROMPT.md](docs/TORCH_NPU_CONSTRAINT_PROMPT.md)。

要求 Python 3.10+，opencode 建议 1.18+（插件 API V1）。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp servers.example.json servers.json
# 编辑 servers.json，填写真实执行机连接信息
opencode
```

`torch` 是保留下来的正式用例生成器依赖，安装体积较大；如组织内部使用专用
PyTorch/昇腾镜像，请按内部源安装后再执行其余依赖。

进入 opencode 后：

```text
/show-workforce
/iterate-operator operator_docs/aclnnAlltoAllMatmul.md --max-iterations 3 --case-count 10
/iterate-operator operator_docs/aclnnAlltoAllMatmul.md --constraint-check-rounds 3
```

ACLNN 默认使用 ATK；如需完整 TTK ACLNN 流程，显式指定：

```text
/iterate-operator operator_docs/aclnnFoo.md --operator-family aclnn --test-framework ttk --mode real
```

该模式仍使用 ACLNN 隔离提示词提取约束，随后由统一生成器产生 `cases.json`，转换为
`cases_ttk.csv`，并通过远端 `python3 -m ttk aclnn` 执行；不需要 torch_npu E2E
Golden plugin。

torch_npu + TTK 默认直接使用原生生成器。只有需要启用 TND/BSND/
paged-attention 场景拆分和投影时才显式指定：

```text
/iterate-operator operator_docs/hs/torch_npu-npu_sparse_flash_attention.md --test-framework ttk --hs-scenario-mode planned
```

未指定 `--prompt` 时，ACLNN 使用 `prompts/operator_constraints/base.md`（canonical
直接编辑），并按当前文档装配 `knowledge/aclnn`；torch_npu 使用
`prompts/torch_npu_constraints/base.md`（canonical 直接编辑）并装配自己的
知识根。两者完全隔离。需要复现指定历史
版本时仍可显式传入：

```text
/iterate-operator operator_docs/aclnnAlltoAllMatmul.md --prompt prompts/history/operator_constraints_extract_v1.md
```

串行执行一个目录中的全部算子文档：

```text
/iterate-directory operator_docs --max-iterations 3 --case-count 10
/iterate-directory operator_docs --constraint-check-rounds 3
```

默认某个算子失败后继续执行下一个；需要首个失败即停止时增加 `--fail-fast`，需要扫描
子目录时增加 `--recursive`。会话中断后可用批次目录恢复：

```text
/iterate-directory --batch-dir runs/batches/<batch-id>
```

默认执行真实用例。如果 `servers.json` 缺失或字段不完整，流程会停止并提示配置，不会
自动降级 Mock。仅需演练编排时显式传入 `--mode mock`。

算子文档也可以位于项目外的其他目录：

```text
/iterate-operator /path/to/operator_docs/aclnnFoo.md
```

外部文档只读，并会复制到本次 `runs/<run-id>/inputs/`；后续 Agent 使用项目内快照。

完整设计见 [docs/WORKFLOW.md](docs/WORKFLOW.md)，可观测方式见
[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)，产物字段见
[docs/ARTIFACT_CONTRACTS.md](docs/ARTIFACT_CONTRACTS.md)，权限边界见
[docs/PERMISSIONS.md](docs/PERMISSIONS.md)。

## 与旧项目的关键差异

| 维度 | 旧项目 | 本项目 |
|---|---|---|
| 顶层编排 | `orchestrator.py` | opencode 主会话 + `/iterate-operator` |
| LLM 调用 | Python backend/API/CLI 子进程 | opencode Agent 原生上下文 |
| 专家隔离 | 手写 Session A/B | `.opencode/agent/*.md` 独立上下文 |
| 流程能力 | Python 函数 | `.opencode/skills/*/SKILL.md` |
| 安全边界 | Python 自检 | 静态 permission + guard.js 插件（fail-closed） |
| 阶段交接 | Python 内存对象为主 | 明确的 JSON/Markdown 产物契约 |

## 目录

```text
.opencode/
  agent/                # 专职子智能体（含独立约束 Checker/Repairer）
  skills/               # 主流程、阶段技能 + 生成型知识技能
  command/              # /iterate-operator 等斜杠命令
  plugins/guard.js      # 写入守卫（run 隔离 + 受保护路径 + 高风险转 ask）
  opencode.json         # 静态 permission 规则
AGENTS.md               # opencode 项目指令
docs/                   # 流程、观测和产物契约
agent/
  generators/           # 原项目确定性用例生成逻辑
operator_docs/         # 输入算子文档
prompts/               # 初始与迭代提示词
scripts/               # 确定性工具，不调用 LLM
runs/                  # 运行产物（不入库）
```
