# 归档：历史迁移工具（已退场）

本目录的两份脚本是一次性迁移脚手架，**已于本次退场，不再 gate、不再运行**：

- `build_aclnn_prompt_base.py` — 从 `prompts/operator_constraints_extract_v4.md` 机械拆分生成
  ACLNN `prompts/operator_constraints/base.md`
- `build_torch_npu_prompt_base.py` — 从 `prompts/torch_npu_constraints_extract_v3.md` 机械拆分
  生成 torch_npu `prompts/torch_npu_constraints/base.md`

## 退场理由

迁移已忠实完成并验证内容对等。两脚本在运行时**不被任何代码调用**
（`init_run.py` / `select_prompt.py` / `route_*_knowledge.py` / `promote_prompt.py` 均不
import 不调用），仅作为"从不可变历史源机械拆分 + `--check` 比对"的文档契约存在。
继续保留 `--check` 门禁会把"对 base.md 的合法直接编辑"判红，从冻结门禁变成阻碍
演进的假阳性。

## 退场后的模型

- `base.md` 为 **canonical 直接编辑**的提示词（git 历史即溯源）。
- `v4` / `v3` 为**历史来源（provenance only）**，一次性迁移已完成，不再作为生成源。
- 提升后的冻结门禁改为：`validate_*_knowledge` + `init_run`（路由+装配）+
  `validate_prompt_assembly.py --record`（sha256 冻结）。这些都不依赖 v4/v3 派生。

## 保留价值

两脚本内的 `EXTRACTED_HEADINGS` / `REFERENCE_REWRITES` / `SCHEMA_REPLACEMENT` /
`DIRECTORY_REPLACEMENT` 留作**审计记录**：记录"哪些章节被抽出到了哪个知识模块"、
"哪些 § 引用被改写为文件+标题定位"。需要追溯迁移映射时可查阅，但不再执行。

## 退休测试

`test_aclnn_prompt_knowledge.py` 原在 `tests/` 下，断言 `build_aclnn_prompt_base.split_prompt`
的机械拆分行为（移除 schema/默认知识节等）。随 builder 退场该测试失去对象（import 即
`ModuleNotFoundError`），一并退休归档于本目录，不再纳入测试套。

## 退休的提升器（vN 模型）

`promote_prompt.py` 原为 ACLNN **vN 提升器**：把 `runs/<run>/iter/prompt_v(N+1).md`
原子写成 `prompts/operator_constraints_extract_v(N+1).md` + 追加 `prompts/CHANGELOG.md` +
落 `promotion_record.json`。canonical 模型下不再生成全局 +1 版本文件——提升改为：
运行内沉淀 `prompt_update_proposal.json` → 用户终态后显式逐条批准 → 主协调器按提案
`change.content` 直接 Edit canonical（`base.md` / knowledge 模块）→
`record_prompt_update_decision.py` 记裁决、`record_prompt_update_application.py` 记应用与
重跑校验（`validate_*_knowledge` + `init_run` + `validate_prompt_assembly.py --record`）。
该脚本无任何 .py 调用（仅历史文档指针），退场归档于本目录，不再调用。
