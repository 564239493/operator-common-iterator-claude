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
- **量化场景元组的 presence 必须落到张量级候选**：当某量化场景元组（按上一条列出的完整元组）中 scale/offset/deqScale 等**参与计算且 present**，这些张量在 `allowed_range_value.value` 必须含 present（非空）形状候选，不能只写 `[null]`。`null` 候选只留给该参数在该场景元组中**缺席**的分支。生成器据 `allowed_range_value` 候选决定 `is_present`——候选仅 `null` 则 `is_present=False`、参数不进 Z3 变量表，随后 `presence_dependency` 中 `X is not None` 被静态替换为 `False`，整条归约成 `False and ...` → UNSAT。即场景绑定的 presence 不能靠 `presence_dependency` 后置，必须在 EXTRACT 层就落到张量级候选。
  - 正确：`"allowed_range_value": {"value": [null, [m], [n]], "type": "enum"}`（`null` 给缺席分支，真形状给 present 分支，按当前 layout/mode 条件化）
  - 错误：`"allowed_range_value": {"value": [null], "type": "enum"}`（只留 `null`，present 分支的真形状塞进 `dimensions` 描述位却不进候选 → 该场景元组 unsat）
  - 与"必填且必填值为空指针"区分：后者 `null` 是**取值语义**（参数 present 但值为 nullptr）；本条 `null` 是**缺席语义**（参数不在），present 候选才是取值。二者 `null` 语义相反，不可混用。
