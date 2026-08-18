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

## 量化 weight 的 INT32 打包（物理 dtype/shape 与逻辑 n 分离）

量化 weight（MatMul 右矩阵，通常名 w/weight）在文档中可能同时以「逻辑量化维 n」与
「物理打包维 n/8」出现，二者相差打包位宽（8 个低比特权重装入 1 个 INT32 容器）。

**触发信号**（下述同时出现才认定 INT32 打包）：
1. 「数据类型」列 INT4/INT8 等低比特量化类型；
2. 「维度(shape)」列或「调用示例」明示物理输入为 INT32 打包：(e,k,n/8) + ACL_INT32 +
   std::vector<int32_t> host data + 「转为 int_4」注释。

**规则**：
1. 物理接口 dtype 取 INT32（量化类型是内部转换结果，非接口 dtype）；
2. shape 取打包维 (e,k,n/8)，`w.shape[2]==n/8`（禁写 n）；
3. 引入独立逻辑 n = 打包维×8；scale/bias/sharedInput/y 与「w 的 n 一致」绑定逻辑 n
   （`== w.shape[2]*8`），禁止 `== w.shape[2]`；
4. `dtype_support_description` 中该 weight 的 combo dtype 同源写 INT32；
5. e/k 依赖无打包变换，保持不变。

**反例**：aclnnGroupedMatmulFinalizeRouting 把 w 提取为 dtype=INT4、shape=(e,k,n) 并写
`w.shape[2]==7168`、`y.shape[1]==w.shape[2]`，生成器产出 w int4+[e,2048,7168]，CANN 按
INT32 打包反推 weight NDim=7168*8=57344，与 y 第二维 7168 不符，100/100 失败。

## 非量化场景必须显式屏蔽量化参数（反向规则）

当 `scene_directive` 已选定非量化场景，并且算子文档明确说明“非量化场景不能输入
量化参数和伪量化参数”或含义等价的互斥规则时，被排除场景的全部专属 Optional 参数
必须逐参数生成可执行的缺席约束。例如：

```json
{
  "expr_type": "presence_dependency",
  "expr": "scaleOptional is None",
  "relation_params": ["scaleOptional"],
  "src_text": "非量化场景不能输入量化参数和伪量化参数",
  "origin": "doc"
}
```

对 `offsetOptional`、`deqScale*Optional`、`antiquantScale*Optional`、
`antiquantOffset*Optional` 等文档明确归属于量化或伪量化场景的参数应用同一规则；示例
参数名只是常见命名，实际参数集合必须从当前算子文档确定，不能按名称臆造。

必须区分以下三种情况：

1. 用户明确选择的参数：按所选单值固定，或按所选多值子集展开；
2. 用户未明确选择的参数：继续根据文档与已选场景提取、适配，不能因
   `param_modes` 缺键而自动删除；
3. 已选场景或文档明确禁止的 Optional 参数：生成 `<param> is None`。

不能使用“省略 `presence_dependency`”表达缺席。生成器对没有 presence 约束的 Optional
参数仍可在存在与缺席之间随机生成，因此只在 `description` / `src_text` 中写“不测试”也
没有约束效果。

若当前 run 同时保留量化、伪量化和非量化等多个场景，不得无条件将相关参数全部置为
`None`；应使用能够区分场景的门控表达式分别约束。只有场景已经收窄为单一非量化分支
时，才能化简为无条件 `<param> is None`。

