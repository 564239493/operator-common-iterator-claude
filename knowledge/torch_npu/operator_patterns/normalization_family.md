---
module: normalization_family
description: torch_npu RMSNorm、LayerNorm 与 GroupNorm 接口的归一化轴和输出审校规则
triggers:
  - kind: operator_name_regex
    value: "(?i)(rms_?norm|rmsnorm|group_norm|layer_norm)"
depends_on: []
---
# Normalization 家族审校知识

- gamma/weight 可能等于输入的一个尾部 suffix，而不一定固定一维；按 normalized shape 或
  文档轴定义表达。
- mean/rstd 输出通常对应未归一化前缀，并在归一化轴位置补 1；不能直接写与输入同 shape。
- GroupNorm 的 channel 轴、`num_groups`、weight/bias 长度和 channel 整除关系需联立，并
  按 layout 指定正确轴。
- Add+Norm 先约束 x1/x2 shape/dtype，再分别提取 norm 输出、add 中间输出和 rstd 的规则。
- 量化 Norm 的 scale/zero-point/beta 常与 gamma 关联；第二套量化参数按文档保持成组
  presence，不生成自由组合。
- epsilon 默认值和“建议较小正数”不能升级为硬 `>0`；只有文档明确合法范围才约束。
- 空 Tensor、尾轴字节对齐、inf/nan、反向/requires_grad 限制常按产品或模式条件化。
- 参数 dtype 总表与产品专项条款冲突时按证据分别保留并标记 `DOC_CONFLICT`。
