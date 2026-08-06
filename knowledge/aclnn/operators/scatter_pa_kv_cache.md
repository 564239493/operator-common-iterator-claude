---
module: scatter_pa_kv_cache
description: aclnnScatterPaKvCache 分组 dtype、PA_NZ/NHSD 场景互斥与物理 shape 专项规则
triggers:
  - kind: operator_name_eq
    value: aclnnScatterPaKvCache
depends_on: []
---

## aclnnScatterPaKvCache 专项约束完整性

本模块只适用于 `aclnnScatterPaKvCache`。禁止把以下规则推广到其他 ACLNN 算子。

### 1. “PA_NZ 时可以不一致”只放宽组间关系

文档先规定 key、value、keyCacheRef、valueCacheRef dtype 一致，又说明 PA_NZ 时
key/keyCacheRef 与 value/valueCacheRef 可以不一致。其含义是 key 组与 value 组之间
可以不同，组内跟随始终成立。必须抽取为一条 `type_dependency`：

```text
keyCacheRef.dtype == key.dtype and
valueCacheRef.dtype == value.dtype and
(
  cacheModeOptional.range_value == "PA_NZ" or
  key.dtype == value.dtype
)
```

不得抽取成下面这种会放弃全部组内关系的弱约束：

```text
cacheModeOptional.range_value == "PA_NZ" or (...四者相等...)
```

supplement/诊断补丁看到 SupportInfo 分别出现 value/valueCacheRef=FP16 与
value/valueCacheRef=BF16 时，必须补 `valueCacheRef.dtype == value.dtype`；禁止补成
`valueCacheRef.dtype == FP16 or valueCacheRef.dtype == BF16`。key 组同理。

### 2. 七个编号场景是合法组合，不得交叉笛卡尔积

逐场景保存 `(cacheMode, scatterMode, presence, rank, shape, dtype, format)`。场景一的
`cacheMode=PA_NZ` 与场景七的 `cacheMode=Norm, scatterMode=NHSD` 不得交叉，至少抽取：

```text
scatterModeOptional.range_value != "NHSD" or
cacheModeOptional.range_value == "Norm"
```

空指针与文档等价字符串均可保留在约束候选中；进入 TTK ACLNN CSV 前由 adapter 将
显式 null 物化为文档等价的具体字符串：`cacheModeOptional=null -> "Norm"`、
`scatterModeOptional=null -> "None"`。TTK ACLNN L2 按参数数量校验，禁止直接删除这两个
char* 属性键，也禁止在 CSV attributes 中写 Python `None`。

场景组合至少提取以下约束（表达式中的 `.format` 是标量字符串，不得与单元素列表比较）：

```text
cacheModeOptional.range_value != "PA_NZ" or
  (keyCacheRef.format == "FRACTAL_NZ" and valueCacheRef.format == "FRACTAL_NZ")

cacheModeOptional.range_value == "PA_NZ" or
  (keyCacheRef.format == "ND" and valueCacheRef.format == "ND")

scatterModeOptional.range_value not in ["Alibi", "Rope", "Omni", "Nct", "NHSD"] or
  cacheModeOptional.range_value == "Norm"
```

文档只在场景一使用 `PA_NZ`，基础正向生成时 PA_NZ 与默认连续散写配对；不得生成
`PA_NZ + Rope/Omni/Nct/NHSD` 的交叉笛卡尔积。

### 3. PA_NZ 与 NHSD 必须绑定各自 format 和 shape

- PA_NZ：keyCacheRef/valueCacheRef 为 FRACTAL_NZ；cache 物理 shape 必须按场景一的
  `last_dim = 32 / sizeof(dtype)`、head-size 分块及 32B 对齐公式推导。不得只约束 rank
  或倒数第二维上限。
- NHSD：cacheMode 必须为 Norm，cache storage format 为 ND，cache 轴序必须为
  `[num_blocks, num_head, block_size, head_size]`。
- Norm 的非 NHSD 场景不得套用 NHSD 轴序；PA_NZ 不得套用 NHSD shape。

最终自检必须逐一重放场景一至场景七，确认不存在 `PA_NZ + NHSD`，并确认每个 cache
Tensor 的 dtype、format、rank 和 shape 都能由同一场景分支同时满足。

### 4. 离散 rank 与隐式维度正数门禁

- `value` 文档为 0、3、4 维，`dimensions.value` 必须是 `[0,3,4]`。
- `valueCacheRef` 文档为 0、4、5 维，`dimensions.value` 必须是 `[0,4,5]`。
- `dimensions` 是离散 rank 集合；禁止把上述规则压成 `[0,4]` / `[0,5]`。
- `num_blocks`、`block_size`、`num_head`、`k_head_size`、`v_head_size` 均为 shape
  中的大小/数量语义隐式参数，逐平台提取 `.range_value > 0`，并由实际 Tensor shape
  反推绑定，禁止独立随机出 0、负数或 int 极值。
