---
module: quantization
scope: feature
description: 官方量化粒度概念与量化参数 shape 检查
default_load: false
triggers:
  - kind: doc_contains
    value: "量化|反量化|pertensor|perchannel|pertoken|pergroup|perblock|伪量化|mx量化"
depends_on: [expression_language, quantization_intro]
---

# 量化参数 shape 检查（派生规则）

> 官方量化粒度概念（pertensor/perchannel/pertoken/pergroup/perblock 及全量化/伪量化/mx 组合的典型参数 shape）见 `foundation/quantization_intro`（由 depends_on 拉入）；本模块只给派生规则，不重复粒度概念表。

本模块不自动启用任何量化场景——量化场景的启用由 `scene_directive` 决定。

若当前文档给出 `m/n/k/group size/block size` 的实际绑定，将关系写为可执行 expr；只
出现概念介绍而未绑定当前参数时，不得把典型 shape 当作算子事实。

## 量化场景的 presence 必须落到张量级候选（通用规则）

当 `scene_directive` 选定量化场景（如 A8W8 全量化）时，参与量化公式的可选张量
（scale/offset/deqScale 等，文档称"可选输入，可为空指针"）必须在 `allowed_range_value.value`
中**同时**给出 present（非空）形状候选，不能只写 `[null]`：

- `null` 候选留给非量化场景（该参数缺席）；
- 文档给出的 per-tensor / per-channel 等真形状（如 `[1]`/`[E]`/`[N1]`/`[E,N1]`）必须作为
  present 候选写入 `allowed_range_value.value`，**不能只记到 `dimensions.src_text`**。

**原因**：生成器在候选阶段据 `allowed_range_value` 候选决定 `is_present`——候选仅 `null`
则 `is_present=False`、参数不进 Z3 变量表；随后 `presence_dependency` 中
`scaleOptional is not None` 被静态替换为 `False`（不在的参数 `is not None` 恒假），
整条 expr 归约成 `False and ...` → 断言假 → UNSAT。即**场景绑定的 presence 不能靠
`presence_dependency` 后置表达，必须在 EXTRACT 层就落到张量级候选**。

**正确**（null 与真形状同列）：
```json
"allowed_range_value": {
  "value": [null, [1], [E], [N1], [E,N1]],
  "src_text": "可选输入，可为空指针；per-tensor一维[E]/[1]；per-channel二维[E,N1]/[N1]",
  "type": "enum"
}
```
**错误**（只留 null，形状塞进 dimensions 描述位却不进候选 → 量化场景 unsat）：
```json
"allowed_range_value": { "value": [null], "src_text": "可选输入，可为空指针", "type": "enum" }
```

> 与 base.md §4.6「支持/仅支持输入 nullptr」规则的区别：那条针对**必填且必填值为 nullptr**
> 的参数（`is_optional=false`，`null` 是取值语义）；本条针对**可选、量化场景才 present** 的
> 参数——`null` 是缺席语义（非量化场景），present 候选是量化场景取值。二者 `null` 语义相反，
> 不可混用。

