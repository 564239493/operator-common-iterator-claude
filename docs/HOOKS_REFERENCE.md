# 守卫插件参考（.opencode/plugins/guard.js）

opencode 原生写入守卫，规则移植自旧版 `.claude/hooks/guard_project_writes.py`
（该脚本随 Claude 编排资产退役）。插件自动被发现加载，无需在 opencode.json 注册。

| 钩子 | 作用 |
|---|---|
| `tool.execute.before` | 对 read/glob/grep/list/edit/write/apply_patch/bash 八类工具做写入门禁：受保护源码只读 + 活动 run 写隔离 + 跨 run 访问禁止 + shell 改文件命令拦截 |
| `permission.ask` | 对进入 opencode 原生询问流程的命令做硬规则复查：命中守卫硬规则的改判 deny，其余保持询问（用户批准后可执行） |

deny → throw 阻断工具并给出原因；静默返回 = 放行（放行兜底由 `opencode.json`
静态 permission 负责）。「需要用户确认」类判断不在 `tool.execute.before` 里以
throw 实现（throw 没有批准通道，等于永久拒绝）：高风险命令的询问由静态 ask
规则承担，opencode 弹原生确认框、用户批准后即可执行；插件在 `permission.ask`
钩子里复查，仅把命中硬规则的改判 deny。
插件自身任何异常一律 fail-closed 转阻断，绝不静默放行。

---

## 一、常量

| 常量 | 值 | 作用 |
|---|---|---|
| `PROTECTED_WRITE_DIRS` | `executer`、`agent/generators`、`.git` | 只读目录（相对项目根，前缀匹配） |
| `PROTECTED_WRITE_FILES` | `servers.json` | 只读文件 |
| `TERMINAL_STATES` | SUCCESS/BLOCKED/MAX_ITERATIONS/STOP_GENERATOR_BUG/STOP_EXECUTOR_BUG/STOPPED_BY_USER | run 终态，触发会话绑定自动释放 |
| `FILE_READ_TOOLS` | read/glob/grep/list | 读类工具 |
| `FILE_WRITE_TOOLS` | edit/write/apply_patch | 写类工具（apply_patch 逐补丁目标送检） |

## 二、run 绑定与隔离（Scope）

1. 绑定文件：`.opencode/runtime/task_scopes/<根会话-id>.json`，原子写（tmp+rename）。
   子代理会话（task 派生，sessionID 各自独立）在守卫入口先沿 `parentID` 链归一到
   根会话（经插件 `client.session.get` 查询，结果进程内缓存），主会话与其全部
   子代理共用同一份绑定；父级链查询失败时退化为按会话各自记账。
2. 首次访问某 `runs/<run-id>/**`（文件工具或唯一引用该 run 的 shell 命令）即绑定；
   已绑定且旧 run 非终态时拒绝切换。
3. 读取 run_state.json 发现终态 → 删除绑定文件自动释放（会话可落地 canonical 修改，
   批准语义仍由 iterate-operator 第 10 步的 question 把关，插件不替代）。
4. 绑定后：文件工具只可写当前 run；读其他 run / `runs/` 根宽泛 glob/grep 拒绝；
   shell 引用其他 run、`runs/x/../` 遍历拒绝。
5. run_id 拒绝含路径分隔符、控制字符与 glob 元字符（`*?[]`），防未展开 glob 被误绑定。

## 三、shell 分类

直接 deny：内联代码（`python -c` / `python -` / heredoc 变体 / `node|perl|ruby|bun|osascript -e` / `php -r` / `deno eval`）、
generation_progress.py 的变量/管道/循环包装、跨 run 遍历、重定向或写删命令
目标在项目外或受保护路径（含任意层级的 `run_state.json`，堵「自标终态换绑」）、
写删命令带未解析变量（`$VAR`）。
询问（由静态 ask 规则承担，批准后可执行）：删除/移动、依赖与环境变更、curl/wget、
Git 写操作、系统/进程变更、项目外 Python 入口、`git apply`/`git restore`/`rsync`。
shell 层改文件命令命中后与写删命令走同一套目标检查——形态型（sed -i / tar 解压 /
unzip -d / dd of= / find -delete 等）任意位置匹配；裸命令词型（install / rsync /
patch / truncate / ed / git apply / git restore）仅在 shell 段首匹配，避免
`pip install` 这类参数位置同名词误伤。直接读取 `.env`/`.env.*` 的命令拒绝。
路径判定先解析符号链接（realpath）再比对边界，`runs/<id>/软链` 逃逸落到真实路径
受检；sed/perl 替换表达式（`s/…/…/`）不当作写入目标。
自动信任：项目内 `.py` 入口；项目外入口由静态 `python /*` 系 ask 承担询问。

## 四、fail-closed

`guard()` 抛出的任何异常在插件层统一转为「守卫插件自检失败（fail-closed），
需要用户确认: …」。守卫失效的最坏情况是多问用户，而不是漏拦。

## 五、测试与验证

`guard.test.js` 随仓库入库；在仓库根目录运行：

```bash
bun test ./.opencode/test/guard.test.js   # 60 个回归用例（仅 bun 运行时；测试文件
                                           # 不放 plugins/ ——该目录被启动时自动加载）
python3 scripts/validate_project.py           # 项目级静态校验
```

用例覆盖：受保护路径拒绝、apply_patch 逐文件送检（patchText 标记行与补丁数组
两种形态、跨 run 写、Move to 目标）、run 隔离绑定与跨 run 拒绝、子代理沿
parentID 链共享根会话绑定（含查询失败退化）、内联 python/node（含 `-X utf8`/
`py`/`pythonw`/heredoc/`-e` 变体）与包装监控拒绝、只读命令引号内关键词不误伤、
shell 层改文件命令（sed -i/tar 解压等）与 `.env` 读取、询问归静态规则语义、
scope run_id 格式校验（`*`/空串视为未绑定）、项目内 .py 放行、fail-closed
异常转报错。
