# Skill、Agent 与调度可观测性

## 启动时看清"有哪些"

SessionStart Hook 会显示 `[WORKFORCE]`，列出项目 Skills、Agents 和常用命令。
也可随时运行 `/show-workforce`，或让 Claude 调用 `show-workforce` Skill，获取名称、
职责、预加载 Skill 与调度链。

Claude Code 原生入口：

- `/agents`：Library 查看全部 Agent；Running 查看运行中和最近完成实例。
- `/hooks`：查看 SessionStart、SubagentStart、SubagentStop 配置及来源。
- 输入 `/`：在命令选择器中看到项目 Skills。

## 执行时看清"谁在工作"

主协调器在委派前后输出可读调度消息。项目 Hook 同时监听：

- `SubagentStart`：显示 Agent 名称与实例 id；
- `SubagentStop`：显示结束事件；
- `SessionStart`：显示 workforce 总览。

示例：

```text
调度 -> constraint-extractor | 输入: doc + prompt_v1 | 预期产物: constraints.json
[SCHEDULER] START agent=constraint-extractor id=agent-...
[SCHEDULER] STOP  agent=constraint-extractor id=agent-...
完成 <- constraint-extractor | 结论: validation passed | 产物: runs/.../constraints.json
```

## 文件化审计

`.claude/runtime/schedule.jsonl` 每行一个事件：

```json
{
  "timestamp": "2026-06-30T02:00:00+00:00",
  "event": "SubagentStart",
  "session_id": "abc",
  "agent_id": "agent-123",
  "agent_type": "constraint-extractor",
  "message": "[SCHEDULER] START ..."
}
```

该目录不提交 Git。业务状态另存于 `runs/<run-id>/run_state.json`，两者分别回答：
"Claude 调度了谁"和"业务流程走到哪一步"。

## 非交互 CI

使用 `--output-format stream-json --verbose` 可以保留 Claude Code 原始事件流；项目级
schedule.jsonl 提供更精简的 Agent 生命周期索引。不要使用 text 输出做机器解析。
