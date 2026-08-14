# ACLNN 算子约束知识库（第一版）

本目录只承载从远程 `main` 的 v4 提示词和 ACLNN 官方基本概念中拆出的、已经人工
划定范围的知识；不导入“扫描全部算子源文档”生成的宽泛知识。

装配层级固定为：

1. `foundation/`：每个 ACLNN 算子默认加载的官方基础概念；
2. `common/`：每个 ACLNN 算子默认加载的结构化映射与表达式规则；
3. `features/`：当前文档或算子名命中特征后加载；
4. `reference/`：只通过依赖加载的参考表；
5. `operators/`：仅算子名精确匹配时加载。

`manifest.json` 是唯一装配清单。模块只能帮助识别和表达当前文档事实，不能覆盖当前
算子文档。ACLNN 装配器不会扫描 `knowledge/torch_npu`，torch_npu 装配器也不会扫描
本目录。

校验知识清单：`python scripts/validate_aclnn_knowledge.py`。
