# OPCI 使用说明书

> CANN 算子迭代测试 MCP Server + Agent Pack  
> 版本 0.1.0

---

## 目录

1. [概述](#1-概述)
2. [安装](#2-安装)
3. [项目初始化（opci setup）](#3-项目初始化opci-setup)
4. [执行机配置](#4-执行机配置)
5. [启动 Claude Code](#5-启动-claude-code)
6. [算子迭代命令](#6-算子迭代命令)
7. [批次执行命令](#7-批次执行命令)
8. [MCP 工具一览](#8-mcp-工具一览)
9. [目录结构说明](#9-目录结构说明)
10. [运行日志](#10-运行日志)
11. [常见问题](#11-常见问题)

---

## 1. 概述

OPCI（Operator Common Iterator）是 CANN 算子迭代测试的自动化编排框架。架构分为两层：

| 层 | 角色 | 技术 |
|---|---|---|
| **编排层** | 顶层运行时，调度 Agent、调用 MCP 工具、决策状态流转 | Claude Code CLI |
| **确定性层** | 校验、用例生成、执行适配、调度留痕 | Python（MCP Server + scripts） |

核心状态机：`PLAN → EXTRACT → GENERATE → EXECUTE → GATE`

- 全部通过 → `SUCCESS`
- 有失败 → `DIAGNOSE`；根因为 `constraint_extraction` 时进入 `OPTIMIZE → EXTRACT` 循环
- `generator_bug` / `executor_bug` → 立即止损
- 达到 max-iterations → `MAX_ITERATIONS`

---

## 2. 安装

### 2.1 前置条件

| 依赖 | 版本要求 | 说明 |
|---|---|---|
| Python | ≥ 3.10 | 推荐 3.12+ |
| Claude Code CLI | 最新版 | 从 [claude.ai/code](https://claude.ai/code) 安装 |
| uv | 最新版 | Python 包管理器，从 [astral.sh/uv](https://astral.sh/uv) 安装 |

> 没有 uv 也可以用 pip 安装，详见下方 2.2B 节。

### 2.2A 使用 uv 安装（推荐）

OPCI 是一个 CLI 工具（`opci setup` / `opci mcp-server`），需要全局安装以便在任意项目目录都能直接使用 `opci` 命令。

```powershell
# 全局安装（自动创建隔离环境，只暴露 opci 命令到 PATH）
# uv 自动识别当前平台，只下载对应的 torch 包（~200MB），不会全量下载所有平台的 2GB 包
uv tool install opci-0.1.0-py3-none-any.whl
```

> **为什么用 `uv tool install` 而不是 `uv pip install`？**
> `uv tool install` 专为全局 CLI 工具设计——它把 opci 及所有依赖安装在隔离环境中，只在全局 PATH 暴露 `opci` 可执行入口。这样你可以在任意目录执行 `opci setup`，而不会污染其他 Python 项目。相当于 `pipx install` 的 uv 版本。
> `uv pip install` 只能往某个已有 venv 里装包，不会全局暴露命令，不适合 CLI 工具场景。

安装完成后 `opci` 命令自动可用：

```powershell
opci --help
# 输出：
#   setup       Copy Agent Pack to working directory
#   mcp-server  Start MCP server in stdio mode
```

> 安装完成后验证 `opci --help`。如果提示"不是内部命令"，说明 PATH 未配置，详见[常见问题](#11-常见问题)。

#### 更新或重装 opci

**更新 opci 版本时，必须先卸载再安装**，避免旧版本缓存残留导致 MCP server 或 `opci setup` 行为异常：

```powershell
# 第一步：卸载旧版本
uv tool uninstall opci

# 第二步：安装新版本
uv tool install opci-0.1.0-py3-none-any.whl
```

> **重要**：更新后需关闭正在使用 opci MCP server 的 Claude Code session（`opci.exe` 可能被锁定），然后再重新启动 Claude Code。

### 2.2B 使用 pip 安装

如果没有 uv，也可以用 pip 安装。但 pip 默认会下载所有平台的 torch 包（约 2GB），非常耗时。**关键技巧**：先用 PyTorch 的 CPU-only 专属索引单独安装 torch，再安装 opci 的其余依赖：

```powershell
# 步骤 1：用 pipx 创建隔离环境并安装 opci（推荐，与 uv tool install 效果一致）
pipx install opci-0.1.0-py3-none-any.whl

# ——— 或者用 venv + pip 手动安装 ———
# 步骤 1：创建隔离 venv
python -m venv opci-env
opci-env\Scripts\Activate.ps1

# 步骤 2：先安装 CPU-only torch（只下载当前平台包，约 200MB）
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 步骤 3：安装 opci（torch 已满足，不再重复下载）
pip install --no-deps opci-0.1.0-py3-none-any.whl

# 步骤 4：安装其余依赖
pip install fastmcp pydantic numpy scipy z3-solver asyncssh openpyxl jinja2 pyyaml packaging typing_extensions

# 步骤 5：验证
opci --help
```

> **为什么 CPU-only torch？** OPCI 用 torch 做用例数据构造和类型推断，不需要 GPU/CUDA。CPU 版本体积约 200MB，而 CUDA 版本约 2GB。如果你确实需要 CUDA torch，跳过步骤 2 的 `--index-url` 参数即可，但安装时间会显著增加。

### 2.3 验证安装

```powershell
# uv 安装方式
uv tool list
# 输出中应包含：opci v0.1.0

# pip 安装方式
pip show opci

# 通用验证：检查 MCP server 是否可启动（会输出 warmup 日志，几秒后 Ctrl+C 退出）
opci mcp-server
# 预期 stderr 输出：
#   [warmup] Pre-importing all dependencies...
#   [warmup] z3 OK
#   [warmup] numpy OK
#   [warmup] torch OK
#   [warmup] scipy OK
#   ...
#   [warmup] OperatorRule OK
#   [warmup] All dependencies loaded successfully.
#   [warmup] 14 OK, 0 FAIL
#   [warmup] All dependencies loaded successfully.
#   [warmup] 14 OK, 0 FAIL
```

> **注意**：MCP server 不需要手动启动。Claude Code 会在启动时自动通过 `.mcp.json` 配置加载它。

---

## 3. 项目初始化（opci setup）

`opci setup` 将 Agent Pack 资源部署到你的工作目录，这是使用前的**必经步骤**。

```powershell
# 在当前目录初始化
opci setup

# 在指定目录初始化
opci setup --target D:\my-operator-project
```

### setup 做了什么

| 操作 | 目标路径 | 说明 |
|---|---|---|
| 复制 Agent 定义 | `.claude/agents/*.md` | 6 个专职 Agent |
| 复制 Skill 定义 | `.claude/skills/*/SKILL.md` | 10 个流程 Skill |
| 复制 Hooks | `.claude/hooks/*.py` | trace_hook.py + guard_project_writes.py |
| 生成 settings.json | `.claude/settings.json` | 权限 + Hooks + sandbox 配置 |
| 复制 .mcp.json | `.mcp.json` | MCP server 注册（Claude Code 自动发现） |
| 复制提示词 | `prompts/` | 约束提取 prompt v1~v3 |
| 复制文档 | `docs/` | 设计文档 |
| 复制知识库 | `knowledge/` | 算子模式参考 |
| 复制示例算子文档 | `operator_docs/` | 10 个示例算子 |
| 复制 servers.example.json | `servers.example.json` | 执行机配置模板 |
| 写项目根标记 | `.opci_project_root` | 绝对路径标记，MCP 工具定位项目根 |
| 创建空目录 | `runs/`、`operator_docs/` | 运行产物和算子文档目录 |

---

## 4. 执行机配置

如果需要**真实执行**算子用例（`mode=real`），必须配置执行机连接信息：

```powershell
# 1. 复制模板
Copy-Item servers.example.json servers.json

# 2. 编辑 servers.json，填写真实信息
```

`servers.json` 格式（参考 `servers.example.json`）：

```json
{
  "servers": [
    {
      "name": "Atlas A3 development host",
      "ip": "192.168.1.100",
      "port": 22,
      "username": "operator_atk",
      "password": "你的密码",
      "platforms": [
        "Atlas A3 训练系列产品/Atlas A3 推理系列产品"
      ],
      "supports_npu": true,
      "env_init_script": "/usr/local/Ascend/ascend-toolkit/set_env.sh"
    }
  ]
}
```

> **安全提示**：`servers.json` 含密码等敏感信息，已被 `.gitignore` 忽略且在 Claude Code 权限 deny 列表中（`Read(./servers.json)` 禁止读取）。MCP 工具只校验配置结构是否完整，不读取或输出秘密字段值。

如果仅做**本地验证**（不执行真实用例），可以不创建 `servers.json`，使用 `mode=mock`。

---

## 5. 启动 Claude Code

```powershell
# 进入 setup 初始化的项目目录
cd D:\my-operator-project

# 启动 Claude Code CLI
claude
```

Claude Code 启动时会自动：
1. 读取 `.mcp.json` → 发现并启动 `opci mcp-server`
2. 读取 `.claude/settings.json` → 加载权限、Hooks、Agent/Skill 配置
3. MCP server warmup → 预加载 Z3/numpy/torch 等重型依赖（stderr 可见进度）

---

## 6. 算子迭代命令

### 6.1 单算子迭代

```
/iterate-operator operator_docs/aclnnFoo.md --max-iterations 3 --case-count 10
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| 算子文档路径 | — | 项目相对路径或绝对路径 |
| `--max-iterations` | 5 | 最大迭代轮数 |
| `--case-count` | 10 | 每轮生成用例数量 |
| `--mode` | real | 执行模式（`real`/`mock`） |

算子文档可以是项目内路径（`operator_docs/aclnnFoo.md`）或项目外绝对路径（`D:\docs\aclnnFoo.md`）。项目外路径会被自动复制到 `runs/<run-id>/inputs/` 作为只读快照。

### 6.2 查看调度拓扑

```
/show-workforce
```

输出所有可用 Skill、Agent 及调度拓扑。

### 6.3 状态流转

```
/iterate-operator 流程中，主协调器按以下顺序委派 Agent：

PLAN → init_run MCP 工具 → 创建 run 目录和 run_state.json
EXTRACT → constraint-extractor Agent → 提取约束 → normalize → validate
GENERATE → case-generator Agent → 生成用例 → validate
EXECUTE → case-executor Agent → 执行用例（mock 或 real）→ validate
GATE → quality-reviewer Agent → 质量门禁 → 决定下一步

全通过 → SUCCESS
有失败 → failure-analyst Agent 诊断根因
  根因 = constraint_extraction → prompt-optimizer 优化提示词 → 下一轮
  根因 = generator_bug / executor_bug → 立即止损
```

---

## 7. 批次执行命令

批量执行多个算子的迭代测试：

```
/iterate-directory operator_docs --max-iterations 3
```

| 参数 | 说明 |
|---|---|
| 目录路径 | 包含多个算子文档的目录 |
| `--max-iterations` | 每个算子的最大迭代轮数 |
| `--batch-dir` | 恢复中断的批次（`runs/batches/<batch-id>`） |

批次状态存储在 `runs/batches/<batch-id>/batch_state.json`，支持中断恢复。

---

## 8. MCP 工具一览

OPCI 注册 22 个 MCP 工具，全部通过 `mcp__opci__<工具名>` 引用。Claude Code 的 settings.json 已预授权所有工具（`dontAsk` 模式）。

### 运行管理

| 工具名 | 功能 | 关键参数 |
|---|---|---|
| `init_run` | 创建 run 目录和初始状态 | `doc`, `max_iterations`, `case_count`, `mode` |
| `update_run_state` | 更新 run_state.json | `run_dir`, `state`, `iteration` |
| `read_operator_prompt` | 读取当前提示词 | `run_dir` |
| `write_operator_prompt` | 写入优化后的提示词 | `run_dir`, `iter_dir`, `content`, `version` |
| `find_latest_operator_prompt` | 查找最新版本提示词 | — |
| `validate_server_config` | 校验执行机配置 | `path` |

### 约束处理

| 工具名 | 功能 | 关键参数 |
|---|---|---|
| `normalize_constraints` | 规范化约束 JSON | `path` |
| `validate_constraints` | 校验约束 JSON | `path` |

### 用例生成

| 工具名 | 功能 | 关键参数 |
|---|---|---|
| `generate_cases` | 生成测试用例 | `constraints`, `output`, `count`, `iter_dir` |
| `validate_cases` | 校验用例 JSON | `path` |

### 用例执行

| 工具名 | 功能 | 关键参数 |
|---|---|---|
| `execute_cases_generate` | 仅生成执行脚本 | `cases`, `output`, `doc`, `operator` |
| `execute_cases_real` | 真实执行（SSH + ATK） | `cases`, `output`, `doc`, `operator` |
| `execute_cases_mock` | Mock 执行 | `cases`, `output` |
| `validate_execution` | 校验执行结果 JSON | `path` |
| `validate_executor` | 校验执行脚本 | `path` |

### 诊断分析

| 工具名 | 功能 | 关键参数 |
|---|---|---|
| `validate_analysis` | 校验分析 JSON | `path` |

### 批次管理

| 工具名 | 功能 | 关键参数 |
|---|---|---|
| `init_batch` | 初始化批次目录 | `directory`, `glob`, `max_iterations`, `case_count` |
| `batch_claim` | 领取下一个待执行算子 | `batch_dir` |
| `batch_attach_run` | 关联 run 到批次 | `batch_dir`, `run_dir` |
| `batch_complete` | 完成当前算子 | `batch_dir`, `terminal_state`, `message` |
| `batch_show` | 查看批次状态 | `batch_dir` |

### 信息查询

| 工具名 | 功能 | 关键参数 |
|---|---|---|
| `show_workforce` | 显示调度拓扑 | — |

---

## 9. 目录结构说明

### 项目目录（setup 产出）

```
<project-root>/
  .mcp.json                  # MCP server 注册
  .opci_project_root         # 项目根绝对路径标记
  .claude/
    settings.json            # 权限 + Hooks + sandbox
    agents/*.md              # 6 个 Agent 定义
    skills/*/SKILL.md        # 10 个 Skill 定义
    hooks/*.py               # trace_hook + guard_project_writes
  prompts/                   # 约束提取提示词版本
    operator_constraints_extract_v1.md
    operator_constraints_extract_v2.md
    operator_constraints_extract_v3.md
  operator_docs/             # 算子文档（可自行添加）
  docs/                      # 设计文档
  knowledge/                 # 算子模式参考
  runs/                      # 运行产物（自动生成）
  servers.example.json       # 执行机配置模板
  servers.json               # 实际配置（需手动创建，不入库）
```

### 运行产物目录（自动生成）

```
runs/<operator>-<timestamp>/
  run_state.json             # 唯一真相源：状态、轮次、参数
  inputs/                    # 只读快照（算子文档 + prompt）
  iter_001/
    constraints.json         # 结构化约束（OperatorRule 模型）
    generation_summary.json  # 生成摘要
    cases.json               # 紧凑用例表示
    cases_executor.py        # ATK 执行脚本（含 CPU golden）
    execution_result.json    # 执行结果
    quality_gate.json        # 门禁决策
    analysis.json            # 根因诊断
    prompt_v2.md             # 仅 constraint_extraction 根因时产出
```

### 运行日志目录（MCP 工具调试）

```
<project-root>/logs/
  mcp/                       # MCP 调用日志
    mcp_calls_YYYYMMDD.log    # MCP 工具执行日志（时间戳 + 步骤 + 参数）
  tools/                     # MCP 工具业务日志
    execution_YYYYMMDD.log    # 执行器日志（SSH、ATK、报告解析）
    generate_case_<op>.log   # 用例生成器日志
    generator/
      operator_generator.log  # 生成器兜底日志
    coverage_analyzer.log    # 覆盖率分析日志
```

---

## 10. 运行日志

所有 22 个 MCP 工具内置了步骤级日志，写入项目目录下的 `logs/mcp/`。确定性 Python 模块（生成器、执行器等）的业务日志写入 `logs/tools/`。

### 查看实时日志

```powershell
# PowerShell 实时监控 MCP 调用日志
Get-Content logs\mcp\mcp_calls_20260715.log -Wait -Tail 50

# 查看执行器业务日志
Get-Content logs\tools\execution_20260715.log -Wait -Tail 50

# VS Code 打开（自动刷新）
code logs\mcp\mcp_calls_20260715.log
```

### 日志格式

```
[2026-07-15 11:39:22] validate_constraints :: start | path=E:\...\constraints.json
[2026-07-15 11:39:22] validate_constraints :: step1_operator_rule_validate |
[2026-07-15 11:39:22] validate_constraints_internal :: step1_done | elapsed_s=0.002
[2026-07-15 11:39:22] validate_constraints :: done | elapsed_s=0.003
```

每行记录：时间戳、工具名、步骤标签、关键参数和耗时。

---

## 11. 常见问题

### Q: "opci 不是内部命令或外部命令"？

`uv tool install` 将入口 `opci.exe` 安装到 `%USERPROFILE%\.local\bin\`，该目录可能不在系统 PATH 中。

**诊断**：运行 `where.exe opci`，如果无输出说明 `.local\bin` 不在 PATH 中。

**修复步骤**：

1. **临时生效**（仅当前终端会话）：
   ```powershell
   $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
   ```
2. **永久生效**（推荐，后续所有终端自动生效）：
   ```powershell
   uv tool update-shell
   ```
   如果 `uv tool update-shell` 不生效（取决于 Shell 类型），可手动将 `%USERPROFILE%\.local\bin` 添加到系统环境变量 PATH 中：
   - 打开 **系统设置 → 环境变量 → 用户变量 → Path → 新建** → 输入 `%USERPROFILE%\.local\bin`
3. 验证修复：
   ```powershell
   where.exe opci    # 应显示 %USERPROFILE%\.local\bin\opci.exe
   opci --help       # 应正常输出帮助信息
   ```

> ⚠️ **残留冲突**：如果系统上曾用 `pip install opci` 安装到系统 Python（如 `D:\Python\Python314\Scripts\opci.exe`），该残留入口会优先被 PATH 选中并报错 `ModuleNotFoundError: No module named 'opci'`。详见下面的 [ModuleNotFoundError 问题](#q-opci-mcp-server-报错-modulenotfounderror-no-module-named-opci)。

### Q: 安装依赖很慢？

Z3 solver（~50MB）、torch（~2GB）、scipy 等依赖体积较大。

- **uv 安装**：自动识别当前平台，只下载对应的 torch 包（~200MB），首次安装约 2~5 分钟。
- **pip 安装**：默认会尝试下载所有平台包。务必按照 2.2B 的步骤，先单独安装 CPU-only torch（`--index-url https://download.pytorch.org/whl/cpu`），再安装 opci。

如需使用国内镜像加速：

```powershell
# uv 方式
uv tool install --index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple opci-0.1.0-py3-none-any.whl

# pip 方式
pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple torch  # 先装 torch
pip install --no-deps -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple opci-0.1.0-py3-none-any.whl
pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple fastmcp pydantic numpy scipy z3-solver asyncssh openpyxl jinja2 pyyaml packaging typing_extensions
```

### Q: 更新或重装 opci 版本？

**必须先卸载再安装**，避免旧版本缓存残留：

```powershell
# uv 方式（推荐）
uv tool uninstall opci
uv tool install opci-0.1.0-py3-none-any.whl

# pipx 方式
pipx uninstall opci
pipx install opci-0.1.0-py3-none-any.whl

# pip 方式（venv 内）
pip uninstall opci
pip install opci-0.1.0-py3-none-any.whl
```

> **重要**：更新后需关闭正在使用 opci MCP server 的 Claude Code session（`opci.exe` 可能被锁定），然后再重新启动 Claude Code。直接使用 `uv tool install --force` 跳过卸载可能导致旧版本文件残留，引发不可预期的行为。

### Q: 如何卸载 opci？

```powershell
# uv 方式
uv tool uninstall opci

# pipx 方式
pipx uninstall opci

# pip 方式（venv 内）
pip uninstall opci
```

### Q: opci mcp-server 报错 `ModuleNotFoundError: No module named 'opci'`？

这是 PATH 中残留了旧版 `opci.exe` 入口导致的。`opci.exe` 只是启动器，实际包需在对应 Python 环境的 site-packages 中。残留入口指向的环境没有安装 `opci` 包，所以找不到模块。

**诊断步骤**：

```powershell
# 1. 查看 PATH 上所有 opci 入口
where.exe opci
# 可能看到多个位置，例如：
#   C:\Users\xxx\.local\bin\opci.exe          ← uv tool install（正确）
#   D:\Python\Python314\Scripts\opci.exe       ← 旧 pip install（残留）

# 2. 确认哪个入口指向有 opci 包的环境
#    uv 方式：包在隔离 venv 的 site-packages 中，入口指向该 venv 的 python
uv tool list
# 输出中应包含：opci v0.1.0

#    pip 方式：包在系统 Python 的 site-packages 中
pip show opci
# 如果显示 "Package(s) not found"，说明该 Python 环境没有安装 opci 包
```

**修复步骤**：

```powershell
# 1. 删除残留入口（pip 安装到系统 Python 的幽灵入口）
#    根据 where.exe 的输出，删除非 uv 管理的入口
Remove-Item "D:\Python\Python314\Scripts\opci.exe" -Force

# 2. 确保 uv 工具目录在 PATH 中（详见 2.2A 安装说明）
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"   # 临时生效
# 或永久生效：uv tool update-shell

# 3. 验证修复
where.exe opci    # 应只有 ~/.local/bin/opci.exe（或项目 venv 的）
opci mcp-server   # warmup 日志应为 14 OK, 0 FAIL
```

> **根因说明**：系统上曾用 `pip install opci-xxx.whl` 安装到某个系统 Python，产生了 `opci.exe` 入口。后来该环境中的 `opci` 包被卸载或未正确安装，但 `.exe` 入口仍残留在 Scripts 目录中。PATH 候选时优先选中了残留入口，导致启动器找不到包。`uv tool install` 不会产生此问题——它将包和入口放在同一个隔离 venv 中，入口始终指向有包的环境。

### Q: opci setup 报错"resources directory not found"？

`opci setup` 依赖 wheel 安装时的 `opci/resources/` 目录。确保用 `uv tool install` 或 `pip install` 安装了 wheel 包（不是直接从源码目录运行）。

### Q: validate_constraints 卡住不动？

已在 v0.1.0 修复。MCP server 启动时 `_warmup()` 预加载所有重型 C 扩展（Z3、numpy、torch 等），并将 stdout 重定向到 stderr 以保护协议通道。如果仍出现卡住，检查：

1. Claude Code 版本是否最新
2. `opci` 版本是否为 0.1.0+（`uv tool list` 或 `pip show opci`）
3. 查看 `logs/mcp/` 日志定位卡住的步骤

### Q: servers.json 缺失时报什么？

`init_run` 工具在 `mode=real` 时会校验 `servers.json`。缺失或不完整时返回错误提示，**不会静默回退 Mock**。需手动创建 `servers.json`。

### Q: 如何使用项目外的算子文档？

直接传绝对路径即可：

```
/iterate-operator D:\docs\aclnnFoo.md --max-iterations 3
```

`init_run` 会自动将文档复制为项目内只读快照（`runs/<run-id>/inputs/`），后续 Agent 只使用快照。

### Q: MCP 工具权限怎么配置？

`opci setup` 生成的 `.claude/settings.json` 已预授权所有 22 个 MCP 工具（`dontAsk` 模式）。工具名格式为 `mcp__opci__<Python函数名>`，如 `mcp__opci__validate_constraints`。如需手动添加，在 settings.json 的 `permissions.allow` 列表中追加。

### Q: 如何查看 MCP server 的 warmup 日志？

warmup 日志输出到 stderr（非 stdout 协议通道）。启动 Claude Code 后，MCP server 的 stderr 由 Claude Code 管理，用户通常不可见。如需查看，可以在终端手动运行：

```powershell
opci mcp-server
# 观察 stderr 输出
```

### Q: `.opci/` 目录是什么？

存放运行时辅助数据，已加入 `.gitignore`：
- `.opci_project_root` — 项目根绝对路径标记文件
- `logs/mcp/` — MCP 工具步骤级调用日志
- `logs/tools/` — MCP 工具业务日志（生成器、执行器等）
