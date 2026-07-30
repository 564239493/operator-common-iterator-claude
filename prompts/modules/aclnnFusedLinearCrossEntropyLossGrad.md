---
module: aclnnFusedLinearCrossEntropyLossGrad
description: 消解该算子文档参数表↔公式冲突：weightGradOut.shape[1] 绑 H 不绑 BT；maskedTarget/targetMask 值域
triggers:
  - kind: operator_name_eq
    value: "aclnnFusedLinearCrossEntropyLossGrad"
depends_on: []
---

# 模块 aclnnFusedLinearCrossEntropyLossGrad（按需加载，算子专属纠错）

> 本模块为消解该算子文档“参数表 ↔ 计算公式”自相矛盾的算子专属纠错，非通用模式。
> 若官方文档修订使参数表与公式一致，应同步删除本模块。

## 1. weightGradOut.shape[1] 必须绑 input.shape[1]（=H），不得绑 grad.shape[0]（=BT）

- 文档参数表（weightGradOut 行）写“第2维长度与输入 grad 长度相同”（=BT），与计算公式
  `grad_weight = softmax^T · input ∈ R^{V×H}`（第2维 = H）**矛盾**。
- **以公式为准**：NPU 运行时 Expected `[V, H]`；失败例 BT=8、H=128 证明 BT≠H 才是常态，
  强制 `BT == H` 会退化测试。
- 落 expr：`weightGradOut.shape[1] == input.shape[1]`
  （等价 `== weight.shape[1]`，因已有 `weight.shape[1] == input.shape[1]`）。
- **禁止** 落 `weightGradOut.shape[1] == grad.shape[0]`（错绑 BT，会触发 EZ1001 161002：
  Expected [V,H] but got [V,BT]）。

## 2. maskedTarget ∈ [0, weight.shape[0]-1]（局部词表索引，不得为负）

- maskedTarget 为“对应词ID映射到当前设备词汇表分片的局部索引”，V = weight.shape[0]。
- 负值越界触发 aicore 507015（D-cache/UB 总线异常）。
- 落 expr：`0 <= maskedTarget.range_value and maskedTarget.range_value <= weight.shape[0] - 1`。
- 说明：文档另述“无效目标会被 targetMask 掩码处理”——若需覆盖该路径，应令无效位
  `targetMask=0` 且 maskedTarget 取合法占位值（如 0），**而非负值**。

## 3. targetMask ∈ [0, 255]（dtype=UINT8，8bit 掩码）

- targetMask dtype UINT8，“每 1bit 代表 1 个布尔值”，字节值为 8 位比特掩码，取值 0-255。
- 落 expr：`0 <= targetMask.range_value and targetMask.range_value <= 255`。
