---
module: backward_partial
description: 反向/grad 算子的 Forward-Output Partial-Shape 跟随约束
triggers:
  - kind: name_contains
    value: "Backward"
  - kind: name_contains
    value: "Grad"
depends_on: []
---

# 模块 backward_partial（按需加载）

> 本模块原为 `operator_constraints_extract_v4.md` §4.6.6 + §6.3 模式7，按算子特征由 `scripts/select_prompt.py` 装配到活跃提示词末尾。原 § 编号保留，便于交叉引用按标题文本定位。

#### 4.6.6 Forward-Output Partial-Shape 跟随约束（两轮实测闭环）

> 本规则来自 `aclnnReflectionPad1dBackward` 两轮迭代：首轮只提取末维派生关系，
> 遗漏 `gradOutput` 与 `self` 的前缀维度和 rank 关系，执行结果为 44/80；第二轮
> 补齐以下约束后为 80/80。该规则按语义触发，不按算子名硬编码。

##### A. 适用判定

满足下列条件时必须执行本节：

1. 算子属于 backward / grad / 反向传播场景；
2. 文档明确说明 `gradOutput` / `dout` 的维度与 `self` / `input` 一致，或说明其
   shape 与正向算子的 output 一致；
3. 文档又给出最后若干维由 `padding`、`kernel_size`、`stride`、`output_size`
   等参数派生，因而不能简单写成两个完整 shape 相等。

##### B. 必须拆分落库

前缀跟随与派生轴关系是彼此独立的约束，不能只保留其中一类：

```text
# 1. 非派生轴（此例为除最后一维外）必须跟随
expr_type: shape_equality
expr: gradOutput.shape[:-1] == self.shape[:-1]
relation_params: ["gradOutput", "self"]

# 2. rank 必须显式一致
expr_type: shape_equality
expr: len(gradOutput.shape) == len(self.shape)
relation_params: ["gradOutput", "self"]

# 3. 派生轴按文档公式单独表达；以下仅为 reflection_pad1d 示例
expr_type: shape_value_dependency
expr: gradOutput.shape[-1] == self.shape[-1] + padding.range_value[0] + padding.range_value[1]
relation_params: ["gradOutput", "self", "padding"]
```

参数名和切片边界必须按文档确定。上例使用 `[:-1]`，是因为 ReflectionPad1d 文档
把 padding 明确绑定到 `self` 最后一维，且末维公式单独发生变化，所以切片表示
“除最后一维外的其余维度”。不得仅凭算子名中的 2d/3d 就外推为 `[:-2]` /
`[:-3]`；只有文档明确给出多个尾部派生轴时，才能采用对应切片并逐轴表达公式。

##### C. 防漏规则

1. `gradInput.shape == self.shape` 不能替代 `gradOutput` 与 `self` 的跟随关系；
2. 末维公式成立不能推出前缀维度或 rank 一致，三类约束必须分别检查；
3. `dimensions.value` 只记录静态 rank 范围，跨参数跟随必须进入
   `constraints_in_parameters`；
4. 每条 `src_text` 摘录对应的维度一致或派生公式原文；同一句覆盖多条约束时可复用；
5. 正向算子、MatMul broadcast、卷积反向等不满足上述语义的场景不得套用此模板。

#### 模式 7：Forward-Output Partial-Shape 跟随

**适用场景**：backward / grad 算子中，`gradOutput` 与 `self` / `input` 共享
非派生轴，而末尾空间轴由 padding、stride、kernel 等参数改变。

```text
# 前缀维度跟随
gradOutput.shape[:-1] == self.shape[:-1]

# rank 一致（必须显式落库）
len(gradOutput.shape) == len(self.shape)

# 派生轴按文档公式表达；reflection_pad1d 示例
gradOutput.shape[-1] == self.shape[-1] + padding.range_value[0] + padding.range_value[1]
```

三条约束语义独立。尤其禁止用 `gradInput.shape == self.shape` 替代第一条，或认为
末维公式成立便会自动保证 batch 维和 rank 一致。

