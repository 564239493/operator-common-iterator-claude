---
module: swin_transformer_ln_qkv_quant
description: aclnnSwinTransformerLnQkvQuant LN+QKV 量化算子专项规则：oriHeight/oriWeight 隐式 >0、Q 输出 4D shape 推导、K/V shape 与 Q 完全相等。
triggers:
  - kind: operator_name_eq
    value: "aclnnSwinTransformerLnQkvQuant"
depends_on: []
---

# 模块 swin_transformer_ln_qkv_quant（按需加载）

> 本模块原为 `knowledge/operator_patterns/swin_transformer_ln_qkv_quant.md` 的实战经验（含 `swin_transformer_ln_qkv_quant_infershape.cpp` 推导规则），按算子名由 `scripts/select_prompt.py` 装配到活跃提示词末尾。该规则按 `operator_name=="aclnnSwinTransformerLnQkvQuant"` 触发，**不**扩散到其他算子。

## 适用判定

满足下列**任一**条件时，执行本模块规则：

1. 算子文档的标题或首段明确为 `aclnnSwinTransformerLnQkvQuant`；
2. 文档描述 LN + QKV 量化算子且参数表里同时出现 `oriHeight`、`oriWeight`、`hWinSize`、`wWinSize`、`queryOutputOut`、`keyOutputOut`、`valueOutputOut`。

## 必须产出

`constraints_in_parameters` 中**至少**包含：

- §A — `oriHeight` / `oriWeight` 标量维度的隐式 >0 约束（`expr_type=self_value_range`，`expr="0 < <param>.range_value"`）；
- §B — `queryOutputOut` 第 1/2/3 维等于 `headNum` / `hWinSize*wWinSize` / `seqLength` 的 shape_dependency，`src_text` 引用 `swin_transformer_ln_qkv_quant_infershape.cpp`；
- §C — Q 首维推导式 `queryOutputOut.shape[0] * hWinSize * wWinSize == x.shape[0] * x.shape[1]`（结合文档 `x.shape[2] == headNum * seqLength` 化简），`src_text` 引用源码 + 文档化简说明；
- §D — `keyOutputOut` / `valueOutputOut` 与 `queryOutputOut` 的 `shape_equality` 各一条，`src_text` 引用源码「K/V 输出 shape 复制 Q 输出 shape」；
- §E — 三输出 `dimensions.value = [4]`（rank-4 守卫）。

并**保留**文档原文中已有的：

- `oriHeight * oriWeight == x.shape[1]`；
- `oriHeight % hWinSize == 0` 与 `oriWeight % wWinSize == 0`；
- `x.shape[0]` / `x.shape[2]` 等通用 shape 描述。

源码推导规则不替换文档约束，是**补充**关系。

## §A 标量维度隐式正值

文档说明 `oriHeight` 与 `oriWeight` 用于 S 轴 transpose 的**维度长度**，而非轴索引；按 v4 公共"标量维度 > 0"规则推论二值均 `> 0`。两条 `constraints_in_parameters`：

```json
{
  "expr_type": "self_value_range",
  "expr": "0 < oriHeight.range_value",
  "relation_params": ["oriHeight"],
  "src_text": "oriHeight为layernorm中S轴transpose的维度；维度长度语义隐含 >0"
}
```

```json
{
  "expr_type": "self_value_range",
  "expr": "0 < oriWeight.range_value",
  "relation_params": ["oriWeight"],
  "src_text": "oriWeight为layernorm中S轴transpose的维度；维度长度语义隐含 >0"
}
```

## §B Q 输出后三维绑定

```json
{
  "expr_type": "shape_dependency",
  "expr": "queryOutputOut.shape[1] == headNum.range_value and queryOutputOut.shape[2] == hWinSize.range_value * wWinSize.range_value and queryOutputOut.shape[3] == seqLength.range_value",
  "relation_params": [
    "queryOutputOut",
    "headNum",
    "hWinSize",
    "wWinSize",
    "seqLength"
  ],
  "src_text": "swin_transformer_ln_qkv_quant_infershape.cpp：Q输出后三维依次由headNum、hWinSize*wWinSize、seqLength设置"
}
```

## §C Q 首维推导式

源码 `swin_transformer_ln_qkv_quant_infershape.cpp` 计算 `queryOutputOut.shape[0]` 为 `prod(x.shape) / (headNum * hWinSize * wWinSize * seqLength)`；结合文档 `x.shape[2] == headNum * seqLength` 化简为 `queryOutputOut.shape[0] * hWinSize * wWinSize == x.shape[0] * x.shape[1]`：

```json
{
  "expr_type": "shape_dependency",
  "expr": "queryOutputOut.shape[0] * hWinSize.range_value * wWinSize.range_value == x.shape[0] * x.shape[1]",
  "relation_params": [
    "queryOutputOut",
    "x",
    "hWinSize",
    "wWinSize"
  ],
  "src_text": "swin_transformer_ln_qkv_quant_infershape.cpp的Q首维计算式，结合文档x.shape[2] == headNum*seqLength化简"
}
```

## §D K / V 与 Q 等形

```json
{
  "expr_type": "shape_equality",
  "expr": "keyOutputOut.shape == queryOutputOut.shape",
  "relation_params": ["keyOutputOut", "queryOutputOut"],
  "src_text": "swin_transformer_ln_qkv_quant_infershape.cpp：K输出shape复制Q输出shape"
}
```

```json
{
  "expr_type": "shape_equality",
  "expr": "valueOutputOut.shape == queryOutputOut.shape",
  "relation_params": ["valueOutputOut", "queryOutputOut"],
  "src_text": "swin_transformer_ln_qkv_quant_infershape.cpp：V输出shape复制Q输出shape"
}
```

## §E 三输出 rank-4 守卫

对每个三输出张量 `queryOutputOut` / `keyOutputOut` / `valueOutputOut` 必须产出：

```text
dimensions.value = [4]
src_text: "swin_transformer_ln_qkv_quant_infershape.cpp：三个输出均为4D"
```

## 文档与源码约束的边界

- 文档原文约束（如 `oriHeight * oriWeight == x.shape[1]`、`x.shape[2] == headNum * seqLength`）**不**被本模块替换；本模块约束仅在文档上**补充**源码推导出的关系。
- 每个 `relation_params` 列表必须**精确**包含表达式实际引用的参数。化简后的 §C 表达式不引用 `oriHeight` / `oriWeight`，因此 `relation_params` 也不应包含二者。
- 化简前后的 §C 公式 **不要** 同时落库到约束列表中，除非确需重复（默认不重复）；若发现已落库的未化简形式，删除之。

## 校验要点

1. `queryOutputOut` / `keyOutputOut` / `valueOutputOut` 三者均 rank-4（§E）；
2. `queryOutputOut` 第 1/2/3 维分别等于 `headNum` / `hWinSize*wWinSize` / `seqLength`（§B）；
3. Q 首维推导式与文档 `x.shape[2] == headNum * seqLength` 一致（§C），与 §B 不冲突；
4. `keyOutputOut.shape == queryOutputOut.shape`、`valueOutputOut.shape == queryOutputOut.shape`（§D）；
5. `oriHeight` / `oriWeight` 隐式 >0 单独落库为 `self_value_range`（§A），不要并入 shape_dependency。

## 规则要点

1. **`src_text` 标注源码**：所有引用源码推导的 `constraints_in_parameters` 条目必须在 `src_text` 中写出 `swin_transformer_ln_qkv_quant_infershape.cpp`，与文档约束区分。
2. **化简公式唯一性**：化简后的 §C 与未化简形式**只保留一个**；默认保留化简形式以减少生成器求解负担。
3. **不传染到其它算子**：本模块是算子内专用，不抽取为通用 §X.Y 规则；其它 LN+QKV 类算子若有需要另建模块。
4. **不替代通用 broadcast / dtype 互推导 / 形状-秩守卫**：本模块只覆盖算子特异性；通用规则由 `knowledge/aclnn/features/broadcast.md` 与命中的 feature 模块承载。
5. **`<param>_range_value` 引用规范**：与 v4 §6.1 一致，用 `param.range_value` 形式取标量参数值；shape 索引用 `param.shape[k]`。
