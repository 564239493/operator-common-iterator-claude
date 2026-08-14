---
module: selection_reduction_sampling
description: torch_npu TopK、排序、采样、归约及 loss 接口的轴、状态和条件输出审校规则
triggers:
  - kind: operator_name_regex
    value: "(?i)(top_k|top_p|sort|random_choice|cross_entropy|_loss(?:_|$)|npu_(?:max|min)$)"
depends_on: []
---
# 选择 / 归约 / 采样 / Loss 家族审校知识

- dim/axis、keepdim、values/indices 输出联合推导 rank/shape；不能只给输出 rank union。
- k 可能是 scalar 或逐 batch Tensor，其值域常依赖最后一维、group count 或 k_group；
  动态上界写跨参数关系，不写静态 allowed range。
- Top-p 的 `(0,1)` 只有文档明确为合法域时才是硬范围；若文档定义 `p<=0`、`p>=1` 的
  fallback 行为，这些值是有定义场景而非非法输入。
- `q` 的 dtype/shape 可能随 `post_sample` 切换，且 `q=None` 可能覆盖 flag；用优先级场景
  表达，不把所有候选做笛卡尔积。
- Generator/seed/offset 属于 RNG 状态和可复现语义；当前 schema 无法表达状态推进时标记
  `SCHEMA_GAP`，不要伪装成确定性布尔。
- loss 的 target shape/value 与类别轴绑定；reduction 决定 loss 输出是逐项还是标量/占位。
- `return_zloss`、`is_need_logits` 等关闭时常保留空 Tensor 槽，固定 tuple 不得删减。
- “当前未使能/预留”参数保留签名槽，并仅在文档明确时限制默认值或 presence。
