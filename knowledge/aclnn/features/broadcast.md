---
module: broadcast
description: 公共互推导关系（dtype promotion）/ broadcast 关系展开
triggers:
  - kind: name_contains
    value: "Matmul"
  - kind: name_contains
    value: "MatMul"
  - kind: doc_contains
    value: "broadcast|广播|互推导|type_promotion"
reject_on:
  - kind: doc_contains
    value: "不支持广播|不满足.*broadcast|不兼容.*broadcast|无需广播|不进行广播"
depends_on: [type_derivation, broadcast_relation]
---

# 模块 broadcast（按需加载）

> 本模块原为 `prompts/history/operator_constraints_extract_v4.md` §4.6.10，按算子特征由 `scripts/select_prompt.py` 装配到活跃提示词末尾。原 § 编号保留，便于交叉引用按标题文本定位。

#### 4.6.10 公共知识展开（互推导关系 / broadcast 关系）

当算子文档通过链接或文字引用公共说明（如 `../common/互推导关系.md`、
`../common/broadcast关系.md`、"数据类型推导规则"、"满足 broadcast 关系"）时，
必须按下表的公共规则把引用语义展开为可执行约束，不能只在 `src_text` 中保留链接。
官方原文（dtype 简写映射 + 推导表、广播规则与特殊 dtype 限制）现拆入
`foundation/type_derivation` 与 `foundation/broadcast_relation`，由 depends_on 拉入；
本模块只保留派生提取要求，不重复概念表。

##### A. 互推导关系（dtype promotion）

> 推导表（`×` 表示两种类型不能进行推导计算）与 dtype 简写映射见 `foundation/type_derivation`（由 depends_on 拉入）。

触发信号：

- "数据类型需要与 X 满足数据类型推导规则"；
- "数据类型需要与 X/Y 推导之后的数据类型保持一致"；
- "如果输入的数据类型存在互推导关系..."。

提取要求：

1. **输入间推导关系**：输入参数 `A`、`B` 的 dtype 候选若需要互推导，必须产出
   `expr_type="type_dependency"`，按推导表枚举合法组合；表中为 `×` 的组合必须排除。
2. **输出 dtype 绑定推导结果**：若文档写输出 `out` 与输入推导后的 dtype 一致，必须把
   `out.dtype` 绑定到推导结果。若某输入组合的推导结果不在 `out.dtype.value` 中，该组合
   必须被排除。
3. **禁止仅提取单参数 dtype 枚举**：只写 `self.dtype ∈ [...]`、`mat2.dtype ∈ [...]`、
   `out.dtype ∈ [...]` 不足以表达互推导关系，必须进入 `constraints_in_parameters`。
4. **典型修复**：若 `self/mat2/out` 都只允许 `FLOAT16` 与 `BFLOAT16`，根据公共推导表：
   `FLOAT16 + BFLOAT16 -> FLOAT`，但 `out` 不支持 `FLOAT`，因此合法约束应排除混合输入，
   写成：

```text
expr_type: type_dependency
expr: ((self.dtype == "FLOAT16" and mat2.dtype == "FLOAT16" and out.dtype == "FLOAT16")
       or (self.dtype == "BFLOAT16" and mat2.dtype == "BFLOAT16" and out.dtype == "BFLOAT16"))
relation_params: ["self", "mat2", "out"]
src_text: "out 数据类型需要与 self 与 mat2 推导之后的数据类型保持一致；互推导关系：f16+bf16->f32，out 不支持 FLOAT"
```

##### B. broadcast 关系

> 广播规则（左侧补 1 扩维、维度为 1 拉伸、无 1 维度则失败）与特殊 dtype 限制（连续广播/非广播轴合并后维度 < 6）见 `foundation/broadcast_relation`（由 depends_on 拉入）。

触发信号：

- "A 与 B 满足 broadcast 关系"；
- "b 要与 A 的 b 和 B 的 b 经过 broadcast 推导后一致"；
- 链接到 `../common/broadcast关系.md`。

提取要求：

1. **完整 shape broadcast**：使用 `expr_type="shape_broadcast"`，按右对齐规则表达每个轴
   相等或其中一方为 1。
2. **单轴 broadcast**：若文档只写某个轴（如 batch 轴 `b`）满足 broadcast，直接表达该轴：
   `A.shape[i] == B.shape[j] or A.shape[i] == 1 or B.shape[j] == 1`。
3. **输出轴等于 broadcast 结果**：若文档写输出轴由多个输入轴 broadcast 推导得到，必须
   同时提取输出轴约束，例如：

```text
expr_type: shape_broadcast
expr: (self.shape[0] == mat2.shape[0] or self.shape[0] == 1 or mat2.shape[0] == 1)
relation_params: ["self", "mat2"]
src_text: "self 的第一个维度 b 需要与 mat2 第一个维度 b 满足 broadcast 关系"

expr_type: shape_value_dependency
expr: (out.shape[0] == self.shape[0] if mat2.shape[0] == 1
       else out.shape[0] == mat2.shape[0] if self.shape[0] == 1
       else out.shape[0] == self.shape[0] == mat2.shape[0])
relation_params: ["self", "mat2", "out"]
src_text: "out 的 b 要与 self 的 b 和 mat2 的 b 经过 broadcast 推导后一致"
```

4. **特殊 dtype 限制**：若参与 broadcast 的输入 dtype 或互推导后的 dtype 落在
   `foundation/broadcast_relation`「限制」节的特殊集合内，除满足广播规则外还需满足
   「连续的需要广播的轴和连续的不需要广播的轴合并之后的维度小于 6」；能形式化时落为
   `shape_broadcast` / `shape_value_dependency` 的 `expr`；**无法可靠形式化时把语义
   记入相关参数 `description`/`src_text`，不得产出空 `expr` 的
   `constraints_in_parameters` 条目**（违 §4.7.2），不得忽略。
