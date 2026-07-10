# aclnnFFNV3 operator pattern

This file records FFNV3-specific extraction rules that should not be mixed into
the global prompt as universal CANN behavior.

Use this pattern only when `operator_name == "aclnnFFNV3"` or the document is
clearly the FFNV3 MoE/FFN operator with `weight1`, `weight2`,
`expertTokensOptional`, `bias*Optional`, `deqScale*Optional`, and
`antiquant*Optional` parameters.

## Source-backed rules

The following rules are directly supported by the FFNV3 document and should be
extracted into `constraints_in_parameters`.

- `x.shape == [M.range_value, K1.range_value]`.
- `weight1` shape is `[K1, N1]` without experts and `[E, K1, N1]` with experts.
- `weight2` shape is `[K2, N2]` without experts and `[G, K2, N2]` with experts.
- `bias1Optional`, when present, is `[N1]` without experts and `[E, N1]` with experts.
- `bias2Optional`, when present, is `[N2]` without experts and `[G, N2]` with experts.
- `deqScale1Optional`, when present, is `[N1]` without experts and `[E, N1]` with experts.
- `deqScale2Optional`, when present, is `[N2]` without experts and `[G, N2]` with experts.
- `expertTokensOptional`, when present, is rank-1, length `E`, and length <= 256.
- A2 `activation` enum is `["fastgelu", "gelu", "relu", "silu", "geglu", "swiglu", "reglu"]`.
- Accelerator-card `activation` enum is `["fastgelu", "gelu", "relu", "silu"]`.
- For `gelu` / `fastgelu` / `relu` / `silu`, `N1 == K2`.
- For `geglu` / `swiglu` / `reglu`, `N1 == 2 * K2`, expert mode is not supported, and all required tensor dtypes are `FLOAT16`.
- Quant params and pseudo-quant params are mutually exclusive.
- Quant mode requires `bias*Optional` dtype `INT32`, `scaleOptional` dtype `FLOAT32`, `offsetOptional` dtype `FLOAT32`, and matching `deqScale1Optional` / `deqScale2Optional` dtype.
- In quant mode, `deqScale*Optional` dtype depends on `y.dtype`: `FLOAT16` output allows `UINT64` / `INT64` / `FLOAT32` in per-tensor mode and `UINT64` / `INT64` in per-channel mode; `BFLOAT16` output allows `BFLOAT16`.
- Pseudo-quant mode has two documented dtype groups:
  - `y/x/bias/antiquantScale/antiquantOffset` are `FLOAT16`, and weights are `INT8` or `INT4`.
  - `y/x/antiquantScale/antiquantOffset` are `BFLOAT16`, `bias` is `FLOAT32`, and weights are `INT8` or `INT4`.
- If `weight1` or `weight2` dtype is `INT4`, its last shape dimension must be even.
- Pseudo-quant per-group requires `G > 0`, `K1 % G == 0` for group-1 params, and `K2 % G == 0` for group-2 params.
- `innerPrecise` must be `0` or `1`; in BFLOAT16 non-quant mode it must be `0`; in accelerator-card mode it must be `1`.
- `tokensIndexFlag == true` with expert mode requires `expertTokensOptional` values to be monotonic non-decreasing. If value-level monotonicity cannot be expressed by the generator, keep the source text and still require the tensor presence/shape constraints.
- Accelerator-card mode supports no experts, so `expertTokensOptional is None`, all quant and pseudo-quant optional params are `None`, and `N1 == K2`.

## NPU feedback rules

The following rules came from NPU execution feedback. Keep their `src_text`
explicitly marked as measured feedback if they are applied, so they are not
confused with source-document facts.

- `weight1.dtype == weight2.dtype`.
- Floating-weight mode requires `y.dtype == x.dtype`.
- Quant mode requires the full quant parameter group:
  `scaleOptional`, `offsetOptional`, `deqScale1Optional`, and `deqScale2Optional`.
- In the measured per-tensor quant path, `scaleOptional.shape == [1]` and
  `offsetOptional.shape == [1]`.

## Expression templates

Use these templates as a starting point. Keep platform-specific availability in
the surrounding platform key.

```text
weight1.dtype == weight2.dtype
y.shape == [M.range_value, N2.range_value]
(weight1.shape == [E.range_value, K1.range_value, N1.range_value]) if (len(weight1.shape) == 3) else (weight1.shape == [K1.range_value, N1.range_value])
(weight2.shape == [G.range_value, K2.range_value, N2.range_value]) if (len(weight2.shape) == 3) else (weight2.shape == [K2.range_value, N2.range_value])
(bias1Optional is None) or ((bias1Optional.shape == [N1.range_value]) if (len(weight1.shape) == 2) else (bias1Optional.shape == [E.range_value, N1.range_value]))
(bias2Optional is None) or ((bias2Optional.shape == [N2.range_value]) if (len(weight2.shape) == 2) else (bias2Optional.shape == [G.range_value, N2.range_value]))
(deqScale1Optional is None) or ((deqScale1Optional.shape == [N1.range_value]) if (len(weight1.shape) == 2) else (deqScale1Optional.shape == [E.range_value, N1.range_value]))
(deqScale2Optional is None) or ((deqScale2Optional.shape == [N2.range_value]) if (len(weight2.shape) == 2) else (deqScale2Optional.shape == [G.range_value, N2.range_value]))
(scaleOptional is None) or (scaleOptional.shape == [1])
(offsetOptional is None) or (offsetOptional.shape == [1])
```
