# 权限与任务隔离

项目级配置位于 `.claude/settings.json`。权限采用“静态规则 + 动态 Hook + OS
sandbox”三层控制；`CLAUDE.md` 中的文字约定只负责引导，不作为安全边界。

## 权限模式

- `defaultMode: default`：未命中的有副作用操作会询问，不再使用 `dontAsk` 的直接拒绝。
- `disableBypassPermissionsMode: disable`：禁止
  `--dangerously-skip-permissions` 绕过权限。
- `Read`、`Glob`、`Grep`、`Agent`、`Skill` 保留静态授权；移除 `servers.json` 的读取
  deny。Bash/PowerShell 不维护逐命令静态 allow，统一由 PreToolUse Hook 动态分类。
- Hook 校验通过的普通命令直接 `allow`，包括项目目录内任意 `.py`（含 `scripts/`、
  `executer/`、`agent/generators/`）、SSH/SCP/SFTP/rsync、只读命令、管道、状态输出、
  后台任务读取，以及 Windows 的普通 `powershell -Command`；新增项目脚本不需要同步
  修改 settings。执行受保护目录代码不等于允许修改它们。Hook 启动失败时没有宽泛
  Shell allow 可兜底，安全回退到 `default` 询问。
- `python -c`、`python -`、跨 run 和受保护路径操作直接 `deny`；依赖/环境变更、Shell
  求值、curl/wget 外部下载、Git 写操作、系统/进程变更、删除/移动和项目外 Python
  文件统一 `ask`。PowerShell `-EncodedCommand` 和 `Invoke-Expression` 仍需询问；普通
  `-Command` 内的文件操作继续接受 Hook 路径检查。运行阶段遇到缺失依赖必须报告并
  停止，Agent 不得自行调用 pip/uv。
- `python -c`、`python -` 与 Python heredoc 由 PreToolUse Hook 直接拒绝，不再进入权限
  询问；JSON 汇总和检查使用 Read/Grep 或已有 `scripts/*.py`。
- 运行任务不创建一次性辅助 `.py` 后再删除；正式 JSON/Markdown 产物直接写入当前 run，
  确定性逻辑使用项目已有脚本，避免把删除权限当作临时脚本清理机制。
- Tool 输出已包含进程 exit code，通常无需追加状态探针；普通管道和状态输出由 Hook
  按实际副作用判定，不再为每种命令形态维护许可清单。
- 业务 CLI 短任务默认前台执行；可能超过前台上限的长任务允许后台执行，但输出只能
  通过 Claude 的 Read/TaskOutput 能力获取，不使用 `while`/`ps`/`sleep` 轮询，不为
  Shell 控制流或项目外临时 task output 路径扩充权限。
- Bash `mkdir -p` 与 PowerShell `New-Item` 创建当前 run 目录时由 Hook 自动允许；目标
  仍须通过当前 run 边界检查。`init_run.py` 已预建 `iter_001`，通常无需重复创建。
- `autoAllowBashIfSandboxed: false`：不依赖 OS sandbox 自动批准，Windows/Linux 使用
  相同的 Hook 分类；显式询问和高危操作仍由用户决定。

规则判定顺序是 `deny → ask → allow`，更具体的 allow 不能覆盖较宽的 deny。

## 静态限制

- 允许读取 `servers.json`，但禁止 Edit/Write。执行脚本可以读取连接配置；输出和日志
  不得回显密码、密钥或完整配置。
- `executer/**` 与 `agent/generators/**` 只允许读取、导入和执行；禁止新增文件或子目录，
  也禁止 Edit/Write/删除/移动/复制覆盖。
- 禁止内置文件工具修改 `.git/**`。
- 禁止读取任意位置的 `.env` 与 `.env.*`。
- 文件路径权限统一使用 `Read(...)` 与 `Edit(...)`；新版本 Claude Code 不使用
  `Write(path)`、`NotebookEdit(path)`、`Glob(path)` 做路径权限判断。

## 当前任务目录隔离

`.claude/hooks/guard_project_writes.py` 挂到 `PreToolUse`：

1. 首次通过文件工具访问任意 `runs/<run-id>/**`，或首次 Shell 命令明确引用唯一一个
   run 时，按 Claude session 绑定活动 run；主 Agent 和所有子 Agent 共用这个绑定。
2. 活动任务中只允许文件工具写 `runs/<run-id>/**`。
3. 禁止 Read/Glob/Grep/Edit/Write 访问其他 `runs/<other-run-id>/**`。
4. 禁止在 `runs/` 根上做宽泛 Glob/Grep，避免一次搜索扫入其他任务。
5. Shell 命令只要同时引用多个 run、显式引用其他 run、通过 `..` 跨 run、向项目外
   重定向或修改受保护目录，即被 Hook 拒绝。
6. 只有当前 run 已进入终态后，批次流程才可绑定下一个 run。

`runs/batches/<batch-id>/` 是目录批次的调度状态，不视为某个算子的业务任务目录；
只有 `init_batch.py` / `batch_state.py` 可按工作流更新它。

Hook 状态保存在 `.claude/runtime/task_scopes/<session-id>.json`，属于权限审计元数据，
不属于业务产物。

这里的隔离是“同一工作树内按当前 run 路径隔离”，不是 Git worktree 隔离。流水线
Agent 必须共享当前工作树；配置与 Hook 会拒绝 `EnterWorktree` 和 Agent 的
`isolation: worktree`，避免临时 worktree 清理后丢失尚未提交的阶段产物。

注意：Claude Code 的 `@file` 提示词引用不会触发 `PreToolUse`。因此动态 Hook 无法单独
拦截用户主动输入的 `@runs/other-run/...`。执行任务时不要主动引用其他 run；需要不可
绕过的强租户隔离时，应为每个任务使用独立容器/工作副本，并只挂载当前 run。

## Sandbox

- macOS、Linux、WSL2：启用 Claude OS sandbox，禁止子进程新增或改写
  `executer/`、`agent/generators/`、`servers.json` 与 `.git/`。
- 原生 Windows：Claude OS sandbox 不可用，动态 Hook 是回退边界。高强度隔离建议
  使用 WSL2 或容器。
- `allowUnsandboxedCommands: false`：不允许命令自行关闭 sandbox。
- `failIfUnavailable: false`：兼容原生 Windows；启动时会警告而不是退出。

## Windows / Linux 一致用法

用户启动 Claude Code 前应创建有效的项目虚拟环境：

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
python --version
claude
```

```bash
# Linux / macOS / WSL2
source .venv/bin/activate
python --version
claude
```

Hook 直接通过 `python -X utf8` 运行，不依赖 Node、Bash、盘符或固定 Python 安装目录。
项目唯一的启动前置条件是 Python 3 已加入 `PATH`，且 `python --version` 可正常执行；
UTF-8 模式用于避免 Windows 中文输出乱码。Hook 只使用 Python 标准库，不要求此时已安装
项目业务依赖。
业务脚本允许 Claude Code 常用的显式虚拟环境入口：
`./.venv/Scripts/python.exe` / `.venv/Scripts/python.exe`（Windows）和
`./.venv/bin/python` / `.venv/bin/python`（Linux/macOS/WSL2）；可执行项目内任意
`.py`，是否允许修改仍由路径保护规则独立决定。

Agent 执行项目脚本时不得先运行 `source`/`activate`；应直接使用上面的虚拟环境
Python 路径。只要系统 `python` 命令可用，用户进入项目后即可直接运行 `claude`，
无需为了 Hook 手工激活 `.venv`。

Hook 将 `/dev/null`、Windows `NUL`、PowerShell `$null` 和 `2>&1` 视为非持久化
输出，不按项目外写入拦截。

## 验证

重启 Claude Code 后运行：

```text
/status
/permissions
/hooks
```

并执行：

```bash
python scripts/validate_project.py
python scripts/test_permission_guard.py -v
```

若启动器报告找不到可用 Python，应重建 `.venv`；不要把某台机器的 Python 绝对路径
写回共享 settings。Agent 不需要也不应执行虚拟环境激活命令。
