---
description: 从 constraints.json 生成 ATK JSON、TTK ACLNN CSV 或 torch_npu TTK E2E CSV。
---

# 用例生成规范

先校验约束，再执行：

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <cases.json> --count <N> --test-framework atk
```

上面是 `generate_cases.py` 的参数形式（仅参考参数）。**实际运行一律经
`scripts/generation_progress.py launch` 启动，无论任务长短**：直接前台/后台调
`generate_cases.py` 会让 `Read` 把生成器逐条 verbose stderr
（`logger_util.ThreadSafeLogger` 默认 `console_output=True` → `StreamHandler` → stderr）拉进 agent
上下文撑爆窗口；更致命的是把 `generate_cases.py` 挂成**长寿命后台 bash 任务**——后台 bash 任务绑定
所属会话生命周期，会话被中断/重启/上下文压缩时整棵进程树被杀（无 60 分钟上限，是会话生命周期杀，
不是固定时长杀），留下冻结的 `generation_progress.json`（`state=running/pid_alive=true` 从不写终态）。
故本项目用**脱离会话的子进程**：launcher 用前台 `Bash`（~1 秒、exit 0）把 `generate_cases.py` 以
`CREATE_BREAKAWAY_FROM_JOB|CREATE_NEW_PROCESS_GROUP`（Windows）/ `start_new_session=True`（POSIX）
拉成脱离 job/session 的子进程，verbose 重定向到 `<iter>/generation_console.log`（只落盘、不进任何捕获流），
写一行 `generation_progress.json` 标记后**自身立即退出**——从此无长寿命 bg 任务可杀，唯一长寿命进程
是脱离会话的 `generate_cases.py`，`scripts/probe_breakaway.py` 已证其在 launcher 退出后存活到完成。
本项目 `settings.json` 是 `default` 模式且未 allow 任何 Bash/PowerShell——**每条 shell 命令都弹权限询问**
（只有 `Read`/`Glob`/`Grep` 不弹）；`generation_progress.py` 由 guard hook 自动 allow `.py` 入口、不弹询问。

launcher 命令（`--` 后是 `generate_cases.py` 的参数，不要带 `python`/脚本名）：

```text
python scripts/generation_progress.py launch --output-dir <iter> \
  -- --constraints <constraints.json> --output <iter>/cases.json \
  --count <N> --test-framework atk
```

launcher 在 ~1 秒内 stdout 打印**一行** JSON 标记（`pid`/`started_epoch`/`requested`/
`breakaway`/`state=running`）并写同名 `generation_progress.json`，然后 exit 0。
逐条 flush 的 JSONL 断点行数由后续 `status` 轮询读出，作为真实进度写入极小的
`generation_progress.json`（含 `state`/`per_platform.done`/`pid_alive`/`elapsed`）。

### 监控纪律（关键，违反即前功尽弃或弹非业务询问）

**角色分工**：生成一律由 case-generator 子 Agent 用**前台** `Bash` 跑 `generation_progress.py launch`
（~1 秒、exit 0）后**报告 pid/`<iter>` 路径、结束本轮**——子 Agent 寿命只有 ~1-2 分钟，无法等待
（同步执行，结束本轮即结束，收不到异步通知）。故**无论长短，一律脱离启动 + 交棒，等待与校验
由主协调器负责**。

每条 Bash/PowerShell 都弹询问，`Read`/`Glob`/`Grep` 不弹。据此（主协调器适用）：

- **等待完成（无询问）**：每 ~60 秒**前台**跑一次
  `python scripts/generation_progress.py status --output-dir <iter>` 读 stdout 那行 JSON 的 `state`：
  `running` 则再等一轮，`complete`（`generation_summary.json` 已产出）则 `validate_artifacts.py cases`，
  `failed` 则读 `error` 字段。`status` 自身 ~1 秒 exit、stdout 只一行，反复调不撑爆上下文。
  **禁止**据此换 grep/sleep 轮询。
- **查进度（无询问，主协调器适用）**：每次 `status` 返回后**必须**向用户报告
  `per_platform.done`/`total`/`elapsed`/`pid_alive`（递增即活跃），再发起下一次——每 ~60 秒给用户一次
  进度，**不得**用"平台名看起来不对"之类的旁支判断替换进度数字。不循环 Read。
- **轮询回合排他（强制，防进度丢失）**：`state=running` 期间主协调器每回合**只**做"跑 status → 报进度
  → 决定下回合"；禁止在轮询回合发起探查性 Read/Grep/平台选择调查/记忆回溯/长思考——旁支疑虑推迟到
  complete/failed 之后，**绝不用调查取代一次轮询**。进度一旦从某回合起长时间空白，根因几乎都是
  "本该轮询的回合被旁支调查/长考占用"——这正是"有时有进度、有时没进度"的唯一可控根因。
- **`per_platform` 语义（防误判调查）**：running 期间 `per_platform` 列的是**正在生成的目标平台**
  （`generate_platform_outputs` 按 `product_support` 逐平台全部生成），**不是执行/canonical 平台**；
  canonical 平台在全部生成完后由 `_select_ttk_platform` 按 `servers.json` 选定。故 `per_platform` 出现
  `servers.json` 未覆盖的平台（如 A3）属**正常**、不是选错平台，**禁止**据此调查或 kill 重启；
  仅在 `state=complete` 后 `generation_summary.json.selected_platform` 不符 `servers.json` 时才处理。
  详见 `iterate-operator` skill 等待段。
- **progress.json 只是 advisory**：是否完成**只**以 `status` 的 `state=complete`（`generation_summary.json`
  存在）或 `failed` 为准。`state=running` 期间 `per_platform` 可能短暂空或残缺（多平台任务里已完成平台
  的 JSONL 被 convert 删掉、转成 `cases_<plat>.json` 而从进度里"消失"，属正常平台间过渡），**绝不**据此
  停掉生成进程。**绝不在已有 `cases_<plat>.json` 时重跑 `generate_cases.py`**——
  `generate_platform_outputs:192` 每平台先 `target.unlink` 删 `cases_<plat>.json` 再生成，重跑=丢弃已完成
  平台数小时成果；脱离进程被异常中止时（部分平台有 cases、部分没有、缺 `generation_summary.json`）
  **报告现状**让用户定夺，不自行重跑。
- **禁止**：`Bash`/`PowerShell` 跑 `until`/`grep`/`tail -f`/`while`/`sleep`/`ps` 循环监视任何日志
  （弹非业务询问且是 verbose 源）；`python -c`/shell 查活；`Read` `generation_console.log` 或
  `logs/generate_case_*.log` 全文；重复启动仍在运行的同任务（`status` 的 `pid_alive=true` 即仍在跑）。
- 失败时：`status` JSON 的 `error` 字段已含**有界**错误摘录（≤20 行），据此报告 `generator_bug`，
  不自行解析日志。

生成过程中默认把每个成功用例立即写入
`<output-dir>/jsonl_checkpoints/<platform>/<operator>.jsonl` 并 flush；各平台目录隔离，
不会互相覆盖。可用 `--jsonl-save-path <dir>` 覆盖 checkpoint 根目录。

**重要：`--count` 是每个产品（platform）独立生成的数量，不是总数量。**
例如 `--count 100` 对 3 个产品会生成约 300 条用例（每个产品 100 条），
**禁止**将 count 除以产品数后再传入。脚本和 facade 内部已按 per-platform 处理，
调用方传入原始期望值即可。

随后执行 `python scripts/validate_artifacts.py cases <cases.json>`。禁止手工补造生成失败
的 case。保留 `<iter-dir>/generation_summary.json` 作为数量和平台摘要。ATK 路径下 cases
校验不通过即中断 GENERATE，不得因告警删除已生成用例或绕过校验继续。

若 `run_state.json.test_framework == "ttk"`，改为：

```text
python scripts/generate_cases.py --constraints <constraints.json> --output <iter>/cases_ttk.csv --count <N> --test-framework ttk --hs-scenario-mode <run_state.hs_scenario_mode> --server-config servers.json
```

所有产品的 `cases_<platform>.json` 仍分别生成并保留；用于 `cases.json` 和
`cases_ttk.csv` 的 canonical 平台不再取 `product_support` 第一项，而是按
`servers.json` 中服务器顺序及各服务器 `platforms` 顺序，选择第一个已有用例桶的平台。
人工调试可用 `--platform <精确平台名>` 覆盖。选择结果和原因写入
`generation_summary.json.selected_platform/platform_selection_reason`。

torch_npu TTK 默认使用 `--hs-scenario-mode original`，完全使用原有
`agent/generators` 逻辑，不做 `tnd` / `bsnd` / `paged_attention`
场景拆分。只有用户显式选择场景拆分时才使用：

```text
--hs-scenario-mode planned
```

`planned` 才做场景拆分和投影。实际值必须从 `run_state.hs_scenario_mode`
透传；兼容旧 run，该字段缺失时使用 `original`，不得在 GENERATE 阶段重新决定。

TTK 与 ATK 一样，`count` 表示每个平台请求生成的统一中间用例数；实际数量以
`generation_summary.json` 为准，禁止复制相同 baseline 凑数。TTK 必须先由正式约束生成器产生 `<iter>/cases.json`；
CSV 只是该统一中间模型的框架 adapter 产物。同时检查 `ttk_conversion_audit.json`，
禁止手写 CSV 绕过 Z3 生成结果。`operator_family=hs` 默认不要求
`golden_manifest.json`；`operator_family=aclnn` 使用 TTK 原生 ACLNN runner，
同样不生成也不要求 E2E Golden plugin/manifest。

## 生成后诊断

`post_check_report.json` 不是必需产物，默认不创建。Z3 约束、Python 复检、
场景覆盖与 domain coverage 的问题可保留在 `generation.log`、
`generation_summary.json` 或转换 audit 中。

- **ATK**：`validate_artifacts.py cases` 不通过即阻断 GENERATE，不得降为 warning 继续。
- **TTK**：Golden 覆盖率、准确度、场景覆盖率只记 warning，不删除用例、不阻断功能流程；
  但所选执行平台 `semantically_clean_count=0` 时必须由生成器以
  `HS_SEMANTIC_GATE_FAILED` 阻断，禁止进入 TTK 转换/EXECUTE。`planned` 模式缺失其
  计划内必需场景时同样阻断。

当前正式工作流没有独立 post-check CLI，`post_check_report.json` 也不属于产物契约。
生成阶段只调用上述生成入口和 `validate_artifacts.py cases`。如以后正式增加复检，
应先实现项目入口、产物契约与测试，再更新本 Skill。

正式生成器调试日志按算子和平台分别写入
`logs/generate_case_<operator>_<platform>.log`。同一平台的分场景生成共用该平台日志，
不同平台不得混写到同一个 `generate_case_*.log`。
