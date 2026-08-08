# 历史提示词源归档（provenance only）

本目录存放 ACLNN / torch_npu 的**历史提示词来源**，仅供审计与追溯，不再驱动生成：

- `operator_constraints_extract_v1.md` ~ `v4.md` —— ACLNN 历史来源
- `torch_npu_constraints_extract_v1.md` ~ `v3.md` —— torch_npu 历史来源

## canonical 基线（运行时实际使用）

| family | canonical 基线（直接编辑） |
|---|---|
| ACLNN | `prompts/operator_constraints/base.md` |
| torch_npu | `prompts/torch_npu_constraints/base.md` |

canonical base.md 由 manifest 驱动的知识路由在 run 初始化（PLAN）阶段装配为
`prompt_v1.md` + `prompt_preanalysis.json` + `prompt_assembly.json`。

## 运行时不会读本目录

`scripts/runtime_config.py` 的提示词发现是 **base.md 优先、vN 仅 fallback**：
`find_active_aclnn_prompt` / `find_latest_hs_prompt` 先看对应 family 的 `base.md`，
存在即返回，**永不扫描本目录**。两 family 的 base.md 均在位 → 本目录文件运行时不被读取。

> 退化说明：若某 family 的 `base.md` 被删除，fallback 扫描 `prompts/` 顶层（不递归到本
> 子目录）将返回 `None`——属 base.md 缺失的退化场景，不专门兜底；恢复 base.md 即可。

## 与 v1-v4/v1-v3 的关系

v1-v4（torch v1-v3）是知识库重构前的整段式提示词来源。一次性机械拆分已完成，相关规则已迁入
`knowledge/aclnn/**` 与 `knowledge/torch_npu/**` 模块、流程与未迁移规则留在 canonical
`base.md`。迁移工具 `build_*_prompt_base.py` 已退场归档于 `archive/builders/`。本目录仅保留
原文件作 provenance，不再作为生成源。
