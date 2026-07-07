# 互推导关系（CANN 公共知识）

来源：
https://www.hiascend.com/document/detail/zh/canncommercial/900/API/aolapi/context/common/%E4%BA%92%E6%8E%A8%E5%AF%BC%E5%85%B3%E7%B3%BB.md

## 语义

当 API 输入的 `aclTensor` 数据类型不一致时，API 内部会按互推导规则推导出一个计算
数据类型，并将输入转换成该类型进行计算。算子文档写“数据类型需要与 X 满足数据类型
推导规则”或“输出数据类型需要与输入推导之后的数据类型保持一致”时，必须把该关系落为
跨参数 dtype 约束，不能只给每个参数单独枚举 dtype。

## dtype 简写映射

- `f32` = `FLOAT`
- `f16` = `FLOAT16`
- `f64` = `DOUBLE`
- `bf16` = `BFLOAT16`
- `s8` = `INT8`
- `u8` = `UINT8`
- `s16` = `INT16`
- `u16` = `UINT16`
- `s32` = `INT32`
- `u32` = `UINT32`
- `s64` = `INT64`
- `u64` = `UINT64`
- `bool` = `BOOL`
- `c32` = `COMPLEX32`
- `c64` = `COMPLEX64`
- `c128` = `COMPLEX128`

## 推导表

`×` 表示两种类型不能进行推导计算。

| dtype | f32 | f16 | f64 | bf16 | s8 | u8 | s16 | u16 | s32 | u32 | s64 | u64 | bool | c32 | c64 | c128 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| f32 | f32 | f32 | f64 | f32 | f32 | f32 | f32 | × | f32 | × | f32 | × | f32 | c64 | c64 | c128 |
| f16 | f32 | f16 | f64 | f32 | f16 | f16 | f16 | × | f16 | × | f16 | × | f16 | c32 | c64 | c128 |
| f64 | f64 | f64 | f64 | f64 | f64 | f64 | f64 | × | f64 | × | f64 | × | f64 | c128 | c128 | c128 |
| bf16 | f32 | f32 | f64 | bf16 | bf16 | bf16 | bf16 | × | bf16 | × | bf16 | × | bf16 | c32 | c64 | c128 |
| s8 | f32 | f16 | f64 | bf16 | s8 | s16 | s16 | × | s32 | × | s64 | × | s8 | c32 | c64 | c128 |
| u8 | f32 | f16 | f64 | bf16 | s16 | u8 | s16 | × | s32 | × | s64 | × | u8 | c32 | c64 | c128 |
| s16 | f32 | f16 | f64 | bf16 | s16 | s16 | s16 | × | s32 | × | s64 | × | s16 | c32 | c64 | c128 |
| u16 | × | × | × | × | × | × | × | u16 | × | × | × | × | × | × | × | × |
| s32 | f32 | f16 | f64 | bf16 | s32 | s32 | s32 | × | s32 | × | s64 | × | s32 | c32 | c64 | c128 |
| u32 | × | × | × | × | × | × | × | × | × | u32 | × | × | × | × | × | × |
| s64 | f32 | f16 | f64 | bf16 | s64 | s64 | s64 | × | s64 | × | s64 | × | s64 | c32 | c64 | c128 |
| u64 | × | × | × | × | × | × | × | × | × | × | × | u64 | × | × | × | × |
| bool | f32 | f16 | f64 | bf16 | s8 | u8 | s16 | × | s32 | × | s64 | × | bool | c32 | c64 | c128 |
| c32 | c64 | c32 | c128 | c32 | c32 | c32 | c32 | × | c32 | × | c32 | × | c32 | c32 | c64 | c128 |
| c64 | c64 | c64 | c128 | c64 | c64 | c64 | c64 | × | c64 | × | c64 | × | c64 | c64 | c64 | c128 |
| c128 | c128 | c128 | c128 | c128 | c128 | c128 | c128 | × | c128 | × | c128 | × | c128 | c128 | c128 | c128 |

## 约束提取要求

- 对输入间的互推导关系，使用 `expr_type="type_dependency"`，用枚举组合表达合法 dtype
  组合与推导结果。
- 对输出“与推导后的数据类型保持一致”，必须把输出 dtype 绑定到推导结果。
- 如果推导结果不在输出参数 `dtype.value` 允许范围内，该输入 dtype 组合必须被排除。
- 示例：若 `self/mat2/out` 都只允许 `FLOAT16` 和 `BFLOAT16`，则 `FLOAT16 + BFLOAT16`
  推导结果为 `FLOAT`，但 `out` 不支持 `FLOAT`，因此合法组合只有：
  `(self.dtype == "FLOAT16" and mat2.dtype == "FLOAT16" and out.dtype == "FLOAT16")`
  或 `(self.dtype == "BFLOAT16" and mat2.dtype == "BFLOAT16" and out.dtype == "BFLOAT16")`。
