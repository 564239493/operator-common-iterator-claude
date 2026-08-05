---
module: matrix_product_family
description: torch_npu 矩阵乘、线性、FFN 与 GMM 接口的轴语义和量化审校规则
triggers:
  - kind: operator_name_regex
    value: "(?i)(matmul|batchmatmul|bmm|linear|ffn|(?:^|_)(?:mm|gmm)(?:_|$))"
depends_on: []
---
# 矩阵乘 / Linear / FFN / GMM 家族审校知识

只按当前文档出现的 transpose、perm、format、group 和 quant 字段应用，不从普通 matmul
公式补全复杂融合接口。

- 先根据 transpose flag、perm 列表、NZ 排布和 int4 打包恢复逻辑 M/K/N，再建立 Reduce/K
  轴相等关系；不能无条件把物理 shape 的最后两轴当 M/K 或 K/N。
- batch broadcast 与 K 轴相等是两类约束；输出 batch 前缀由合法广播结果派生。动态任意
  rank broadcast 无法安全展开时标记 `SCHEMA_GAP`，不要只写一个示例 rank。
- bias 的 rank、广播 shape、dtype 和 presence 常依输出 rank、产品或量化模式变化，需与
  对应场景放在同一 AND 分支。
- int4 常以 int32 承载并压缩物理末维；逻辑 K/N、storage dtype 和物理 shape 分开记录。
- ND/NZ/FRACTAL_NZ 是 storage format；transpose、非连续支持和转换后的亲和格式是独立
  条件。文档只给逻辑矩阵 shape 时不要套 ACLNN 的 NZ 物理维度公式。
- `trans_*`、`perm_*`、group/split 模式会同时改变输入轴解释和输出 shape，不能只提取
  enum 而遗漏门控关系。
- grouped/GMM 的 group list 语义按文档区分前缀和与每组计数；不要假设同一种表示。
