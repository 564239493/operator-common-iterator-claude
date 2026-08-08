# Hooks 参考手册

项目 `.claude/hooks/` 下两个正式注册的 hook 脚本，配置在 `.claude/settings.json`：

| 事件 | matcher | 脚本 | 一句话总结 |
|---|---|---|---|
| `PreToolUse` | `Read\|Glob\|Grep\|Edit\|Write\|NotebookEdit\|Bash\|PowerShell\|Agent\|EnterWorktree` | `guard_project_writes.py` | 写入门禁：受保护源码只读 + 活动 run 写隔离 + 跨 run 访问禁止 + 高风险 shell 转 ask，并强制 Agent 共享工作树 |
| `SessionStart` | （全部） | `trace_hook.py` | 会话开机时报 workforce 清单（skills/agents 数量与列表） |
| `SubagentStart` | （全部） | `trace_hook.py` | 子 Agent 启动时打印 `[SCHEDULER] START` 调度行 |
| `SubagentStop` | （全部） | `trace_hook.py` | 子 Agent 结束时打印 `[SCHEDULER] STOP` 调度行 |

两个脚本均以 `python -X utf8` 执行，stdin 收 JSON payload，靠 `CLAUDE_PROJECT_DIR` 定位项目根。trace 是只读观察者永不阻断；guard 是权限门禁返回 `permissionDecision`(allow/deny/ask)。

---

## 一、`trace_hook.py`

**职责**：会话/子 Agent 启停事件的可观测性留痕。展示给主会话 + append 到 `.claude/runtime/schedule.jsonl`（不入库）。写日志失败只降级注释，绝不阻断流程。

### 常量

无模块级配置常量。

### 业务方法

| 方法 | 作用 |
|---|---|
| `project_dir(payload) -> Path` | 定项目根：`CLAUDE_PROJECT_DIR` 环境变量 → payload `cwd` → `.`，`.resolve()` |
| `registry_summary(root) -> str` | 扫 `.claude/skills/*/SKILL.md` 与 `.claude/agents/*.md`，拼 `[WORKFORCE] skills=N [...] \| agents=M [...]` 一行；仅 SessionStart 输出 |
| `message_for(payload, root) -> str` | 按事件生成展示文本：SessionStart→workforce 清单；SubagentStart→`[SCHEDULER] START agent=.. id=..`；SubagentStop→`[SCHEDULER] STOP ...` |
| `main() -> int` | 读 stdin JSON → 组 event dict → append 写 `schedule.jsonl`（失败降级注释不抛错）→ `print({"systemMessage": ...})` 回会话 → 返回 0 |

---

## 二、`guard_project_writes.py`

**职责**：PreToolUse 时按四条边界判定 allow/deny/ask —— (1) `executer/`、`agent/generators/`、`.git/`、`servers.json` 只读；(2) 活动任务写入只能落当前 `runs/<run-id>/`；(3) 不得访问其他 run；(4) 禁内联 python / 跨 run `../` 穿越等高风险 shell，高风险类转 ask。

### 2.1 常量

| 常量 | 作用 |
|---|---|
| `PROTECTED_WRITE_DIRS` | `("executer", "agent/generators", ".git")` 禁止写入的目录 |
| `PROTECTED_WRITE_FILES` | `("servers.json",)` 禁止写入的凭据文件 |
| `WRITE_EXEMPT_PREFIXES` | 维护期临时豁免前缀，当前为空（常态全护） |
| `TERMINAL_STATES` | `{SUCCESS, BLOCKED, MAX_ITERATIONS, STOP_GENERATOR_BUG, STOP_EXECUTOR_BUG}` run 终态集，终态后释放会话 scope |
| `FILE_READ_TOOLS` | `{Read, Glob, Grep}` 走文件读门禁分支 |
| `FILE_WRITE_TOOLS` | `{Edit, Write, NotebookEdit}` 走文件写门禁分支 |
| `SHELL_TOOLS` | `{Bash, PowerShell}` 走 shell 门禁分支 |
| `WRITE_OR_DELETE` | 匹配 `rm/mv/cp/mkdir/touch/set-content/...` 等写删命令词 |
| `EXTERNAL_PATH` | 从命令抠路径（带引号/bare/Windows 盘符/UNC/`..`/`~`/绝对） |
| `REDIRECTION` | 抠 `>`/`>>`/`2>` 重定向目标 |
| `RUN_REFERENCE` | 抠 `runs/<run_id>` 的 run_id（排除 `runs/batches`） |
| `RUN_TRAVERSAL` | 抓 `runs/<id>/../` 跨任务穿越 |
| `PROTECTED_SHELL_REFERENCE` | 抓 shell 段里 `executer/agent/generators/servers.json/.git` |
| `INLINE_PYTHON` | 抓 `python -c`/`python -` 内联代码 |
| `PYTHON_COMMAND` | 仅当 python 是命令段开头才算执行（避免误判路径里的 python.exe） |
| `DESTRUCTIVE_COMMAND` | `rm/mv/del/move-item/...` 删移命令 |
| `HIGH_RISK_PATTERNS` | 5 类高风险：依赖/环境变更、Shell 求值(source/eval/iex/ps -enc)、外部下载(curl/wget)、Git 状态变更、系统/进程变更(sudo/kill/reg...) |

### 2.2 路径解析与判定

| 方法 | 作用 |
|---|---|
| `project_root(payload) -> Path` | 定项目根：`CLAUDE_PROJECT_DIR` → `cwd` → `.` |
| `native_path_text(path_text) -> str` | Git Bash 路径转 Windows（`/c/..`→`C:/..`）+ expandvars/expanduser |
| `is_inside(path_text, root) -> bool` | 路径是否在项目根下 |
| `resolved_path(path_text, root) -> Path` | 解析成绝对 Path（含 native 转换 + relative 补 root） |
| `is_null_sink(path_text) -> bool` | 是否空设备（`/dev/null`/`nul`/`$null`），重定向到这不算写入 |
| `relative_parts(path, root) -> tuple\|None` | 路径相对 root 的各段；不在 root 下返回 None |
| `run_id_for_path(path, root) -> str\|None` | 从路径提 run_id：须 `runs/<id>` 且非 `batches` |
| `run_key(run_id) -> str` | `os.path.normcase` 归一化 run_id 大小写（跨平台比较） |
| `is_runs_container(path, root) -> bool` | 是否 `runs/` 容器级（无具体 run_id），用于拦整 runs 扫描 |
| `is_protected_write(path, root) -> bool` | 是否命中受保护目录/文件（豁免前缀优先，当前空） |

### 2.3 会话 scope 绑定（per-run 隔离核心）

会话（含全部子 Agent，按 `session_id` 聚合）绑定到一个 run_id，存 `.claude/runtime/task_scopes/<session>.json`。

| 方法 | 作用 |
|---|---|
| `scope_file(payload, root) -> Path` | 把 session_id 清洗成文件名，拼 scope 文件路径；同会话子 Agent 共享同一文件=同一 run 边界 |
| `read_scope(payload, root) -> str\|None` | 读当前绑定的 run_id；校验 `_valid_run_id`；run 已终态→删 scope 返回 None（释放以放行 canonical 编辑） |
| `_valid_run_id(run_id) -> bool` | 拒 `*`/`runs`/`batches`/含分隔符（防 `runs/*` 锁死全会话） |
| `write_scope(payload, root, run_id)` | 原子写 scope 文件（`.tmp`+`replace`） |
| `run_is_terminal(root, run_id) -> bool` | 读 `runs/<id>/run_state.json` 的 state 是否在终态集 |
| `bind_scope(payload, root, run_id) -> (bool, reason\|None)` | 已绑同 run→放行；已绑别 run 且未终态→拒；否则写 scope 绑新 run |

### 2.4 抽取与门禁

| 方法 | 作用 |
|---|---|
| `extracted_paths(text, pattern) -> list[str]` | 按命名组 `double`/`single`/`bare` 抠路径，去尾 `,)` |
| `tool_paths(tool_name, tool_input, root) -> list[Path]` | 按工具取目标路径：Read/Edit/Write→`file_path`；NotebookEdit→`notebook_path`；Glob/Grep→`path`（缺省 root） |
| `guard_file_tool(payload, root) -> str\|None` | 文件工具门禁：受保护路径拒写；Glob/Grep 整 runs 拒；跨 run 拒；写非当前 run 拒；未绑但路径在 run 下→bind_scope |
| `guard_shell(payload, root) -> str\|None` | shell 门禁：禁内联 python；禁 `runs/../` 穿越；多 run 引用拒；重定向/写删目标逐个查项目内/受保护/当前 run |
| `_quoted_spans(text) -> list[(start,end)]` | 引号内子串区间，跳过引号里的写删关键词（数据非命令） |
| `_shell_refs_exempt(segment) -> bool` | 维护期豁免判定；前缀空（常态）恒 False |
| `all_python_entries_are_project_files(command, root) -> bool` | python 入口是否全是项目内 `.py`；是→自动信任，否→ask |
| `shell_approval_reason(command, current, root) -> str\|None` | 返回"需用户确认"原因（不 deny 转 ask）：删移、高风险类、python 入口非项目内、未绑 scope 的写/重定向 |

### 2.5 输出与主入口

| 方法 | 作用 |
|---|---|
| `decision_json(decision, reason) -> str` | 组 `hookSpecificOutput`：PreToolUse + permissionDecision(allow/deny/ask) + reason |
| `main() -> int` | 总调度：文件类→`guard_file_tool`；shell 类→`guard_shell`+`shell_approval_reason`；`EnterWorktree`→deny；`Agent` isolation=worktree→deny 否则 allow。有 decision 则 print，返回 0 |

---

## 附：`test_guard_project_writes.py`

未在 settings.json 注册，是 `guard_project_writes.py` 的单元测试（跨 run 访问、受保护路径、内联 python、重定向、终态释放 scope 等用例），不影响线上流程。
