---
module: attention_family
description: torch_npu Attention、稀疏索引与 MLA 文档的场景化审校规则
triggers:
  - kind: operator_name_regex
    value: "(?i)(attention|lightning_indexer|mla_prolog)"
  - kind: doc_contains
    value: "(?i)(input_layout|layout_query|layout_kv|PageAttention|sparse_mode|softmax_lse)"
depends_on: []
---
# Attention / Indexer / MLA 家族审校知识

仅在当前文档出现相应字段时应用以下检查，不得借此补写文档没有的规格。

- 把 query/key/value 的 layout、rank、轴语义、head 数、head dim、sequence length 和 dtype 分开提取。相同字母只在文档明确表示同一维度时建立相等关系。
- BSND、BNSD、BSH、TND、NTD、PA_BSND、PA_NZ 等布局改变轴位置或张量容器语义；每种 layout 都应成为独立场景，禁止用一套固定轴关系覆盖全部布局。
- TND 通常伴随实际序列长度/前缀和参数；PA 通常伴随 block table、block size 或 cache layout。是否必传、长度和累计语义必须逐场景读取。
- GQA/MQA/MHA 的 `num_heads`、`num_key_value_heads`、query heads 和 key/value heads 关系要按文档写出，尤其检查整除、0 表示默认和 transformed/NZ layout 的例外。
- `sparse_mode`、`pre_tokens`、`next_tokens`、mask shape、稀疏索引及 sparse count 往往互相条件化；不要把某一个 mode 的限制提升为全局限制。
- 可选 rope、pse、mask、prefix、padding、block table 的 presence 和 shape 是独立条件，不能从名字推断必选。
- 训练/推理、prompt/decode、dense/paged、quant/non-quant、是否返回 lse/softmax 等场景要保持组合相关性。
- 返回开关为 false 时，文档可能仍返回固定占位 Tensor；必须依据返回值章节和场景表，而不是把输出槽位删除。
- 对性能建议（例如序列长度或 tile 的推荐值）只做软说明；对“用户保证不越界/索引有效”的语句做硬前置条件。
