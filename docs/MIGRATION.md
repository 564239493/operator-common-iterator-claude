# 从旧项目迁移到 Claude Code CLI 模式

保留了原项目的 `generators/`、算子文档和初始提示词。被替代的部分：

- `orchestrator.py` → CLAUDE.md + iterate-operator Skill；
- `constraint_extractor.py` 的 LLM 调用 → constraint-extractor Agent；
- `result_analyzer.py` 的 LLM 调用 → failure-analyst / prompt-optimizer Agents；
- `backends.py` → Claude Code 自身模型与认证；
- workflow JavaScript → Claude Code 原生 Agent 调度。

原项目的 `.env`、服务器密码、历史 logs 和 iterator_output 不迁移。默认真实执行仍
复用同级 `operator-agent` 的 executer_subgraph；缺少 `servers.json`、必要字段或
operator-agent 时停止并提示。Mock 仅作为用户显式选择的编排演练模式。
