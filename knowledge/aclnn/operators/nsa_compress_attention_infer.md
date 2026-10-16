---
module: nsa_compress_attention_infer
description: aclnnNsaCompressAttentionInfer 的 BSND 布局 shape 轴序、分页容量、压缩一致性、计算预算与可选参数专项规则（11 轮迭代 0→100% 收敛验证）
triggers:
  - kind: operator_name_eq
    value: aclnnNsaCompressAttentionInfer
depends_on: []
---

## aclnnNsaCompressAttentionInfer 专项约束完整性

本模块只适用于 `aclnnNsaCompressAttentionInfer`。禁止把以下规则推广到其他 ACLNN 算子。

### 1. BSND 布局下各 Tensor 的 rank 与轴序（layoutOptional 绑定）

layoutOptional 取值 `TND`（3维）或 `BSND`（4维）。**只测 BSND 时**：

- `query`/`output`/`topKOutput` 必须 4 维 `[B,S,N,D]`，且三者的 rank 与
  `layoutOptional.range_value=="BSND"` 绑定（`len(query.shape)==4` 等）。
  TND 时是 3 维 `[T,N,D]`，两者 shape 轴索引语义完全不同。
- `key`/`value` **固定 3 维** `[numBlocks, blockSize, numKeyValueHeads*D]`，
  不随 layout 变化。
- `blockTableOptional` 固定 2 维 `[B, maxNumBlocksPerSeq]`。

axis 语义（BSND）：`query.shape[0]=B`、`[1]=S`、`[2]=N(numHeads)`、`[3]=D(headDims)`；
`key.shape[0]=numBlocks`、`[1]=blockSize`、`[2]=numKeyValueHeads*D`。

### 2. GQA 与 D 轴关系（倍数/整除）

```text
numHeads.range_value % numKeyValueHeads.range_value == 0
numHeads.range_value // numKeyValueHeads.range_value <= 128
128 % (numHeads.range_value // numKeyValueHeads.range_value) == 0
query.shape[2] == numHeads.range_value
key.shape[2] == numKeyValueHeads.range_value * query.shape[3]   # key D = numKvHeads × query D
value.shape[2] == numKeyValueHeads.range_value * output.shape[3] # value D = numKvHeads × output D
query.shape[3] >= output.shape[3]
```

**易错**：`key.shape[2]` 是 `numKeyValueHeads*D`，不是单 head 的 D。C-021 给了
`key.shape[2] <= 192*numKvHeads`，迭代中实测 `key.shape[2]` 需加绝对上界
`<= 1024`（C-053）防大 D 组合拖慢 NPU。

### 3. 分页容量一致性（blockTable / pageBlockSize / actualCmpKvSeqLen）

这是 11 轮迭代中 MTE（EZ9999 aicore 0x800000，MTE invalid GM address）的头号根因，三条缺一不可：

```text
# 每个 KV block 索引必须落在有效分区内（缺 → INT32 极值寻址越界）
blockTableOptional.range_value >= 0 and blockTableOptional.range_value < key.shape[0]

# blockTable 每行槽位数必须 ≥ ceil(actualCmpKvSeqLen / pageBlockSize)
blockTableOptional.shape[1] >= (actualCmpKvSeqLenOptional.range_value[0] + pageBlockSize.range_value - 1) // pageBlockSize.range_value

# numBlocks 与 maxNumBlocksPerSeq 绝对上界
key.shape[0] <= 512
blockTableOptional.shape[1] <= 512
```

**关键教训**：
- `blockTableOptional.range_value` 是**标量**（迭代实证 ATK 用例折叠后物化为 int），
  约束写 `blockTableOptional.range_value` 而非 `[0]` 下标。
- C-050 若只校验 `range_value[0]`（首元素），B>1 时 actualCmp 数组后部元素仍可使
  所需页数超 blockTable 行宽 → **MTE 回归**。最稳妥是**收窄为单 batch**
  （`actualCmpKvSeqLenOptional.array_length=[1]`），使首元素即全量。

### 4. 压缩一致性（actualCmp / actualSel / compressBlock）

```text
actualCmpKvSeqLenOptional is not None          # 只支持 paged attention，必须传入
actualCmpKvSeqLenOptional.range_value[0] <= 1024
(actualSelKvSeqLenOptional is None) or (actualSelKvSeqLenOptional.range_value[0] <= 4096)
actualCmpKvSeqLenOptional.range_value[0] == (actualSelKvSeqLenOptional.range_value[0] - compressBlockSize.range_value) // compressBlockStride.range_value + 1

# selectBlockCount 与 select/topK 预算
selectBlockSize.range_value >= compressBlockSize.range_value
selectBlockSize.range_value % compressBlockStride.range_value == 0
compressBlockSize.range_value >= compressBlockStride.range_value
selectBlockCount.range_value <= (actualCmpKvSeqLenOptional.range_value[0]-1)*compressBlockStride.range_value + compressBlockSize.range_value) // selectBlockSize.range_value
# 且该值 /selectBlockSize <= 4096
```

**易错**：`selectBlockCount` 过大会触发 `selectBlockNum must be within (0, 4096]`
（CheckTopK tiling 失败），并放大 select/topK 计算量。收敛到 `[1,64]`。

### 5. query S 轴与单 token 判定

文档 456 行：多 token 才传 actualQSeqLenOptional（值须 1~4 且 ≤ actualSel）；否则回退单 token。

```text
(query.shape[1] == 1) and (actualQSeqLenOptional is None)     # 纯单 token 场景
len(actualQSeqLenOptional) == blockTableOptional.shape[0]     # 若 present
output.shape[1] == query.shape[1]
topKOutput.shape[1] == query.shape[1]
```

**易错**：`queryDim1 must be equal to qSeqMaxLen`（CheckQkvShape tiling）根因是
query S 轴与 actualQSeqLenOptional 不一致——缺 actualSel 时应回退单 token
（queryS==1），C-042 必须是完整三段式（None→1 / <1或>4→1 / 1~4→actualQ）。

### 6. 计算预算（NPU 超时收敛）

NPU 超时由**总 KV 体积**驱动，不是单一轴。迭代收敛路径：
`numBlocks≤512` → `key.shape[2]≤1024` → **`key.shape[0]*key.shape[1]*key.shape[2] <= 10000000`（10M 元素）**。
实测 10M 体积下 100/100 用例全部通过；此前 60M 上界仍有大量 TIMEOUT。

### 7. 可选/预留参数

```text
attentionMaskOptional is None    # 文档 "预留参数暂未使用"
topKMaskOptional is None         # 文档 dtype 为空；若生成 int4 会在 ATK create_int4_acl_tensor 崩溃（executor_bug）
```

**易错**：topKMaskOptional 若被 PICT 选为 present 且 dtype=INT4，ATK 执行器创建
tensor 时抛 TypeError。直接约束为 None 最稳。

### 8. 数组参数收窄（aclIntArray 的 Z3 表示）

`actualQSeqLenOptional`/`actualCmpKvSeqLenOptional`/`actualSelKvSeqLenOptional`
是 aclIntArray（list）。**收敛为单 batch（`array_length=[1]`）** 后：
- Z3 生成端 `.range_value[0]` 语义成立（首元素即全量）
- 生成速度大幅提升（41分钟→5分钟）
- 回归求值器会把单元素数组物化为标量 int，`.range_value[0]` 抛 TypeError——这是
  **generator 工具缺陷**，约束在 Z3 生成端与真机执行端均真实生效，不属约束问题。

### 9. 迭代验证结论（11 轮 0→100%）

最终达成 100/100 通过的约束组合：单 batch（array_length=[1]）+ 单 token（queryS==1）+
KV 体积≤10M + numBlocks≤512 + 分页容量/压缩一致性 + blockTable 索引值域 + 禁 int4。
任何一条缺失都会在真机上复现 TIMEOUT 或 MTE。