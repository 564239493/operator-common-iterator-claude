---
module: gmm_quant_common_knowledge
scope: operator
description: aclnnGroupedMatmulV5 全量化公共约束：TensorList 长度、groupType/groupListType、始终为空参数与 ttk Tensor 顺序
default_load: false
triggers:
  - kind: operator_name_eq
    value: "aclnnGroupedMatmulV5"
depends_on: ["grouped_matmul_v5", "quantization"]
---

# GroupedMatmulV5 全量化公共约束知识库

> 来源：`inputs/GroupedMatmulV5/tests/st/analysis/spec.md` + `skills/ascendc-gmm-case-generator/SKILL.md`

---

## 1. 全量化通用约束（所有量化场景适用）

| 约束 | 内容 |
|------|------|
| **TensorList 长度** | x、weight、out 最多 1 个 tensor（仅"单单单"） |
| **数据类型排除** | x、weight 不支持 INT16；x 不支持 INT4 |
| **groupType** | 仅支持 0（M轴）和 2（K轴）；-1 不适用 |
| **groupListType** | 支持 0/1/2；**type=2 仅全量化+groupType=0** |
| **splitItem** | 仅 2/3（单 tensor 输出） |
| **format** | 全量化所有参数仅 ND |
| **weight 转置** | groupType=0 可转置/不转置；groupType=2 weight 不能转置，x 必须转置 |
| **offset** | 全量化始终为空 |
| **antiquantScale/Offset** | 全量化始终为空 |
| **activation 系列** | activationInput/QuantScale/QuantOffset/FeatureOut/dynQuantScaleOut 始终为 nullptr |

---

## 2. 始终为空的参数（全量化通用）

| Index | 参数 | 原因 |
|-------|------|------|
| 4 | offsetOptional | 全量化不使用 offset |
| 5 | antiquantScaleOptional | 仅伪量化 |
| 6 | antiquantOffsetOptional | 仅伪量化 |
| 9 | activationInputOptional | 仅 nullptr |
| 10 | activationQuantScaleOptional | 仅 nullptr |
| 11 | activationQuantOffsetOptional | 仅 nullptr |
| 13 | activationFeatureOutOptional | 仅 nullptr |
| 14 | dynQuantScaleOutOptional | 仅 nullptr |

---

## 3. ttk 框架 Tensor 顺序（C header 原始顺序）

| Index | 参数名 | C++ 类型 | 输入/输出 |
|-------|--------|----------|----------|
| 0 | x | aclTensorList | 输入 |
| 1 | weight | aclTensorList | 输入 |
| 2 | biasOptional | aclTensorList | 可选输入 |
| 3 | scaleOptional | aclTensorList | 可选输入 |
| 4 | offsetOptional | aclTensorList | 可选输入（全量化为空） |
| 5 | antiquantScaleOptional | aclTensorList | 可选输入（全量化为空） |
| 6 | antiquantOffsetOptional | aclTensorList | 可选输入（全量化为空） |
| 7 | perTokenScaleOptional | aclTensorList | 可选输入 |
| 8 | groupListOptional | **aclTensor**（非 TensorList） | 可选输入 |
| 9 | activationInputOptional | aclTensorList | 可选输入（nullptr） |
| 10 | activationQuantScaleOptional | aclTensorList | 可选输入（nullptr） |
| 11 | activationQuantOffsetOptional | aclTensorList | 可选输入（nullptr） |
| 12 | out | aclTensorList | 输出 |
| 13 | activationFeatureOutOptional | aclTensorList | 输出（nullptr） |
| 14 | dynQuantScaleOutOptional | aclTensorList | 输出（nullptr） |

### 属性参数（5个）

| 参数名 | 类型 | 说明 |
|--------|------|------|
| splitItem | int64 | 输出合并方式，仅 2/3 |
| groupType | int64 | 分组轴，0 或 2 |
| groupListType | int64 | groupList 格式，0/1/2 |
| actType | int64 | 激活函数类型，0/1/2/4/5（3 不支持） |
| tuningConfigOptional | aclIntArray* | 调优配置，所有框架必须包含，值为 `[0]` |

---

## 4. shape 约束（按 groupType）

### groupType=0（M轴分组）

| 参数 | 维度 | shape | 转置 |
|------|------|-------|------|
| x | 2D | (M, K) | 不转置 |
| weight | 3D | (g, K, N) 不转置 / (g, N, K) 转置 | 可转置 |
| out | 2D | (M, N) | - |
| bias | 2D | (g, N) 或 null | - |
| groupList | 1D/2D | type=0/1 为 (g,)，type=2 为 (g,2) | - |

### groupType=2（K轴分组）

| 参数 | 维度 | shape | 转置 |
|------|------|-------|------|
| x | 2D | (K, M)，stride=[1, M] | **必须转置** |
| weight | 2D | (K, N) | **不能转置** |
| out | 3D | (g, M, N) | - |
| bias | - | **不支持 bias** | - |
| groupList | 1D | (g,)，**groupListType=2 不支持** | - |

---

## 5. 各量化子场景约束

### 5.1 静态量化（无 perTokenScale）

**入参为空**：offsetOptional、antiquantScaleOptional、antiquantOffsetOptional、perTokenScaleOptional、activationInputOptional

| 子场景 | groupType | x | weight | bias | scale dtype | scale shape | out | actType |
|--------|-----------|---|--------|------|------------|-------------|-----|---------|
| S1 | 0 | INT8 | INT8 | INT32/null | UINT64/INT64 | (g,N) T-C / (g,1) T-T | BF16/FP16/INT8 | 仅0 |
| S2 | 0 | INT8 | INT8 | INT32/null | null/UINT64/INT64 | - | INT32 | 仅0 |
| S3 | 0 | INT8 | INT8 | INT32/BF16/FP32/null | BF16/FP32 | (g,N) T-C / (g,1) T-T | BF16 | 0/1/2/4/5（仅T-C） |
| S4 | 0 | INT8 | INT8 | INT32/FP16/FP32/null | FP32 | (g,N) T-C / (g,1) T-T | FP16 | 0/1/2/4/5（仅T-C） |
| S5 | 0 | HIFLOAT8 | HIFLOAT8 | null | UINT64/INT64 | (g,N) / (g,1) | BF16/FP16/FP32 | 仅0 |
| S6 | 0/2 | HIFLOAT8 | HIFLOAT8 | null | FP32 | (g,N) / (g,1) | BF16/FP16/FP32 | 仅0 |
| S7 | 0 | FLOAT8_E5M2/E4M3FN | FLOAT8_E5M2/E4M3FN | null | UINT64/INT64 | (g,N) / (g,1) | BF16/FP16/FP32 | 仅0 |
| S8 | 0/2 | FLOAT8_E5M2/E4M3FN | FLOAT8_E5M2/E4M3FN | null | FP32 | (g,N) / (g,1) | BF16/FP16/FP32 | 仅0 |

> scale shape：perchannel=(g,N)，pertensor=(g,1)/(g,)；**out=INT8 不支持 pertensor**

**公式**（950PR offset 为空）：
- 无 bias：`y = (x @ w) * scale`
- x=INT8, bias=INT32：`y = (x @ w + bias) * scale`
- x=INT8, bias=BF16/FP16/FP32：`y = (x @ w) * scale + bias`

### 5.2 动态量化 T-T/T-C/K-T/K-C（有 perTokenScale）

**入参为空**：offsetOptional、antiquantScaleOptional、antiquantOffsetOptional、activationInputOptional

| 子场景 | groupType | x | weight | bias | scale dtype | perTokenScale | out | actType |
|--------|-----------|---|--------|------|------------|---------------|-----|---------|
| D1 | 0 | INT8 | INT8 | INT32/BF16/FP32/null | BF16/FP32 | FP32 | BF16 | 0/1/2/4/5（仅K-C） |
| D2 | 0 | INT8 | INT8 | INT32/FP16/FP32/null | FP32 | FP32 | FP16 | 0/1/2/4/5（仅K-C） |
| D3 | 0/2 | HIFLOAT8 | HIFLOAT8 | null | FP32 | FP32 | BF16/FP16/FP32 | 仅0 |
| D4 | 0/2 | FLOAT8_E5M2/E4M3FN | FLOAT8_E5M2/E4M3FN | null | FP32 | FP32 | BF16/FP16/FP32 | 仅0 |

> INT8 动态量化 perTokenScale **不支持 pertensor**，即 INT8 仅 K-T 或 K-C

**量化模式判定**（由 scale shape 和 perTokenScale shape 共同决定）：

| 量化模式 | scale shape | perTokenScale (gt=0) | perTokenScale (gt=2) |
|----------|-------------|----------------------|----------------------|
| T-T (pertensor-pertensor) | (g,1)/(g,) | (g,1)/(g,) | (g,1)/(g,) |
| T-C (pertensor-perchannel) | (g,N) | (g,1)/(g,) | (g,1)/(g,) |
| K-T (pertoken-pertensor) | (g,1)/(g,) | (M,) | (g,M) |
| K-C (pertoken-perchannel) | (g,N) | (M,) | (g,M) |

**公式**：
- 无 bias：`y = (x @ w) * scale * perTokenScale`
- x=INT8, bias=INT32：`y = (x @ w + bias) * scale * perTokenScale`
- x=INT8, bias=BF16/FP16/FP32：`y = (x @ w) * scale * perTokenScale + bias`

### 5.3 动态量化 MX（gsM=1, gsN=1, gsK=32）

**入参为空**：offsetOptional、antiquantScaleOptional、antiquantOffsetOptional、activationInputOptional

| 子场景 | groupType | x | weight | bias | scale | perTokenScale | out |
|--------|-----------|---|--------|------|-------|---------------|-----|
| MX1 | 0/2 | FLOAT8_E5M2/E4M3FN | 同 x | null | FLOAT8_E8M0 | FLOAT8_E8M0 | BF16/FP16/FP32 |
| MX2 | 0 | FLOAT4_E2M1 | FLOAT4_E2M1 | FP32/null | FLOAT8_E8M0 | FLOAT8_E8M0 | BF16/FP16/FP32 |

> MXFP4（x=FLOAT4_E2M1）仅支持 groupType=0

**scale shape**：

| groupType | weight 不转置 | weight 转置 |
|-----------|---------------|-------------|
| 0 | (g, ceil(K/64), N, 2) | (g, N, ceil(K/64), 2) |
| 2 | (K/64+g, N, 2) | -（不能转置） |

**perTokenScale shape**：

| groupType | shape |
|-----------|-------|
| 0 | (M, ceil(K/64), 2) |
| 2 | (K/64+g, M, 2) |

> scale 最后一维为 2（对应 E8M0 的共享指数对）

**FLOAT4_E2M1 额外约束**：K 必须为偶数且 K≠2；weight 非转置时 N 必须为偶数

**公式**：`y[m,n] = Σ_j ((Σ_{k=0}^{31} xSlice * weightSlice) * (perTokenScale[m,j] * scale[j,n])) + bias[n]`

### 5.4 动态量化 G-B（gsM=1, gsN=128, gsK=128，无 bias）

**入参为空**：biasOptional、offsetOptional、antiquantScaleOptional、antiquantOffsetOptional、activationInputOptional

| 子场景 | groupType | x | weight | scale | perTokenScale | out |
|--------|-----------|---|--------|-------|---------------|-----|
| GB1 | 0/2 | HIFLOAT8 | HIFLOAT8 | FP32 | FP32 | BF16/FP16/FP32 |
| GB2 | 0/2 | FLOAT8_E5M2/E4M3FN | FLOAT8_E5M2/E4M3FN | FP32 | FP32 | BF16/FP16/FP32 |

**scale shape**：

| groupType | weight 不转置 | weight 转置 |
|-----------|---------------|-------------|
| 0 | (g, ceil(K/128), ceil(N/128)) | (g, ceil(N/128), ceil(K/128)) |
| 2 | (K/128+g, ceil(N/128)) | - |

**perTokenScale shape**：

| groupType | shape |
|-----------|-------|
| 0 | (M, ceil(K/128)) |
| 2 | (K/128+g, M) |

**公式**：`y[m,n] = Σ_j ((Σ_{k=0}^{127} xSlice * weightSlice) * (perTokenScale[m,j] * scale[j,n/128]))`，bias 为空

---

## 6. actType 支持条件

仅当以下条件**全部满足**时支持 actType > 0：
1. x = INT8
2. weight = INT8
3. out = BFLOAT16 或 FLOAT16
4. 量化模式 = 静态 T-C 或 动态 K-C
5. scale = FLOAT32 或 BFLOAT16
6. 支持 actType ∈ {0, 1, 2, 4, 5}（3 不支持）

其余全量化场景：actType 仅 0。

| actType | 含义 |
|---------|------|
| 0 | 无激活 |
| 1 | ReLU |
| 2 | GELU (tanh 近似) |
| 3 | GELU (error function) — **不支持** |
| 4 | Fast GELU |
| 5 | SiLU/Swish |

---

## 7. groupList 共用场景

### 7.1 groupListType 取值与格式

| groupListType | 含义 | shape | 数据要求 | 适用范围 |
|---------------|------|-------|----------|----------|
| 0 | cumsum（累加和） | (g,) 1D | 非负、单调非递减；末值 ≤ M（gt=0）或 K（gt=2） | 所有全量化 |
| 1 | 每组大小（count） | (g,) 1D | 非负；总和 ≤ M（gt=0）或 K（gt=2） | 所有全量化 |
| 2 | [groupIdx, groupSize] 对 | (g, 2) 2D | 非零组前置，零组置末尾；第二列总和 ≤ M | **仅全量化 + groupType=0** |

### 7.2 groupList dtype / format

- dtype：固定 `int64`
- format：固定 `ND`

### 7.3 groupList 与 groupType 的组合约束

| groupType | 支持的 groupListType | groupList shape | 末值/总和约束 | g 来源 |
|-----------|---------------------|-----------------|-------------|--------|
| 0 | 0, 1, 2 | 1D (g,) 或 2D (g,2) | ≤ M | weight.shape[0] |
| 2 | 0, 1（**不支持 2**） | 1D (g,) | ≤ K | out.shape[0] |

### 7.4 groupList 数据生成规则

**groupListType=0（cumsum）**：
```python
per_group = M // g
data = []
cumsum = 0
for j in range(g):
    cumsum += per_group + (1 if j < M % g else 0)
    data.append(cumsum)
# 例: g=4, M=64 → [16, 32, 48, 64]
```

**groupListType=1（count）**：
```python
per_group = M // g
data = [per_group + (1 if j < M % g else 0) for j in range(g)]
# 例: g=4, M=64 → [16, 16, 16, 16]
```

**groupListType=2（pairs，仅 gt=0）**：
```python
data = [[i, size_i] for i, size_i in enumerate(per_group_sizes)]
# 零大小组放末尾
# 例: g=4, M=64 → [[0,16],[1,16],[2,16],[3,16]]
```

### 7.5 groupList 边界场景

| 场景 | 说明 | 示例（gt=0, type=0） |
|------|------|---------------------|
| 均分组 | 每组大小相同 | g=4, M=128 → [32,64,96,128] |
| 非均分组 | 余数分到前几组 | g=3, M=100 → [34,67,100] |
| 单组 | g=1 | g=1, M=64 → [64] |
| 含零组 | 部分组大小为 0 | g=4, M=64, type=1 → [64,0,0,0] |
| 1组占满 | 一个组占全部 M | g=2, M=128 → [128,128] |
| groupType=2 | 末值 ≤ K | g=2, K=256 → [128,256] |
| 最大组数 | g ≤ 1024（gt=0 单单单） | - |

---

## 8. 动态量化特殊场景处理规则

| 条件 | 量化模式判定 | 来源 |
|------|-------------|------|
| M/K分组, N=1, scale=(g,1) | 优先 pertensor | spec line 126 |
| M分组, g=M, perTokenScale=(g,) | pertoken | spec line 127 |
| M分组, g=M, K≤128, perTokenScale=(g,1) | 由 weight 模式决定 | spec line 127 |
| K分组, K<128, N≤128, scale=(g,1) | 按 G-B 量化处理 | spec line 128 |
| K分组, M=1, perTokenScale=(g,1) | 优先 pertensor | spec line 129 |
| K分组, K<128, M=1, perTokenScale=(g,1) | N≤128: pergroup; N>128: 由 weight 模式决定 | spec line 130 |
| K分组, K<128, M≠1 | N≤128: pergroup; N>128: 由 weight 模式决定 | spec line 131 |

---

## 9. 数据范围覆盖要求

### 浮点 dtype（bias、scale）

- 覆盖极值 / 零 / ±1 / 最小正数 / inf / nan / ±0

### 整型 dtype

- INT8（x/weight）：-128 / 0 / 1 / 127
- INT32（bias）：类型最小/最大值 / 0 / ±1
- UINT64（scale）：0 / 1 / 类型最大值
- INT64（scale/groupList）：0 / 1 / 正整数

### 低精度浮点

| dtype | 范围 | 说明 |
|-------|------|------|
| HIFLOAT8 | ±57344 | 点域位、指数、尾数具体看数据 |
| FLOAT8_E5M2 | ±57344 | 指数5位尾数2位 |
| FLOAT8_E4M3FN | ±448 | 指数4位尾数3位 |
| FLOAT4_E2M1 | ±6.0 | 指数2位尾数1位 |
| FLOAT8_E8M0 | 2 的幂次 | 仅指数位（8位） |

### groupList 数据约束

- groupListType=0：非负、单调非递减
- groupListType=1：非负数列
- groupListType=2：非负数列且零组置末尾
- groupList 第1维最大 1024（gt=0 单单单）

---

## 10. CSV 编码规范

### 占位符

| 框架 | 空 tensor | 说明 |
|------|----------|------|
| CSV (ttk) | `None` | 不能用 `((),)` |
| XLSX | `[[]]` | 不能用 `null` |

### 输出 tensor 补全

`output_tensor_indexes` 必须为 `(12, 13, 14)`：

| Index | 状态 | shape | dtype | format |
|-------|------|-------|-------|--------|
| 12 | 始终存在 | 实际输出 shape | 实际 dtype | 实际 format |
| 13 | **始终 nullptr** | `((),)` | `('bf16',)` | `('ND',)` |
| 14 | **始终 nullptr** | `((),)` | `('bf16',)` | `('ND',)` |

### attributes 必须包含

- `splitItem`：2 或 3
- `groupType`：0 或 2
- `groupListType`：0/1/2
- `actType`：0/1/2/4/5
- `groupListData`：按 groupListType 格式填充
- `tuningConfigOptional`：`[0]`（所有框架必须）
