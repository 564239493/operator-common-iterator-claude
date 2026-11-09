---
module: gmm_grouplist_common_knowledge
scope: operator
description: aclnnGroupedMatmulV5 groupList 公用约束：aclTensor 形态、groupListType 0/1/2 数据格式与累加语义
default_load: false
triggers:
  - kind: operator_name_eq
    value: "aclnnGroupedMatmulV5"
depends_on: ["grouped_matmul_v5", "quantization"]
---

# GroupedMatmulV5 groupList 公用约束知识库

> 来源：`inputs/GroupedMatmulV5/tests/st/analysis/spec.md` + `skills/ascendc-gmm-case-generator/SKILL.md`

---

## 1. groupList 基本属性

| 属性 | 值 |
|------|-----|
| 参数名 | groupListOptional |
| C++ 类型 | **aclTensor**（非 aclTensorList） |
| Index（ttk 顺序） | 8 |
| dtype | 固定 `int64` |
| format | 固定 `ND` |
| 输入/输出 | 可选输入 |

> **关键区别**：groupList 是 `aclTensor`，shape 编码为单层嵌套 `(g,)`，而非 TensorList 的双层 `((g,),)`。

---

## 2. groupListType 取值与格式

| groupListType | 含义 | tensor shape | 数据格式 | 适用范围 |
|---------------|------|-------------|----------|----------|
| 0 | cumsum（累加和） | (g,) 1D | 非负、单调非递减；末值 ≤ M（gt=0）或 K（gt=2） | 所有全量化 |
| 1 | 每组大小（count） | (g,) 1D | 非负；总和 ≤ M（gt=0）或 K（gt=2） | 所有全量化 |
| 2 | [groupIdx, groupSize] 对 | (g, 2) 2D | 非零组前置，零组置末尾；第二列总和 ≤ x 第一维 | **仅全量化 + groupType=0** |

---

## 3. groupList 与 groupType 的组合约束

| groupType | 支持的 groupListType | groupList tensor shape | 末值/总和约束 | g 来源 |
|-----------|---------------------|------------------------|-------------|--------|
| 0 | 0, 1, 2 | 1D (g,) 或 2D (g,2) | ≤ M | weight.shape[0]（weight 3D） |
| 2 | 0, 1（**不支持 2**） | 1D (g,) | ≤ K | out.shape[0]（out 3D (g,M,N)） |
| -1 | 不适用 | - | - | 1（不分组，全量化不适用） |

---

## 4. g 的推导规则

| groupType | g 来源 | 说明 |
|-----------|--------|------|
| 0 | `weight.shape[0]` | weight 是 3D (g, K, N) 或 (g, N, K) |
| 2 | `out.shape[0]` | out 是 3D (g, M, N) |

> groupList 长度必须等于 g。

---

## 5. M 的推导规则

| groupType | M 来源 |
|-----------|--------|
| 0 | `x.shape[0]`（x 为 (M, K)） |
| 2 | `x.shape[1]`（x 为 (K, M)） |

---

## 6. groupListData 数据生成规则

### 6.1 groupListType=0（cumsum）

```python
per_group = M // g
remainder = M % g
data = []
cumsum = 0
for j in range(g):
    gs = per_group + (1 if j < remainder else 0)
    cumsum += gs
    data.append(cumsum)
# 例: g=4, M=64 → [16, 32, 48, 64]
# 例: g=3, M=100 → [34, 67, 100]
```

约束：非负、单调非递减。

### 6.2 groupListType=1（count）

```python
per_group = M // g
remainder = M % g
data = [per_group + (1 if j < remainder else 0) for j in range(g)]
# 例: g=4, M=64 → [16, 16, 16, 16]
# 例: g=8, M=64 → [8, 8, 8, 8, 8, 8, 8, 8]
```

约束：非负；sum(data) ≤ M。

### 6.3 groupListType=2（pairs，仅 gt=0）

```python
per_group = M // g
remainder = M % g
sizes = [per_group + (1 if j < remainder else 0) for j in range(g)]
data = [[i, size_i] for i, size_i in enumerate(sizes)]
# 零大小组置末尾
# 例: g=4, M=64 → [[0,16],[1,16],[2,16],[3,16]]
# 例: g=4, M=64, 含零组 → [[0,32],[1,32],[2,0],[3,0]]
```

约束：非零组前置，零组置末尾；第二列总和 ≤ M。

---

## 7. groupList 边界场景

| 场景 | 说明 | 示例（gt=0） |
|------|------|-------------|
| 均分组 | 每组大小相同 | g=4, M=128, type=0 → [32,64,96,128] |
| 非均分组 | 余数分到前几组 | g=3, M=100, type=0 → [34,67,100] |
| 单组 | g=1，一个组占满 | g=1, M=64, type=0 → [64] |
| 含零组 | 部分组大小为 0 | g=4, M=64, type=1 → [64,0,0,0] |
| 含零组（type=2） | 零组置末尾 | g=4, M=64, type=2 → [[0,32],[1,32],[2,0],[3,0]] |
| 1组占满 | 一个组占全部 M | g=2, M=128, type=0 → [128,128] |
| 最大组数 | g ≤ 1024（gt=0 单单单） | g ≤ 128（gt=0 单多多，全量化不适用） |
| groupType=2 | 末值 ≤ K | g=2, K=256, type=0 → [128,256] |
| groupType=2 含零组 | 部分组为 0 | g=4, K=256, type=1 → [128,128,0,0] |

---

## 8. groupListData 在 CSV/XLSX 中的存储

### CSV（attributes JSON 字段）

```json
{"groupListData": [16, 32, 48, 64]}
```

CSV 转义：
```
"{""groupListData"": [16, 32, 48, 64]}"
```

| groupListType | groupListData 示例 |
|---------------|-------------------|
| 0 | `[16, 32, 48, 64]`（cumsum） |
| 1 | `[16, 16, 16, 16]`（count） |
| 2 | `[[0, 16], [1, 16], [2, 16], [3, 16]]`（pairs） |

### XLSX（attr_name = groupListData）

- attr_type: `int_array`
- attr_dtype: 空
- attr_value: 同 CSV 格式

### tensor_view_shapes 中的 groupList

groupList 的 `tensor_view_shapes` 存储的是 **tensor shape**（不是数据值）：

| groupListType | tensor shape | 说明 |
|---------------|-------------|------|
| 0 | `(g,)` | 1D |
| 1 | `(g,)` | 1D |
| 2 | `(g, 2)` | 2D |

> 数据值放在 `attributes.groupListData`，tensor shape 放在 `tensor_view_shapes`。

---

## 9. groupList 约束验证清单

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | groupListData 在 attributes 中 | CSV: `'groupListData' in attrs` |
| 2 | groupList 是 aclTensor | index 8 shape 为 `(g,)` 或 `(g,2)`，非 `((g,),)` |
| 3 | groupListData 长度 = g | `len(groupListData) == g` |
| 4 | groupListType=0 is cumsum | 非负、单调非递减 |
| 5 | groupListType=1 is non-negative | 非负、sum ≤ M（gt=0）或 K（gt=2） |
| 6 | groupListType=2 only gt=0 | `not (gt == 2 and glt == 2)` |
| 7 | groupListType=2 零组置末尾 | 非零组在前，零组在后 |
| 8 | 末值 ≤ M（gt=0）或 K（gt=2） | cumsum 末值或 count 总和不超过 M/K |
| 9 | groupList dtype = int64 | `dtypes[8] == 'int64'` |
| 10 | groupList format = ND | `formats[8] == 'ND'` |

> L2 异常用例（`_nc_` 前缀）故意违反约束，验证脚本应跳过。

---

## 10. groupList 与 K 维一致性

| groupType | x shape | weight shape | K 一致性 | groupList 末值约束 |
|-----------|---------|-------------|----------|-------------------|
| 0 | (M, K) | (g, K, N) 未转置 / (g, N, K) 转置 | x[-1] == weight 中 K 维 | ≤ M |
| 2 | (K, M) | (K, N) | x[0] == weight[0] | ≤ K |

### K 维位置判定

| 条件 | 判定 |
|------|------|
| gt=0, weight.shape[1] == x.shape[-1] | weight = (g, K, N)，未转置，K 在 dim=1 |
| gt=0, weight.shape[2] == x.shape[-1] | weight = (g, N, K)，已转置，K 在 dim=2 |
| gt=2 | weight = (K, N)，K 在 dim=0 |
