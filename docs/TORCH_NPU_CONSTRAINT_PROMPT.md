# torch_npu 约束提取提示词：语料审计与设计说明

## 结论

项目现在具有一条与 ACLNN 完全隔离的 torch_npu 提示词链：

```text
torch_npu 算子文档
  -> prompts/torch_npu_constraints_extract_vN.md
  -> knowledge/torch_npu/common/documentation_conventions.md（始终加载）
  -> knowledge/torch_npu/operator_patterns/*.md（按文档触发）
  -> run/inputs/prompt_v1.md（不可变完整快照）
  -> 当前 OperatorRule constraints.json
```

`scripts/select_torch_npu_prompt.py` 只扫描 `knowledge/torch_npu/**`；ACLNN 的
`scripts/select_prompt.py` 只扫描 ACLNN 基线和 `prompts/modules/**`。运行时的 auto、
`--operator-family hs` 和 `--operator-family torch_npu` 都会选择前一条链；ACLNN 选择
后一条链。显式 `--prompt` 保持原样复制，是可复现/排障逃生口。

运行范围也显式隔离：六个已有 TTK adapter 的重点算子在 auto 模式走完整 TTK；其余
torch_npu API 自动使用 `run_scope=constraints_only`，在约束 normalize/validate 后结束，
不会误入只支持六个算子的生成器。5 个 `torch.npu.*` 管理接口与 `torch_npu.*` 一样
执行 HS 约束门禁，但 opaque/Dict 返回按 schema gap 保守落库。

## 全量语料范围

扫描目录：`op-plugin-26.0.0/docs/zh/custom_APIs/torch_npu`。

- Markdown 共 165 篇。
- `torch_npu.md` 只有入口标题；`torch_npu_list.md` 是 HTML 索引。
- 实际 API 文档 163 篇：非 beta 99 篇、beta 64 篇。
- 163 篇都有产品、功能、原型和参数段；127 篇有返回值，112 篇有独立约束段，155 篇
  有示例。
- 非 beta 的返回/约束/示例覆盖为 98/95/99；beta 仅为 29/17/56。
- 51 篇没有“约束说明”，其中至少 28 篇仍在参数段包含“仅支持、必须、范围、不能、
  保持一致”等硬限制。因此提取器必须阅读全文，不能以章节存在性决定是否提约束。
- 9 篇含 HTML 表，23 篇含锚点；Attention、MLA、MoE 的关键场景组合常在 HTML 合并
  单元格内。
- `torch_npu_list.md` 不是可靠全集，至少漏列 3 个实际文档；批量处理必须遍历目录。
  目录批次会遍历 Markdown，但明确排除这两个导航页，只为 163 篇 API 建 run。

## 文档形态

### 旧 beta 短文档

常有停止维护/替代接口 NOTICE，参数解释简短，缺 dtype、shape、返回值或约束段。提示词
要求把缺失记为 `DOC_GAP`，不能从替代算子或同名 ACLNN API 补全。

### 现代标准文档

一般按产品、功能/公式、函数原型、参数、返回、约束、示例组织。硬规则分散在参数段、
NOTE、产品分节和返回段。参数句式经常同时包含必选性、连续性、format、dtype、shape、
范围和条件，不能只截取第一句。

### 大型条件矩阵文档

FIA/MLA 文档交叉使用 Markdown 表、HTML table、`<term>`、锚点和 `<br>`。正确的建模
单位是“场景行”，不是每一列的独立枚举。量化模式、dtype、scale presence 和输出 dtype
必须保持行内相关性。

### 解析变体

- H1 可能是 `torch_npu.npu_x`、`torch_npu-npu_x` 或转义下划线；真实 callable 取原型。
- 原型有 `torch_npu.*`、`torch.npu.*` 和裸 `npu_*` 三类；153/5/5 篇。
- 55 篇使用 keyword-only `*`，但只有 36 篇在参数段解释它。
- 110 篇有默认值；`2^63-1` 是数学写法，不能按 Python XOR 执行。
- shape 混用方括号、圆括号和 LaTeX；`ND` 也可能写作 `$ND$`。
- layout 名中的下划线可能表示 Q/KV 不同布局，也可能表示输入输出转换，不可机械拆分。
- fenced code 内可能出现 `##` 注释，解析章节时必须忽略代码块内伪标题。

## 类型和约束分布

参数段可识别的主要 API 类型约 1324 项：Tensor 680、int 293、float 77、bool 69、
List[int] 58、str 56、Scalar 12、string 12、List[Tensor] 11；另有 TensorList、
ScalarType、torch.dtype、torch.device、Stream、Generator、Dict 和联合类型。

API 类型与底层 dtype 必须分开。例如 Python 参数是 `int`，正文“数据类型 int64”不应
把 API 类型替换成 int64 Tensor。常见 Tensor dtype 覆盖包括 fp16、fp32、bf16、
int32、int64、bool、int8、int4、uint8。常见 storage format/layout 包括 ND、NZ、
BSND、TND、BNSD、FRACTAL_NZ、PA_BSND、BSH、PA_NZ；logical layout 与 storage
format 在 JSON 中必须分开。

泛化约束模式包括：

- dtype 相等/组合、shape 相等/派生、layout 条件分支；
- actual-seq 前缀和、容器长度和逐元素关系；
- 整除、倍数、对齐、非连续 Tensor；
- reserved/default-only、空 Tensor、固定占位返回；
- 原地更新、用户保证/不校验、未定义行为；
- 产品/版本限定、训练/推理、图/单算子模式。

“不校验、用户保证”仍是用例生成的硬前置条件；“建议、推荐、性能更优、可能超时”是
软信息，不能成为生成器硬约束。

## 提示词结构

### 基线

`prompts/torch_npu_constraints_extract_v1.md` 负责：

- 当前 11 字段 `OperatorRule` 契约和平台嵌套；
- 原型顺序、required/optional、keyword-only 与默认值处理；
- Python/Tensor/List 类型映射；
- dtype、storage format、logical layout、rank 和 shape 轴关系；
- 场景 OR-of-AND、条件 presence、固定 tuple 输出和空 Tensor；
- 当前表达式语法白名单及门禁限制；
- 文档冲突、缺失和 schema 缺口留痕；
- 输出前的结构/语义自检。

### 始终加载的通用知识

`knowledge/torch_npu/common/documentation_conventions.md` 固化 26.0.0 的章节、语言强度、
类型、列表、场景和保守提取约定。

### 按需家族知识

- `attention_family.md`
- `quantization.md`
- `collections_and_grouped_ops.md`
- `inplace_and_stateful_ops.md`
- `matrix_product_family.md`
- `distributed_collectives.md`
- `indexed_access_and_update.md`
- `normalization_family.md`
- `selection_reduction_sampling.md`

它们通过 operator name、文档内容或文件名触发，只提供审校规则，不允许凭家族常识补写
当前文档没有的规格。对 163 篇 API 的 selector 审计中，上述模块分别覆盖 Attention 23、
量化 44、集合/分组 74、原地/状态 17、矩阵乘 21、分布式通信 13、索引更新 10、
Normalization 10、选择/归约/采样 12 篇；模块可叠加，46 篇不需要家族知识而只加载
通用基线。覆盖数不是约束来源，具体事实仍由当前文档确认。

### 六个精确算子知识

以下模块只在 callable 完全相等时加载：

- `npu_kv_quant_sparse_flash_attention.md`
- `npu_sparse_flash_attention.md`
- `npu_lightning_indexer.md`
- `npu_quant_lightning_indexer.md`
- `npu_mla_prolog_v3.md`
- `npu_fused_infer_attention_score.md`

专项模块保存 26.0.0 的“必须反查项”和已知冲突哨兵。所有数字仍必须由当前输入文档
确认；版本变化时输入文档优先。

## 六个重点算子的主要风险

| 算子 | 必须保持的场景关联 | 当前文档冲突/缺口 |
|---|---|---|
| KV Quant SFA | Q/KV layout、TND/PA presence、packed D、量化 mode、稀疏 block | `attention_mode=0` 与仅支持 2；value/output 维度文字与示例冲突；无效索引哨兵未定义 |
| Sparse FA | BSND/TND/PA、rope、actual seq、return lse 条件 | `attention_mode=0` 与仅支持 2；固定三输出不能缩 tuple；T1/Q_T 符号不一 |
| Lightning Indexer | weights dtype/shape、TND/PA、return value 条件 | sparse_count 是非连续集合；false 时第二返回槽的具体占位语义不充分 |
| Quant Lightning Indexer | int8 Q/K、dequant scale layout、两个 mode 联合 | mode 原型 required、正文称 optional；逐元素 fp16 乘积范围难以结构化 |
| MLA Prolog v3 | cache mode、空 Tensor、原地 cache、三 mode 量化矩阵 | 原型无 `*` 但模板称 keyword-only；mode 3 枚举缺失；quant_scale_ckv 必传/无需赋值冲突 |
| FIA | Q_S 分支、layout、Tensor/TensorList、PA/prefix/rope/padding、量化矩阵 | query 首段 dtype 与 int8 场景冲突；大量条件不可合并；存在参数名笔误 |

## 当前 OperatorRule 的适配边界

当前结构足以表达：参数/输出卡片、平台域、dtype/format/rank、静态值域、可解析的跨参数
表达式，以及大部分条件 presence/shape/dtype 关系。因此 v1 不修改 schema，优先保持
与现有生成器和校验器兼容。

以下信息不能无损落入当前结构或生成器，已通过 `DOC_CONFLICT`、`DOC_GAP`、
`SCHEMA_GAP`、`GENERATOR_GAP`、`SCHEMA_FALLBACK` 标记保存在 description/src_text：

1. **调用语义**：没有独立的 `default_value`、`keyword_only`、`signature_required`、
   `semantic_required`、`required_when` 字段。
2. **场景对象**：没有可命名的 scenario/guard/scope，复杂矩阵只能压成扁平 OR-of-AND
   表达式，难以携带每行证据和产品/版本范围。
3. **冲突与置信度**：没有 conflict/ambiguity/warning/evidence span；只能把两边原文
   塞进描述，机器无法可靠阻断或要求人工裁决。
4. **容器元素约束**：TensorList/List[int] 缺少元素 schema、动态长度、前缀和、单调性
   和“每个元素”谓词；当前门禁又禁止无界 `all/any`。
5. **返回有效性**：无法区分固定 tuple 槽、条件有效输出、空/零占位和真正省略输出。
6. **副作用**：没有 alias/mutation/read-write set，无法表示 cache 原地更新。
7. **类型与布局**：联合类型、Tensor 或 TensorList、逻辑 dtype/物理承载 dtype，以及
   logical layout 与 storage format 缺少独立结构。
8. **行为分类**：reserved、ignored、unsupported、invalid、undefined、not-validated、
   user-guaranteed 和性能建议没有机器字段。
9. **表达式能力**：逐元素乘积范围、动态序列聚合、ceil/divisibility 等内容约束无法在
   当前安全 DSL 中完整表达；`allowed_range_value.type=range` 还被生成器固定解释为
   双边开区间，缺少开闭边界元数据，闭区间必须退回显式关系。
10. **零长度 Tensor**：当前 `TensorVar` 对每个 shape 轴硬编码 `>0`，无法生成或求解
    文档明确支持的 zero-extent 输入以及 `[0]` 占位输出；直接写 `shape[i]==0` 会使合法
    场景 UNSAT。v1 只留 `GENERATOR_GAP`，不修改生成器核心。
11. **长度表示不一致**：validator 接受扁平 `[min,max]`，但生成器把它当两个离散值；
    torch_npu prompt 统一使用嵌套 `[[min,max]]` 避免漏采中间长度。
12. **版本门槛**：产品字段不能单独表示 CANN/HDK/Extension/包版本范围。

当前 prompt 已对 `List[Tensor]`、混合 tuple、名称式 tuple、opaque/Dict 返回和缺少
`->` 的原型规定保守映射；其中 opaque object 的内部结构仍属于 schema/generator 缺口。

后续若扩展 schema，建议先新增可选字段而不是改变现有字段含义：`call_semantics`、
`scenarios`、`evidence`、`conflicts`、`container_schema`、`output_semantics`、
`side_effects`、`logical_layout`、`behavior`、`version_support`。在生成器、normalize、
validator 和 TTK adapter 全部支持前，这些字段不能提前写入生产 `constraints.json`。

## 验证方式

1. 对 163 篇 API 文档运行 selector 分类，确保 frontmatter 和触发器均可解析。
2. 对六个重点文档核对加载模块，保证只加载自己的精确算子模块。
3. 对 ACLNN 文档核对仍只走 ACLNN selector。
4. 运行 runtime/selector/normalize/constraint validation 相关测试。
5. 真正评估准确率时，分别选择短 beta、普通标准、大型 HTML 矩阵及六个重点算子，
   对 constraints.json 做字段级人工 gold diff；不能仅以 JSON 通过 schema 校验作为准确。
