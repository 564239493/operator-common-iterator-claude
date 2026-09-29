# 权限与任务隔离（opencode 原生）

项目级配置位于 `.opencode/opencode.json` 与 `.opencode/plugins/guard.js`。权限采用
「静态 permission 规则 + 动态守卫插件」两层控制；`AGENTS.md` 中的文字约定只负责引导，
不作为安全边界。

## 静态 permission 规则（.opencode/opencode.json）

- `edit`：默认 ask（改 `docs/`、`knowledge/`、`scripts/`、`src/`、`.opencode/` 等项目
  文件需用户确认）；`runs/**` 放行（任务产物免打扰）；`executer/**`、
  `agent/generators/**`、`.git/**`、`servers.json`、`run_state.json` 一律 deny
  （含 `**/` 前缀变体，路径形式无关）。
- `read`：默认放行；`.env` 与 `.env.*` deny（含 `**/` 变体）。
- `bash`：`*: allow` 打底；依赖/环境变更（pip/uv/npm/apt 等）、curl/wget、Git 写操作、
  sudo/chmod/kill 等系统命令统一 ask；删除/移动（rm/mv/del 等）、项目外绝对路径的
  python 入口、`git apply` / `git restore` / `rsync` 也走 ask；`python -c*` /
  `python3 -c*`（含路径前缀变体）与 `node -e*` / `node --eval*` 直接 deny。
- **ask 的语义**：静态 ask 由 opencode 原生确认框承担——用户批准后命令即可执行。
  守卫插件在 `permission.ask` 钩子里对进入询问流程的命令做同一套硬规则复查，
  命中硬规则的改判 deny（不给批准通道），其余保持询问。
- 规则对象按插入顺序求值，**后匹配者生效**：宽规则在前、窄规则在后；新增窄规则时
  必须放在对应宽规则之后，`scripts/validate_project.py` 会校验 `*` 仍居 bash 首位。

## 动态守卫插件（.opencode/plugins/guard.js）

守卫插件挂在 `tool.execute.before`，覆盖静态规则无法表达的运行期逻辑：

1. 首次通过文件工具访问任意 `runs/<run-id>/**`，或首次 shell 命令明确引用唯一一个
   run 时，按会话绑定活动 run（绑定文件 `.opencode/runtime/task_scopes/<session-id>.json`，
   不入库）；主会话与所有子智能体共用这个绑定。
2. 活动任务中只允许文件工具写 `runs/<run-id>/**`（edit/write/apply_patch 逐文件检查；
   apply_patch 从补丁文本 `patchText` 的 `*** Add/Update/Delete File:` 与
   `*** Move to:` 标记行提取全部目标路径送检）。
3. 禁止 read/glob/grep/list 访问其他 `runs/<other-run-id>/**`。
4. 禁止在 `runs/` 根上做宽泛 glob/grep，避免一次搜索扫入其他任务。
5. shell 命令同时引用多个 run、显式引用其他 run、通过 `..` 跨 run、向项目外重定向、
   修改受保护目录、用变量/管道/循环包装 `generation_progress.py` 监听、或内联执行
   代码（`python -c` / `python -` / heredoc / `node -e` / `perl -e`）时，直接拒绝。
6. 直接读取 `.env` / `.env.*` 的 shell 命令（cat 等）直接拒绝（`.env.example` 豁免）。
7. 「绕过文件工具在 shell 层改文件」的命令（`sed -i`、`perl -pi`、`git apply`、
   `tar` 解压、`unzip -d`、`dd of=`、`find -delete` 等）按启发式识别，与 rm/mv
   走同一套写入目标检查（受保护路径 / run 隔离 / 项目外路径）。旧架构的 OS 级
   sandbox 在 opencode 无对应配置位，此层为启发式补位而非内核强制——覆盖常见
   变体但不承诺穷尽。
8. 「需要用户确认」类判断不在插件内以 throw 实现（throw 没有批准通道，等于永久
   拒绝）：高风险命令的询问全部由静态 ask 规则承担，用户在原生确认框批准后即可
   执行；插件仅在 `permission.ask` 钩子里复查并改判硬规则命中者。
9. 项目内 `.py` 入口（含 `scripts/`、`executer/`、`agent/generators/`）自动信任放行；
   执行受保护目录代码不等于允许修改它们。

**fail-closed 语义**：守卫插件自身任何异常（正则失效、参数形态未预料）一律转
「需要用户确认」，绝不静默放行。这与「钩子进程启动失败时静态放行兜底」的旧风险
模型相反——守卫失效时最坏情况是多问用户，而不是漏拦。

`runs/batches/<batch-id>/` 是目录批次的调度状态，不视为某个算子的业务任务目录；
只有 `init_batch.py` / `batch_state.py` 可按工作流更新它。

这里的隔离是「同一工作树内按当前 run 路径隔离」，不是 Git worktree 隔离。流水线
子智能体共享当前工作树交接 `runs/` 产物。

## 静态限制（与旧版一致的语义）

- 允许读取 `servers.json`，但禁止修改；输出和日志不得回显密码、密钥或完整配置。
- `executer/**` 与 `agent/generators/**` 只允许读取、导入和执行；禁止新增、修改、删除。
- 禁止内置文件工具修改 `.git/**`。
- 禁止读取任意位置的 `.env` 与 `.env.*`（文件工具由静态 permission 拦截；shell 侧 cat 等由守卫插件拦截，启发式）。
- 运行任务不创建一次性辅助 `.py` 后再删除；正式 JSON/Markdown 产物直接写入当前 run。

## 运行前提

守卫插件是纯 JS（运行于 opencode 内嵌 Bun），无 Python、无 `.venv` 依赖。
业务脚本仍建议使用项目虚拟环境解释器（Windows `.venv/Scripts/python.exe`，
Linux/macOS `.venv/bin/python`），Agent 不得先运行 `source`/`activate`。
守卫把 `/dev/null`、Windows `NUL` 视为非持久化输出，不按项目外写入拦截。

## 验证

重启 opencode 后运行：

```bash
python3 scripts/validate_project.py
bun test ./.opencode/plugins/guard.test.js   # 守卫回归测试（已入库）
```

负向手工用例：尝试 edit `executer/runner.py`（应被静态 deny）、尝试 bash
`python -c "print(1)"`（应被静态 deny）、绑定 run 后读其他 run（应被插件 deny）。
