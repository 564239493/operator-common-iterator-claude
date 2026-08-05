---
module: quantization
description: torch_npu 量化、反量化与伪量化场景的组合审校规则
triggers:
  - kind: operator_name_regex
    value: "(?i)(quant|antiquant|dequant)"
  - kind: doc_contains
    value: "(?i)(量化模式|反量化|quant_mode|dequant_scale|antiquant|quant_scale_repo_mode)"
depends_on: []
---
# 量化家族审校知识

- 先列出完整量化场景元组：输入 dtype、weight dtype、mode 值、scale/offset presence、scale/offset dtype/shape、输出 dtype。每个合法元组是 AND 分支，不得把各列独立枚举成笛卡尔积。
- 区分 quant scale、dequant scale、antiquant scale、smooth scale、offset 和 repository mode；相似名称不代表相同语义或 shape。
- mode 的签名默认值、参数表支持枚举、场景表实际使用值可能冲突。三者分别保留，并标记 `DOC_CONFLICT`，不能用默认值替代支持集。
- “无需赋值”“传 None”“空 Tensor”“无效”必须依文档分别表示。不要把保留参数从签名删除。
- per-tensor、per-channel、per-token、per-group 以及 combined/separate scale 通常对应不同 rank/shape；按当前 layout 和 mode 条件化。
- int8/int4/fp8/hifloat8/mxfp 等输入支持必须有当前文档证据。某个中间场景出现 int8 不代表所有 query/key/value 全局支持 int8。
- 文档要求量化乘积或累加结果在某 dtype 数值范围内时，保留为跨参数值域关系；若当前 DSL 无法精确表达，写清 `SCHEMA_GAP`，不要删掉。
