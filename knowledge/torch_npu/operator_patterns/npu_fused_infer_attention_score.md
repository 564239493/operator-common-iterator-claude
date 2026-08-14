---
module: npu_fused_infer_attention_score
description: npu_fused_infer_attention_score 的大型场景矩阵与输出检查单
triggers:
  - kind: operator_name_eq
    value: "torch_npu.npu_fused_infer_attention_score"
depends_on: ["attention_family", "quantization", "collections_and_grouped_ops"]
---
# npu_fused_infer_attention_score 专项检查单

该算子文档通常很长并含多张 HTML 条件表。必须处理输入文档的完整参数段、表格和约束段，禁止只读取开头的 query/key/value 描述。所有数字、模式和产品约束均以本次输入文档为准，不得因专项知识来自其他版本而覆盖输入证据。

## 场景拆分顺序

建议按以下键逐层构造场景，而不是生成一个全局约束交集：

1. 产品及推理/图模式范围；
2. `Q_S == 1`（decode）或 `Q_S > 1`（prompt）；
3. `input_layout`，包括普通、TND/NTD、PA 以及文档定义的 transformed/NZ 组合布局；
4. 普通 KV Tensor 或 TensorList/分段输入；
5. page attention、shared prefix、padding、rope 的 presence；
6. 非量化、量化、伪量化/antiquant 及 scale 表示方式；
7. sparse mode、mask/pse 与 `softmax_lse_flag`。

每个叶场景绑定 query/key/value dtype、rank/shape、head 关系、序列参数、block 规则、辅助 Tensor presence 和输出规则。

## Q_S 分支不能合并

- `Q_S == 1` 是增量分支，`Q_S > 1` 是全量分支。两部分文档中同名参数的合法域可能不同，
  必须生成带 Q_S/layout 守卫的关系，禁止取两段规则的全局交集或并集。
- `Q_S` 不是独立输入参数，必须从当前 `input_layout` 对应的 query S 轴派生。BSH、
  BSND 的 S 轴为 `shape[1]`，BNSD 的 S 轴为 `shape[2]`；TND/NTD 使用总 token
  轴及 cumulative sequence list 语义，不得直接套用普通 layout 的 `shape[1]`。
- 文档对 B、N、S、D 给出的上限以及 D 的 16/32/64 对齐要求，必须按
  `Q_S`、dtype（含 int4 物理表示）和产品条件化。示例中的超时尺寸只是风险示例，
  不能反向收紧合法上限。
- `query`、`key`、`value` 中 D/N/S 轴的位置随 layout 改变。先建立 layout 到轴索引的
  映射，再生成上限、对齐、相等或整除关系；不得在所有 layout 上复用一个固定轴。
- 复合 layout 的下划线左侧描述输入排布，右侧描述输出转换排布，不能把后缀当成输入
  rank。按当前文档：
  - 输入 rank 3：`BSH`、`BSH_NBSD`、`TND`、`TND_NTD`、`NTD_TND`；
  - 输入 rank 4：`BSND`、`BNSD`、`BNSD_BSND`、`BSND_NBSD`、`BNSD_NBSD`。
  至少生成以下可执行 DSL 关系，禁止使用 `.dimensions.value`：

  ```python
  (input_layout.range_value not in
   ["BSH", "BSH_NBSD", "TND", "TND_NTD", "NTD_TND"]) or \
  (len(query.shape) == 3)

  (input_layout.range_value not in
   ["BSND", "BNSD", "BNSD_BSND", "BSND_NBSD", "BNSD_NBSD"]) or \
  (len(query.shape) == 4)
  ```
- BSH/BSH_NBSD 中 `query.shape[2]` 是 H，不是 D。必须先约束
  `query.shape[2] % num_heads.range_value == 0`，再以
  `query.shape[2] // num_heads.range_value` 表示 D；不得将 BSH 的最后一轴直接当 D。
  BSND/BNSD 以及 TND 系列再按文档轴映射使用显式 D 轴。最后一轴统一写
  `shape[-1]`，禁止 `shape[shape.__len__()-1]`。

## 必查关系

- `num_heads`、`num_key_value_heads` 与 Q/KV 的 N/H 轴关系随 layout 改变；GQA/MQA、NZ/transformed layout 的整除和 head 组合不能套用普通 BSH 公式。
  `num_key_value_heads == 0` 是“与 query head 数相同”的哨兵，不能参与除法；非零时才约束
  `num_heads % num_key_value_heads == 0` 且比值不大于文档上限。BNSD/BSND 等显式 N
  轴场景还必须将标量 head 数绑定到对应 Tensor N 轴。
- 普通 Tensor 输入下 `key.shape == value.shape` 是硬约束；TensorList/非连续分支按文档
  单独处理 batch、元素个数、N/D 关系，不得用普通 Tensor 的全 shape 等式替代。
- `actual_seq_lengths` 与 `actual_seq_lengths_kv` 是 Python `List[int]`，不是 Tensor。TND/NTD 场景的前缀和、batch 数上限、末值与 T 关系要保留；当前 schema 无法完整表达序列内容时标记 `SCHEMA_GAP`。
  长度关系使用 `len(actual_seq_lengths)`，不得访问 `.shape`。文档给出 TND/NTD
  必传、两个 list 共同表示 effective batch、元素数上限、非负和非递减时，应分别保留
  presence/长度关系；无法对任意元素量化的内容约束标记
  `SCHEMA_GAP:sequence_element_relation`，不能静默遗漏。
- `actual_shared_prefix_len` 是 optional `List[int]`，不是 Tensor。文档的“存在时 shape
  为 `[1]`”必须写 `array_length.value=[1]`，不得误写成
  `allowed_range_value=[1,null]`。它的唯一元素还必须不大于
  `key_shared_prefix` 和 `value_shared_prefix` 各自在当前 `input_layout` 下的 S 轴；
  用带 `actual_shared_prefix_len is None`、共享前缀参数 presence 和 layout 分支守卫的
  `shape_value_dependency` 表达。BSH/BSND 的 S 轴为 1，BNSD 的 S 轴为 2；其他布局
  只按当前输入文档明确的轴定义提取，不得统一套用 `shape[1]`。
  长度固定为 1 后，元素关系使用 `actual_shared_prefix_len[0]`，不要拿整个
  `.range_value` 序列与整数比较。至少生成：
  `(actual_shared_prefix_len is None) or ((key_shared_prefix is not None) and (value_shared_prefix is not None))`，
  以及分别针对 key/value、按布局选择 S 轴的上界关系，例如
  `(actual_shared_prefix_len is None) or (key_shared_prefix is None) or (input_layout.range_value not in ["BSH", "BSND"]) or (actual_shared_prefix_len[0] <= key_shared_prefix.shape[1])`；
  BNSD 分支把最后一轴索引改为 `shape[2]`。value 侧生成对应的独立约束。
- `key_shared_prefix`、`value_shared_prefix` 自身的 shape 和互等关系也必须带 optional
  presence 守卫，避免参数缺省时仍访问 `.shape`。
- PageAttention 的 `block_table`、`block_size`、KV cache shape、有效 block id 和 padding 规则按 `Q_S`、产品与 layout 条件化。文档声明 block id 不校验时，写成用户前置条件。
- PageAttention 至少审校：`block_table is not None`、rank 为 2、第一轴为 effective B、
  `actual_seq_lengths_kv is not None`、第二轴容量、KV cache 两种排布、key/value shape
  一致和总 block 容量。涉及 `max`、逐 batch ceil-div 或 block id 内容而当前 schema
  无法准确表达时保留 `SCHEMA_GAP`，不得用错误的全局常量替代。
- “PA 未开启时 key/value rank 与 query 相同”的 implication 不能写反。若有效 PA
  条件是 `block_table is not None and block_size != 0`，使用：
  `((block_table is not None) and (block_size.range_value != 0)) or
  (len(value.shape) == len(query.shape))`；key 侧独立生成同类关系。
- PA 开启后不能只检查 presence：BSH/BSND 的 KV cache 为
  `(blocknum, block_size, H)`；BNSD/TND 还可为
  `(blocknum, KV_N, block_size, D)`。必须把 KV shape 中对应轴与
  `block_size.range_value` 绑定，并校验 `block_table.shape[0]` 与 effective B。
- PageAttention 的 `block_size` 不能全局设成一个范围：全量分支的普通 PA、增量分支按
  KV dtype 对齐的 PA、MLA/RoPE 分支以及 NZ 伪量化分支有不同的最小值、最大值、倍数或
  离散候选。必须带 `Q_S`、dtype、rope/NZ 场景守卫；“推荐128”仅是软提示。
- 左 padding 与 PageAttention/prefix/tensorlist 的互斥关系，以及 padding 参数所需的
  actual sequence list 必须双向审校。文档写“小于0时置0”是归一化行为，不得误提取成
  “必须大于等于0”；搬运起止边界仍应按文档表达为输入前置条件。
- `query_padding_size`、`kv_padding_size` 存在时都是 shape `[1]` 的单元素
  int64 Tensor。该规则已经由 A2/CANN 9.0.0 的 FIA tiling 检查验证；仅写
  `dimensions.value=[1]` 只约束 rank 为 1，仍会生成 `(768,)` 等非法 shape。
  除输入卡片的 `dimensions.value=[1]` 外，必须分别生成：

  ```python
  (query_padding_size is None) or (query_padding_size.shape == [1])
  (kv_padding_size is None) or (kv_padding_size.shape == [1])
  ```

  `src_text` 应注明这是该算子的运行时补充约束，不得把“所有 padding Tensor
  都是单元素”提升为 torch_npu 通用规则。
- query/key rope、shared prefix、query/KV padding、mask、pse 的 dtype/shape/presence 都有独立条件和互斥项，不能根据名字自动成对必选。
- `query_rope` 与 `key_rope` 必须同时存在或同时缺省；存在时进入 MLA 分支，分别绑定
  query/key 的 dtype、format 和除 rope D 轴外的 shape。D=128 与 D=512 是两个不同
  叶场景，各自绑定合法 layout、N/D、sparse、PA 和不支持特性，不能合并成
  `D in [128,512]` 后丢失其余条件。
- `sparse_mode` 与 mask shape、pre/next token 的规则按 mode 分支；文中的 `sparse_modew` 等明显笔误保留为 `DOC_GAP`，不要创建不存在的输入名。
- `sparse_mode=0/1` 的完整 mask shape 与 `2/3/4` 的固定 2048 mask shape分开建模；
  mask 最后轴可向上对齐、PA 容量下界和 prefix 长度下界也是不同条件，不能统一成
  `atten_mask.shape[-1] == KV_S`。
- 空 query/KV 的输出可能分别为空 Tensor 或全零 Tensor；按文档条件提取，不要统一成“支持空输入”。

## BSH 系列 head 整除关系

- 对 `BSH`、`BSH_NBSD` 的普通 Tensor 输入，最后一轴是隐藏维 H。定义有效 KV head
  数为：`num_key_value_heads == 0` 时取 `num_heads`，否则取
  `num_key_value_heads`。必须分别约束 query、key、value，不能只检查 query：

  ```python
  (input_layout.range_value not in ["BSH", "BSH_NBSD"]) or \
  (query.shape[2] % num_heads.range_value == 0)

  (input_layout.range_value not in ["BSH", "BSH_NBSD"]) or \
  (num_key_value_heads.range_value != 0) or \
  (key.shape[2] % num_heads.range_value == 0)

  (input_layout.range_value not in ["BSH", "BSH_NBSD"]) or \
  (num_key_value_heads.range_value != 0) or \
  (value.shape[2] % num_heads.range_value == 0)

  (input_layout.range_value not in ["BSH", "BSH_NBSD"]) or \
  (num_key_value_heads.range_value == 0) or \
  (key.shape[2] % num_key_value_heads.range_value == 0)

  (input_layout.range_value not in ["BSH", "BSH_NBSD"]) or \
  (num_key_value_heads.range_value == 0) or \
  (value.shape[2] % num_key_value_heads.range_value == 0)
  ```

  若 key/value 是 TensorList 或 PA cache，必须使用对应分支的轴定义，不能套用上面的
  普通 rank-3 公式。`num_heads`、非零 `num_key_value_heads` 的正值约束应先独立存在，
  避免取模除零。

## 量化与 dtype

- 量化表按行保存 query/key/value dtype、dequant/quant/antiquant scale 与 offset 的 presence、dtype/shape以及输出 dtype。
- 三类基础量化 presence 矩阵必须拆成完整 AND 场景：
  1. int8 输入、int8 输出：`dequant_scale1`、`quant_scale1`、
     `dequant_scale2`、`quant_scale2` 同时存在，`quant_offset2` 可选；
  2. int8 输入、float16 输出：前三个 scale 同时存在，`quant_scale2` 和
     `quant_offset2` 必须缺省；
  3. float16/bfloat16 输入、int8 输出：`quant_scale2` 必须存在，
     `quant_offset2` 可选，前三个 scale 必须缺省。
  禁止把每个 scale 的 presence 拆成独立全局约束，否则会产生跨行笛卡尔积。
- `quant_offset2` 存在时必须与 `quant_scale2` dtype、shape 一致；perchannel 时
  scale 的元素总数与输出 H 或 Q_N*D 的关系是硬约束，而文档列出的推荐 shape 只是软提示。
- combined antiquant 与 key/value separate antiquant 是不同场景；per-tensor/per-channel 等 mode 也分别处理。
- combined antiquant 中非对称模式要求 scale/offset 同时存在，对称模式允许 offset
  缺省；mode 0 的 pertensor/perchannel 与 mode 1 的 pertoken shape、dtype 不同。
  “根据 shape 判断模式”必须与 `antiquant_mode`、Q_S 和 KV dtype 同时落入一个场景。
- separate antiquant 中 scale 的 key/value presence 成对，offset 的 key/value
  presence 也成对；除文档明确允许的 `(key_mode,value_mode)=(0,1)` 外，两个 mode
  及对应 scale/offset shape 保持一致。`Q_S > 1` 只允许 mode 0/1，
  `Q_S == 1` 才允许 0..5；这些是条件子域，不得只保留全局 enum。
- combined 与 separate antiquant 同时传入时，文档定义“separate 优先”，这不是互斥
  关系。提取时保留优先级说明，并对有效的 separate 分支施加约束，不能擅自禁止同时存在。
- `antiquant_mode` 的“传入0或1”是两个离散模式，必须提取成
  `allowed_range_value={"value":[0,1],"type":"enum"}`，禁止写成
  `type="range", value=[[0,1]]`。`key_antiquant_mode`、
  `value_antiquant_mode`、`sparse_mode`、`inner_precise` 等明确列举模式编号的参数
  同理：全局 enum 保存文档列出的候选集合，随 `Q_S`、rope、量化场景变化的合法子集
  另用带守卫的 `value_dependency` 表达。
- query 参数首段只列 fp16/bf16，但后续约束和量化表出现 int8 query 场景。保留场景表中的条件支持，并给全局 query dtype 描述加 `DOC_CONFLICT`；禁止把 int8 提升成所有场景均支持。
- 文档中的 `int4 (int32)` 表示 PyTorch 侧以 int32 承载打包后的 int4。参数 dtype
  保留可执行的 int32 表示，同时在描述和场景关系中保留“8 个 int4 打包”造成的
  N/D/H 轴缩放及 64/8 对齐区别；不得把 int4 和普通 int32 当成两个无关 dtype。
- GQA + NZ 伪量化是独立叶场景：query/KV dtype、D、Q_S、layout、PA、
  block_size、NZ 物理 shape、scale dtype/shape、offset 缺省、inner_precise、
  sparse/mask 以及 head 组合必须整行绑定。不能把其中任一候选提升为普通场景的全局支持。

## Prefix、PSE 与返回值联动

- shared prefix 的 key/value presence 成对；存在时与主 key/value rank、dtype及按 layout
  选定的 N/D/H 轴关系必须带 presence 守卫。prefix S、主 KV S 和 mask/pse 最后轴之间
  的容量关系也必须留在同一 prefix 场景。
- prefix 不支持 PA、左 padding、TensorList，且全 int8 QKV 受限；这些互斥项必须随
  prefix presence 生效，不能写成全局禁用相关特性。
- `pse_shift` 的 dtype 与 query 联动，shape 允许 B 或 1 广播；Q_S、KV_S 轴约束按
  layout/PA/prefix 场景选择。32-byte padding 是建议，不是 shape 等式；D 轴整除要求
  若在对应分支被文档明确为“仅支持”，则是硬约束。

## 返回值与软提示

- 原型固定返回两个 Tensor。`softmax_lse_flag=False` 时，第二槽在当前文档是 shape `[1]`
  的 float32 零 Tensor，而不是省略返回值；shape 1 可结构化，恒零内容标记
  `SCHEMA_GAP:constant_tensor_contents`。
- 输出 shape/dtype 随 layout、Q_S、量化和 rope 场景派生；不要只写“与 query 相同”后遗漏例外。
- `attention_out` 的 D 来自 value，其余逻辑轴来自 query；带输出转换后缀的 layout
  必须先确定输出 layout 再映射物理 shape，不能简单逐轴复制 query。
- `softmax_lse_flag=True` 时普通 layout 输出 `(B,Q_N,Q_S,1)`，TND/NTD_TND
  输出 `(T,Q_N,1)`；False 时仍返回固定第二槽 `[1]` float32 零 Tensor。所有输出关系
  必须带 flag/layout 守卫。
- “建议 padding”“推荐 128”“可能超时”“性能更优”等仅记录为软说明，不能生成硬范围或整除约束。

## 交付前专项反查

1. `input_layout` 的每个候选是否同时定义了 query/KV/output 的轴角色，而不只是写 enum；
2. 是否至少存在 `Q_S == 1` 与 `Q_S > 1` 两个互斥场景；
3. GQA 的 0 哨兵是否避免了除零，非零分支是否写了整除和比值上限；
4. 三类基础量化 presence 矩阵、combined antiquant、separate antiquant 是否彼此隔离；
5. PA、rope、prefix、padding、TensorList 的 presence/互斥是否都有正确的 implication 方向；
6. 所有标量模式是否用 enum，条件子域是否另有带守卫关系；
7. 所有 List[int] 长度是否用 `array_length`/`len()`，没有误用 Tensor `.shape`；
8. HTML 量化表的每一行及 rowspan 产品列是否完整展开，未拆成跨行笛卡尔积；
9. 文档笔误、版本差异和 schema 无法表达的 Tensor 内容关系是否标记 DOC_GAP/SCHEMA_GAP，
   而不是生成不存在的参数或猜测约束。
