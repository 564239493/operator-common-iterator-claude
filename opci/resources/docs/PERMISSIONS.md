# 无交互权限配置

项目级权限定义在 `.claude/settings.json`。

## 行为

- `defaultMode: dontAsk`：不会弹权限确认；允许规则直接执行，其他操作直接拒绝。
- `Read`、`Glob`、`Grep`：允许读取项目内外文件。
- `Edit(/**)`、`Write(/**)`：仅允许修改项目根目录及子目录。
- `Bash`：直接执行，不逐条确认。
- `Agent`、`Skill`：Agent 调度与 Skill 调用直接执行。
- `.env`、`.env.*`、`servers.json`：仍禁止读取，避免凭据进入模型上下文。

## Bash 写入边界

Claude Code sandbox 可用时，OS 层默认只允许命令写当前项目，读取范围则覆盖计算机上的
其他可读文件。项目同时配置 `guard_project_writes.py`，在 Bash 执行前阻止明显的外部
删除、移动、复制、写文件和重定向操作。

在原生 Windows 上 Claude Code 的 OS sandbox 不可用；此时 Hook 是回退保护。Hook 会
拦截常见文件命令，但无法从任意第三方程序内部推断所有副作用。需要强隔离保证时，
请在 WSL2 中运行本项目并安装 bubblewrap/socat。

## 生效方式

修改设置后重新启动项目中的 Claude Code：

```powershell
cd D:\operator_project\operator-common-iterator-claude
claude
```

运行 `/permissions` 和 `/hooks` 可查看最终合并的权限规则与写入守卫。

