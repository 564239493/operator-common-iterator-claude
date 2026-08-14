# 提示词与知识沉淀流程

运行失败先做归因，只有 `constraint_extraction` 才能提出提示词/知识更新；
`generator_bug` 和 `executor_bug` 不得用规则补丁掩盖。

## 提案目的地

每条经验只能选择一个最小目的地：

- `base_prompt`：所有 ACLNN 算子都适用的稳定流程或质量门禁；
- `knowledge_common`：所有 ACLNN 算子默认加载的结构化映射/表达规范；
- `knowledge_feature`：有明确文档特征才适用；
- `knowledge_operator`：只适用于精确算子名；
- `torch_npu`：仅 torch_npu family，不能写入 ACLNN 根；
- `no_update`：运行偶发、证据不足或已有规则可覆盖。

## 证据与审批

提案必须包含失败 case、当前文档证据、命中的模块、目标 canonical 文件、候选 diff、
试验结果和回滚方式。先在 run-local prompt 中验证；验证通过只代表“可以询问”，不代表
自动批准。

主协调器逐条向用户展示摘要、证据、适用范围和目标，询问“应用 / 暂缓 / 拒绝”。只有
用户明确选择应用后才能修改 `prompts/` 或 `knowledge/`；批处理、运行成功和用户沉默都
不能推断批准。应用后必须重建 base（如涉及）、验证 manifest/路由、再跑组装冻结校验。

