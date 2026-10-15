# 技能、子智能体与调度可观测性（opencode 原生）

## 启动时看清"有哪些"

运行 `/show-workforce` 获取项目技能、子智能体、命令与调度链的名称、职责、
开工首载技能（`scripts/show_registry.py` 渲染）。在 opencode 中输入 `/` 可在
命令选择器中看到项目命令与技能。

## 执行时看清"谁在工作"

主协调器在委派前后输出可读调度消息（项目约定，写入 AGENTS.md）：

```text
调度 -> constraint-extractor | 输入: doc + prompt_v1 | 预期产物: constraints.json
完成 <- constraint-extractor | 结论: validation passed | 产物: runs/.../constraints.json
```

任务（task）工具的每次调用即一次子智能体委派，opencode 会在会话消息中
保留完整的工具调用记录（含参数与结果状态），可用于事后核对委派链。

## 业务状态审计

- 业务流程状态存于 `runs/<run-id>/run_state.json`（唯一真相源），每次状态迁移
  由 `scripts/flow_control.py advance` 落盘裁决。
- 会话与 run 的权限绑定元数据存于 `.opencode/runtime/task_scopes/`（不入库）。

> 历史说明：旧版（Claude Code 线）曾通过 `.claude/runtime/schedule.jsonl` 记录
> 子智能体启停调度事件；opencode 原生化后该机制已随 trace 插件移除，事件级
> 采集与关联作为独立需求另行设计（opencode 原生事件流可提供更完整依据）。

## 非交互 CI

opencode 以 server 模式运行时可通过 SDK/HTTP API 获取会话消息与工具记录；
机器解析不要依赖 TUI 文本输出。
