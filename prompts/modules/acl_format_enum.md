---
module: acl_format_enum
description: ACL_FORMAT 名称↔数值 枚举参考表（格式转换算子把 int 型 format 参数换算为整数时使用）
triggers: []
depends_on: []
---

# 模块 acl_format_enum（按需加载，随 format_cast 装配）

> 本模块是一份**纯参考数据**，不含提取规则。当 `format_cast` 模块装配时
> （算子名匹配 `aclnn.*FormatCast` 或文档含 `aclnnXxxCalculateSizeAndFormat` 等
> 子接口），本表由 `scripts/select_prompt.py` 通过 `depends_on` 连带加载到活跃
> 提示词末尾。提取器在需要把「格式名」换算为「整数枚举码」时查本表。

## A. ACL_FORMAT 名称 ↔ 数值 枚举表

| 枚举值 | 数值 | 描述 | 典型应用场景 |
|--------|------|------|-------------|
| ACL_FORMAT_UNDEFINED | -1 | 未定义格式 | 初始化或错误状态 |
| ACL_FORMAT_NCHW | 0 | 通道优先格式 | PyTorch默认格式，推理常用 |
| ACL_FORMAT_NHWC | 1 | 通道最后格式 | TensorFlow常用格式 |
| ACL_FORMAT_ND | 2 | 通用N维格式 | 支持任意维度数据 |
| ACL_FORMAT_NC1HWC0 | 3 | 5D优化格式 | 昇腾内部数据格式，C维度按16对齐分块 |
| ACL_FORMAT_FRACTAL_Z | 4 | 分形格式 | 卷积权重优化格式 |
| ACL_FORMAT_NC1HWC0_C04 | 12 | C0=4的特殊格式 | 特定硬件优化场景 |
| ACL_FORMAT_HWCN | 16 | 高度宽度通道批次格式 | 图像处理专用格式 |
| ACL_FORMAT_NDHWC | 27 | 3D卷积通道最后格式 | 3D卷积神经网络 |
| ACL_FORMAT_FRACTAL_NZ | 29 | NZ分形格式 | 矩阵乘法优化，Cube Unit直接消费 |
| ACL_FORMAT_NCDHW | 30 | 3D卷积通道优先格式 | 3D卷积神经网络，视频处理 |
| ACL_FORMAT_NDC1HWC0 | 32 | 6维数据格式 | 3D卷积的5D优化格式扩展 |
| ACL_FRACTAL_Z_3D | 33 | 3D卷积权重格式 | Conv3D/MaxPool3D/AvgPool3D等3D算子 |
| ACL_FORMAT_FRACTAL_NZ_C0_16 | 50 | C0=16的分形格式 | 内部矩阵计算优化 |
| ACL_FORMAT_FRACTAL_NZ_C0_32 | 51 | C0=32的分形格式 | 内部矩阵计算优化 |

## B. §5.3 受控字典短名 ↔ ACL_FORMAT 全名 对照

`constraints.json` 中**张量**参数的 `format.value` 始终用 §5.3 受控字典短名
（字符串），不带 `ACL_FORMAT_` 前缀、不带括号数值。下表给出短名 ↔ 全名 ↔ 数值
的对照，便于在跨参 expr 里把短名与整数对齐：

| §5.3 短名 | ACL_FORMAT 全名 | 数值 | 备注 |
|-----------|----------------|------|------|
| `ND` | ACL_FORMAT_ND | 2 | 自由 rank |
| `NCHW` | ACL_FORMAT_NCHW | 0 | 4D |
| `NHWC` | ACL_FORMAT_NHWC | 1 | 4D |
| `NC1HWC0` | ACL_FORMAT_NC1HWC0 | 3 | 5D，C1/C0 分块 |
| `NC1HWC0_C04` | ACL_FORMAT_NC1HWC0_C04 | 12 | 5D，`NC1HWC0` 的 `C0=4` 特化变体 |
| `FRACTAL_Z` | ACL_FORMAT_FRACTAL_Z | 4 | 4D 权重 |
| `HWCN` | ACL_FORMAT_HWCN | 16 | 图像 |
| `NDHWC` | ACL_FORMAT_NDHWC | 27 | 5D |
| `NZ` / `FRACTAL_NZ` | ACL_FORMAT_FRACTAL_NZ | 29 | **`NZ` 与 `FRACTAL_NZ` 同指 29**，二者在 §5.3 字典中并列存在。**字面保真**：`format.value` 与跨参 expr 只用文档为该张量枚举的那一种短名（参数表写 `NZ` 就全程 `"NZ"`、写 `FRACTAL_NZ` 就全程 `"FRACTAL_NZ"`），**不**把二者并列塞进同一 `format.value` 或 expr 的 `in` 集合 |
| `NCDHW` | ACL_FORMAT_NCDHW | 30 | 5D |
| `NDC1HWC0` | ACL_FORMAT_NDC1HWC0 | 32 | 6D |
| `FRACTAL_Z_3D` | ACL_FRACTAL_Z_3D | 33 | 8D storage |
| `FRACTAL_NZ_C0_16` | ACL_FORMAT_FRACTAL_NZ_C0_16 | 50 | 5D，C0=16 |
| `FRACTAL_NZ_C0_32` | ACL_FORMAT_FRACTAL_NZ_C0_32 | 51 | 5D，C0=32 |
| `NCL` | （未在上方 15 行表内） | — | 仅作 §5.3 短名出现，本方案不涉及其整数；`aclnnNpuFormatCast` 中 `NCL` 只作 `srcTensor.format` 字符串短名 |

## C. 用法说明

1. **张量参数** `format.value`：始终用 §5.3 受控字典短名（字符串列表，如
   `["ND","NZ","NCDHW"]`），**不**写 `ACL_FORMAT_` 前缀，**不**写括号数值。
2. **int 型标量参数**（如 `aclnnNpuFormatCast` 的 `dstFormat`、`actualFormat`、
   `additionalDtype`）：其 `allowed_range_value.value` 与跨参 expr 里的取值用本
   表 §A 的**整数**（如 `29`、`2`、`30`），与参数的 `int` 类型一致。
3. **不得混用**：不得在 `format.value` 里写裸整数 `29`，也不得在 int 参数的
   expr 里写字符串 `"29"`（违 §9.30 d/f）。
4. 跨参 expr 里引用张量 format 时用短名字符串（如 `srcTensor.format == "NZ"`），
   引用 int 参数时用整数（如 `dstFormat.range_value == 29`）；二者通过同表 §B
   的「短名↔整数」对照保证语义一致。

---
