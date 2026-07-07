# broadcast 关系（CANN 公共知识）

来源：
https://www.hiascend.com/document/detail/zh/canncommercial/900/API/aolapi/context/common/broadcast%E5%85%B3%E7%B3%BB.md

## 语义

broadcast 描述算子在运算期间如何处理不同形状的张量或数组。算子文档写“满足
broadcast 关系”“经过 broadcast 推导后一致”时，必须把关系落为 `shape_broadcast`
或对应输出轴推导约束，不能仅保留自然语言。

## 广播规则

1. 如果数组间维度数不一致，所有数组向最长形状看齐，形状不足的部分在左侧填充 1，
   直到维度数相同。
2. 如果数组间维度数一致，且某个数组的某一维度为 1，则该维度为 1 的数组可拉伸以
   匹配另一个数组对应维度。
3. 如果数组间维度数不一致，且均没有等于 1 的维度，则广播会失败。

广播一般先按规则 1 左侧补 1 扩维，再按规则 2 拉伸形状。

## 特殊 dtype 限制

当满足 broadcast 关系的两个输入的数据类型或推导后的数据类型为
`COMPLEX64`、`COMPLEX128`、`DOUBLE`、`INT16`、`UINT16`、`UINT64` 中的任一种时，
除了满足上述广播规则，还需满足：连续的需要广播的轴和连续的不需要广播的轴合并之后
的维度要求小于 6。

## 约束提取要求

- 两个完整 shape 满足 broadcast：使用 `expr_type="shape_broadcast"`，按右对齐规则
  检查每个轴相等或其中一方为 1。
- 某个单独轴满足 broadcast（如 batch 轴 b）：表达为
  `a.shape[i] == b.shape[j] or a.shape[i] == 1 or b.shape[j] == 1`。
- 输出轴“经过 broadcast 推导后一致”时，输出轴应等于广播结果：
  若 `out_b` 是 `a_b` 与 `b_b` 的 broadcast 结果，则
  `out_b == max(a_b, b_b)` 在二者满足 broadcast 前提下成立；用 Python 表达式可写为
  `(out.shape[k] == a.shape[i] if b.shape[j] == 1 else out.shape[k] == b.shape[j] if a.shape[i] == 1 else out.shape[k] == a.shape[i] == b.shape[j])`。
- 对 MatMul 类文档中“self 的第一个维度 b 与 mat2 的第一个维度 b 满足 broadcast 关系”
  以及 “out 的 b 与 self、mat2 的 b 经过 broadcast 推导后一致”，必须同时提取
  输入 batch broadcast 约束和输出 batch 结果约束。
