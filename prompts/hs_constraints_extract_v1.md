# torch_npu 海思算子约束提取提示词 v1

本提示词适用于 `torch_npu.*` 海思融合算子。输出仍必须严格符合项目
`agent/generators/common_model_definition.py::OperatorRule` schema；通用字段、平台、
dtype 命名和表达式语法遵循 `prompts/operator_constraints_extract_v4.md`。以下规则覆盖
其中仅面向 aclnn 的假设。

## 核心原则

1. 函数原型是参数顺序、参数种类、可选性和默认值的最高优先级来源。
   `*` 之前是必选位置参数；有 `=...` 的参数一律 optional。文档正文不得把 optional
   参数误写成 required。
2. `torch_npu.*` 是 PyTorch E2E API，`operator_name` 保留完整点号名称，
   `api="pytorch"`，不要伪造 `aclnn_name` 或 ACLNN workspace 接口。
3. 先提取场景/模式，再提取参数。layout、量化模式、稀疏模式、page attention、
   Prompt/Decode、RoPE/MLA 是场景开关，必须用 presence/type/shape/value dependency
   将同一场景的参数绑定，禁止独立随机组合。
4. 文档给出的调用示例是合法 baseline，但不能覆盖正文约束。示例中的符号维度
   B/S/T/N/D/Dr/H 等应建立共享维度变量和 shape equality/binding。
5. 默认值是一个确定值，不是随机范围。生成阶段必须把 scalar attr 实例化为单值；
   禁止在最终 case 的 scalar `range_values` 中保留 `[min,max]`。
6. 输出 JSON 使用项目内部类型名，而不是直接照抄 Python 标注：`Tensor`、
   `Optional[Tensor]`、`torch.Tensor` 一律写 `aclTensor`；`List[Tensor]` 写
   `aclTensorList`；`List[int]` 写 `aclIntArray`；`List[float]` 写
   `aclFloatArray`；`List[bool]` 写 `aclBoolArray`；`str` 写 `string`。
   optional 性只由 `is_optional` 表达，不得把 `Optional[...]` 留在 `type.value`。

## 必做提取

- 产品支持差异按平台分别保存，特别是 A2/A3 不支持的 fp8/hif8/mxfp8 模式。
- 每个 tensor 的 dtype、format、rank、是否连续、空 tensor 支持情况。
- layout 到 rank/轴语义的完整映射；复合 layout（例如输入/输出 layout 不同）必须保留，
  不得凭名称猜测为非法值。
- optional tensor 的启用条件和互斥条件；未启用时必须为 None。
- 量化 mode 与 scale/offset dtype、shape、presence 的联合约束。
- sequence length 与 tensor S/T 轴、batch、block table、block size 的关系。
- 所有整数整除、对齐、前缀和、有界索引、最大内存相关约束。
- 返回 tensor 只写入 outputs，不得混入 inputs。

## 六类算子场景要求

- FIA：区分 Q_S=1 Decode 与 Q_S>1 Prompt；layout、mask、sparse、量化、PA、RoPE、
  shared-prefix 分场景建模。
- MLA Prolog v3：cache_mode、BS 合轴、weight/kv/query quant mode 与 cache 原地输出联动；
  FRACTAL_NZ 权重格式不可丢失。
- LI/QLI：query/key layout、PA block_table、actual seq、sparse_count 与输出索引范围联动；
  QLI 的 query/key quant mode 与 dequant scale 联动。
- SFA/KV-SFA：sparse_indices 必须是合法结构化索引，不能用无界随机数；RoPE、PA、
  quant repository/tile mode 分场景建模。

## 输出前强制自检

- required/optional 是否与函数原型一致。
- 每个场景中 shape rank 是否与 layout 一致。
- dtype 组合是否受当前产品支持。
- scalar attr 是否具有可实例化的确定候选值。
- optional tensor 是否只在启用场景出现。
- 是否存在负 uint、越界 index、未满足整除/对齐或明显超内存 shape。
- outputs 是否被错误放入 inputs。
- `type.value` 是否已经转换为上述内部类型；禁止残留 `Tensor`、`List[int]`、`str`。

仅输出纯 JSON，不输出 Markdown、解释或注释。
