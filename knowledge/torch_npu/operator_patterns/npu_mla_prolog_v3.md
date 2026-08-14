---
module: npu_mla_prolog_v3
description: npu_mla_prolog_v3 的 26.0.0 专项场景矩阵、cache 与文档冲突检查单
triggers:
  - kind: operator_name_eq
    value: "torch_npu.npu_mla_prolog_v3"
depends_on: ["attention_family", "quantization", "inplace_and_stateful_ops"]
---
# npu_mla_prolog_v3 专项检查单

这是 26.0.0 文档的反查索引，不是脱离当前文档的规格来源。该文档条件密集，必须先还原 HTML 场景矩阵，再生成约束。

## 接口与主体规格

- 产品范围必须以当前算子文档的“产品支持”章节为准；26.0.0 文档同时包含
  Ascend 950、Atlas A3、Atlas A2 的分平台说明，不得沿用“仅 Atlas A3”旧结论。
  原型具有 11 个主体输入、随后大量条件参数，并固定返回五个 Tensor。
- 原型本身没有 `*`。参数段模板文字声称星号前为位置参数、之后为关键字参数时，记录 `DOC_CONFLICT`，不得凭模板添加原型中不存在的 keyword-only 边界。
- 逐项核对当前文档给出的固定维度，例如 He、Hcq、Hckv、D、Dr、Nkv；这些符号在 BSND、NZ 和合轴表示中的轴位置不同，禁止只写一个无 layout 条件的 shape。
- `cache_mode` 至少涉及 PA_BSND、PA_NZ、PA_BLK_BSND、PA_BLK_NZ、BSND、TND 场景。分别提取 kv/kr cache shape、是否允许空 Tensor、cache index、actual sequence length、block size 与 block table/块索引语义。
- 当前文档给出的 BlockSize 为 16~1024 且需 16 对齐；不要把连续范围误成离散 rank，也不要用变量模变量表达。

## cache、副作用和校验责任

- `kv_cache` 与 `kr_cache` 会被原地更新。五个返回槽与两个被更新输入是不同语义；在 description 中标记 mutation/alias 的 `SCHEMA_GAP`。
- `cache_index`、`actual_seq_len` 等值合法性若文档声明“不做校验、用户保证”，仍提取为输入前置条件并保留未校验行为。
- 空 Tensor 支持取决于 BS 合轴和 cache mode。空 Tensor、None 与普通零长度轴分别处理。

## 量化场景矩阵

- 以 `(weight_quant_mode, kv_cache_quant_mode, query_quant_mode)` 为主键，逐行保存文档合法组合。
  当前 26.0.0 场景表包含：`(0,0,0)`；部分量化 `(1,0,0)`、`(1,2,0)`、
  `(1,3,0)`；全量化族 `(W,0,0)`、`(W,1,1)`、`(W,3,0)`，其中 `W` 按产品
  分支取 `2/3/4/5`，A2/A3 文档明确排除当前不支持的 fp8/hif8/mxfp8 分支。
  禁止写成不存在的 `(2,2,0)` 或 `(3,3,1)`。每一行还要绑定主体输入、权重、
  cache、scale 的 dtype、format、presence 和输出 dtype。
- 不得把三个 mode 各自的枚举交叉组合。不同 weight quant 模式下 dequant scale、smooth scale、quant scale、query 输出和 cache dtype 都需留在同一 AND 分支。
- `constraints_in_parameters` 必须至少包含一条覆盖三个 mode 的 OR-of-AND 表达式；仅在
  三个参数各自的 `allowed_range_value` 中保存枚举视为提取不完整。
- 非量化基线 `(0,0,0)` 必须绑定：token/三个权重/两个 cache 均为 BF16，三个权重为
  FRACTAL_NZ，其余主体 Tensor 为 ND，所有量化 scale 与 dtype override 不传。
- 输出的五个固定槽在某些场景以 `[0]` 无效 Tensor 占位；保持固定返回数量和条件有效性
  说明。因当前生成器强制每轴 `>0`，不得写 `shape[0]==0`，应标记
  `GENERATOR_GAP:shape_axis_gt_zero`，避免合法默认场景变成 UNSAT。

## 当前版本冲突哨兵

- 参数说明的 `weight_quant_mode` 只列 0/1/2，场景表却使用 3。将 3 保留在对应场景证据中，同时写 `DOC_CONFLICT`；不要擅自删除场景或扩大全局枚举。
- per-tile 场景正文称 `quant_scale_ckv` 必须传入，而 dtype 表相应列出现“无需赋值”。保留两个相反证据并标记冲突，不能将其静默归一为 required 或 None-only。
- `kv_cache_quant_mode` 的“3-表示per-tile”等排版异常、`Dtile` 在非 per-tensor 场景的歧义应标为 `DOC_GAP`/`DOC_CONFLICT`，不要据上下文猜公式。

## original 模式的 TTK 基础运行策略

- `original` 模式允许保留通用生成器的原始用例用于审计，但不得把独立随机得到的 mode、
  dtype、presence、rank、format 笛卡尔积直接交给 TTK 执行。
- 基础跑通阶段由 TTK 适配层将执行副本投影到文档非量化基线：
  `PA_BSND + (weight,kv,query)=(0,0,0) + BF16 + 三个权重 FRACTAL_NZ`。
- 原始 `cases_<platform>.json` 不覆盖；执行副本使用
  `cases_<platform>_ttk_materialized.json`，并输出 `ttk_materialization_report.json`。
- 该投影只是功能冒烟基线，不宣称量化场景覆盖率；后续扩展应以完整场景 profile 为单位。
