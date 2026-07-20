# aclnnSwinTransformerLnQkvQuant operator pattern

This file records extraction rules specific to
`aclnnSwinTransformerLnQkvQuant`. Do not apply its Q/K/V formulas to other
operators.

Use this pattern only when
`operator_name == "aclnnSwinTransformerLnQkvQuant"` or the document clearly
describes the same LN + QKV quant operator with `oriHeight`, `oriWeight`,
`hWinSize`, `wWinSize`, `queryOutputOut`, `keyOutputOut`, and
`valueOutputOut`.

## Evidence and derivation boundary

- The operator document states that `oriHeight` and `oriWeight` are dimensions
  used when transposing the S axis. They are dimension extents, not axis
  indices, so the global scalar-dimension rule requires both values to be
  strictly positive.
- The operator document also states
  `oriHeight * oriWeight == x.shape[1]`, with `oriHeight` divisible by
  `hWinSize` and `oriWeight` divisible by `wWinSize`. Keep those document
  constraints in addition to this pattern.
- `swin_transformer_ln_qkv_quant_infershape.cpp` sets all three outputs to rank
  4. For Q, dimensions 1, 2, and 3 are respectively `headNum`,
  `hWinSize * wWinSize`, and `seqLength`; K and V copy Q's complete shape.
- The source computes Q dimension 0 from the product of all `x` dimensions
  divided by `headNum * hWinSize * wWinSize * seqLength`. Combined with the
  document relation `x.shape[2] == headNum * seqLength`, this simplifies to
  `queryOutputOut.shape[0] * hWinSize * wWinSize == x.shape[0] * x.shape[1]`.

Source-derived rules must say
`swin_transformer_ln_qkv_quant_infershape.cpp` in `src_text`; do not present
them as verbatim operator-document constraints.

## Required constraints

Add the following constraints for every supported platform:

Set `dimensions.value` to `[4]` for `queryOutputOut`, `keyOutputOut`, and
`valueOutputOut`, using the infer-shape source as `src_text`.

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

## Extraction checks

- `queryOutputOut`, `keyOutputOut`, and `valueOutputOut` must all have rank 4.
- Keep the document's `oriHeight * oriWeight`, divisibility, `x.shape[0]`, and
  `x.shape[2]` constraints; the source-derived rules supplement rather than
  replace them.
- Every `relation_params` list must contain exactly the parameters referenced
  by its expression. In particular, the simplified Q-dimension-0 formula does
  not reference `oriHeight` or `oriWeight`.
- Do not emit both the simplified Q-dimension-0 formula and its unsimplified
  source form unless duplicate constraints are intentionally required.
