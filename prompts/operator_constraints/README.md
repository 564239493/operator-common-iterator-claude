# ACLNN base prompt (canonical)

`base.md` 是 ACLNN 约束提取的**canonical 直接编辑**提示词：保留稳定任务、字段提取
流程、边界处理和质量门禁，不复制 Pydantic schema（schema 由 §3 输出对象合同引用
`OperatorRule` 唯一事实源）。

`../history/operator_constraints_extract_v4.md` 为历史来源（provenance），一次性机械拆分已完成、
不再作为生成源；原迁移工具 `build_aclnn_prompt_base.py` 已退场归档于 `archive/builders/`，
仅留审计、不再 `--check`。v1-v4 文件均为历史快照（归档于 `../history/`），不是默认活动提示词。

## 编辑后验证

直接编辑 `base.md` 后，从项目根目录跑：

```text
python scripts/validate_aclnn_knowledge.py
python scripts/init_run.py --doc operator_docs/<op>.md --max-iterations 1 --case-count 1
python scripts/validate_prompt_assembly.py --record runs/<id>/inputs/prompt_assembly.json
```

三条均绿即 base + 知识装配链路完好。可复用的约束知识放 `knowledge/aclnn/`（manifest 驱动
按信号装配），稳定提取流程留在 `base.md`。
