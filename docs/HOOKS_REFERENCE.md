# 守卫插件参考（.opencode/plugins/guard.js）

opencode 原生写入守卫，规则移植自旧版 `.claude/hooks/guard_project_writes.py`
（该脚本随 Claude 编排资产退役）。插件自动被发现加载，无需在 opencode.json 注册。

| 钩子 | 作用 |
|---|---|
| `tool.execute.before` | 对 read/glob/grep/list/edit/write/apply_patch/bash 八类工具做写入门禁：受保护源码只读 + 活动 run 写隔离 + 跨 run 访问禁止 + 高风险 shell 转 ask |

deny → throw 阻断工具并给出原因；ask → throw「需要用户确认: …」。注意：插件层的
ask **无法触发 opencode 原生确认框**，实际语义是「阻断并给出提示」——不存在批准
后放行的通道，重试同一命令会再次被拦。模型应转述原因请用户决策；确需执行时由
用户手动运行。静默返回 = 放行（放行兜底由 `opencode.json` 静态 permission 负责）。
插件自身任何异常一律 fail-closed 转 ask，绝不静默放行。

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

直接 deny：内联 Python（`python -c` / `python -` / heredoc 变体）、
generation_progress.py 的变量/管道/循环包装、跨 run 遍历、重定向或写删命令
目标在项目外或受保护路径、写删命令带未解析变量（`$VAR`）。
转 ask：删除/移动、依赖与环境变更、curl/wget、Git 写操作、系统/进程变更、
项目外 Python 入口、未绑定 run 时的 shell 写入。
自动信任：项目内 `.py` 入口（容忍解释器堆叠与前缀开关；`-m` 模块模式与 stdin
模式不自动信任）。

## 四、fail-closed

`guard()` 抛出的任何异常在插件层统一转为「守卫插件自检失败（fail-closed），
需要用户确认: …」。守卫失效的最坏情况是多问用户，而不是漏拦。

## 五、测试与验证

`guard.test.js` 是**开发态本地回归文件，不入库**（发布仓库不含此文件）；开发环境
在仓库根目录运行：

```bash
bun test ./.opencode/plugins/guard.test.js   # 28 个回归用例（仅 bun 运行时）
python3 scripts/validate_project.py           # 项目级静态校验
```

用例覆盖：受保护路径四类拒绝、apply_patch 补丁数组逐文件送检、run 隔离绑定与
跨 run 拒绝、子代理沿 parentID 链共享根会话绑定（含查询失败退化）、内联 python
（含 `-X utf8`/`py`/`pythonw` 变体）与包装监控拒绝、只读命令引号内关键词不误伤、
scope run_id 格式校验（`*`/空串视为未绑定）、高风险 ask 语义、项目内 .py 放行、
fail-closed 异常转报错。
