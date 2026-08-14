---
module: platform_dtype
scope: common
description: 平台、dtype 和 format 命名规范
default_load: true
triggers: []
depends_on: [official_basics]
---

# 平台、dtype 与 format 命名规范

- 平台 key 逐字采用当前文档产品支持表中的标准全名；每个业务参数必须覆盖
  `product_support` 的全部平台，不能用单一平台代笔。
- Tensor dtype 使用当前文档 token，并仅做官方基础知识登记的规范化；未知 token 原样
  保留并标记字典缺口，禁止猜测相似名称。
- format 使用文档短名；整数型 format 参数的枚举值只在命中 `acl_format_enum` 参考模块时换算。
- 横线、空白、"不涉及"与"不支持"不是字符串候选。

## §平台

提取 `product_support` / `deterministic_computing` / `constraints_in_parameters` / `dtype_support_description` / `format_support_description` 的 key 时，**必须**使用以下字符串之一：

| 平台 | 字符串 |
| ---- | ------ |
| Atlas A2 训练 + 推理 | `Atlas A2 训练系列产品/Atlas A2 推理系列产品` |
| Atlas A3 训练 + 推理 | `Atlas A3 训练系列产品/Atlas A3 推理系列产品` |
| Atlas 训练系列（旧） | `Atlas 训练系列产品` |
| Atlas 推理系列（旧） | `Atlas 推理系列产品` |
| Atlas 推理系列加速卡 | `Atlas 推理系列加速卡产品` |
| Atlas 350 加速卡 | `Atlas 350 加速卡` |
| Atlas 200I/500 A2 推理 | `Atlas 200I/500 A2 推理产品` |
| Atlas 300I 推理 | `Atlas 300I 推理产品` |
| Atlas 300I Duo 推理 | `Atlas 300I Duo 推理产品` |
| Atlas 300V 视频解析 | `Atlas 300V 视频解析产品` |
| Atlas 500 A2 智能小站 | `Atlas 500 A2 智能小站` |
| Atlas 800 推理服务器 A2 | `Atlas 800 推理服务器 A2` |
| Atlas 800 训练服务器 | `Atlas 800 训练服务器` |
| Atlas 800I A2 推理服务器 | `Atlas 800I A2 推理服务器` |

## §dtype

提取 `dtype.value` / `dtype_support_description` 中的 dtype 时，**必须**使用以下字符串之一（修正：`HFLOAT8` 为误写，正确名为 `HIFLOAT8`，与 `agent/generators/data_definition/constants.py` 及全部算子文档原文一致）：

### Tensor 数据类型

```
FLOAT32, FLOAT16, BFLOAT16, BF16, DOUBLE, INT8, UINT8, INT16, UINT16,
INT32, UINT32, INT64, UINT64, BOOL, COMPLEX64, COMPLEX128,
FLOAT8_E4M3FN, FLOAT8_E5M2, FLOAT4_E2M1, HFLOAT4, HIFLOAT8
```

### 标量参数"类型"（仅用于 `dtype.value`，不用于 `dtype_support_description` 的 combo）

```
bool, char, int, int64_t, int8_t, double, float, uint64_t, size_t
```

- 文档中出现 `float` / `Float` / `FLOAT` 时 → 统一为 `FLOAT32`（除非上下文明确为 `float16`）；
- 标量参数（`int64_t`、`bool`、`char` 等）的 `dtype.value` 填写 `["bool"]`、`["char"]`、`["int"]`、`["int64_t"]` 等，表示"该参数自身类型"。

## §format

```
ND, NC, NCL, NCHW, NCDHW, NHWC, HWCN, NZ, FRACTAL_NZ, FRACTAL_Z, FRACTAL_Z_3D,
NDC1HWC0, FRACTAL_NZ_C0_16, FRACTAL_NZ_C0_32, NDHWC, NCHW_VECT_C0_16, NC1HWC0, NC1HWC0_C04
```

- Tensor 参数始终用 `List[str]`：多格式如 `["FRACTAL_Z_3D", "ND"]`，单格式也必须写成 `["ND"]`，没有明确格式时使用 `[]`；
- 标量 / 非 Tensor 参数用 `"N/A"`（注意是字符串，不是 `null`）。
- **`NZ` / `FRACTAL_NZ` / `FRACTAL_NZ_C0_16` 张量必须配套应用 `knowledge/aclnn/features/nz_matmul.md` §4.6.5**（v2 新增）。
