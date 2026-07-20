# 约束提取提示词变更记录（CHANGELOG）

> 本文件原为 `operator_constraints_extract_v3.md` 附录 B，已移出活跃提示词。
> 提取约束时**无需加载**本文件；仅供维护者追溯 v1→v2→v3 的规则演进。
> 章节编号引用以移出时的 v3 为准；后续活跃提示词若重编号，需人工对照。

## 附录 B：从 v1 升级到 v3 的注意事项

- 本 v1 的 `inputs`/`outputs` 二级 key 体系是 `平台名`；每参数每平台一条 `ParamAttributes`；平台差异通过多条记录体现。
- `expr_type` 为自由 `str`，§7 仅作参考；若新增语义（如 `shape_value_enum`），追加到 §7.2 并附真实算子样例。
- 若增加新平台（昇腾下一代硬件），在 §5.1 字典中追加官方字符串。
- 若未来 schema 要求 `ValueWithSrcText` 包裹更多字段（如 `description`），同步更新 §3 与 §4.6.3。
- **v2 新增**：§4.6.5 NZ 格式块尺寸硬约束（覆盖所有 NZ / FRACTAL_NZ / FRACTAL_NZ_C0_16 算子）；
  §4.6.4 D 新增 NZ 块尺寸常量识别；§4.6.3 allowed_range 映射表新增 NZ 块尺寸行；
  §4.7.3 新增 NZ 落库铁律；§6.3 新增模式 5（NZ 块尺寸硬约束模板）；
  §8 新增三条 NZ 相关边缘场景；§9 新增第 17 项自检（NZ 块尺寸硬约束）。
  §10 调用模板引用 §4.6.5 与模式 5。
- v2 **不**改变 `OperatorRule` schema 字段；所有新增规则均为已有字段的更精细约束。

### B+：v2 → v2-iter-2026-07-04 变更记录（基于 `aclnnAlltoAllMatmul` 实战）

下列变更在不变 schema、不破坏现有算子的前提下，强化了"全平台覆盖"与"bool 参数
枚举化"两条关键防线，避免在多个支持的平台文档（尤其是表格化文档）中只填写单平台条目：

1. **§4.6.2 二级 key 规则重写**：删除"约束完全一致时用单个平台名"的措辞，
   改为强制逐平台复制 `ParamAttributes`（即便内容相同）。
2. **§4.6.3 bool 类型参数子节重写**：所有 bool 参数（包括无明确固定值约束的）必须产出
   `type="enum"` 条目；取值按文档限定选择 `[false]` / `[true]` / `[false, true]`。
   删除"`no_constraint.md` 中 bool 参数不产出"的旧规则。
3. **§8 边缘场景新增两行**：
   - "bool 参数（无固定值约束）" → enum `[false, true]`；
   - "`product_support` 含 ≥2 平台但某非隐式参数只产出 1 平台条目" → 漏抽，逐平台复制。
4. **§9.3 平台字典一致**：强化"逐平台复制"检查，新增"常见错误模式"反例说明。
5. **§9 自检清单新增第 14 项**：bool 参数 `allowed_range_value` 必须 enum，value 为
   `[false]` / `[true]` / `[false, true]`；自检总数从 12 → 14 → 15（含原 v2 §9.13
   的 NZ 块尺寸硬约束）。

**触发背景**：`aclnnAlltoAllMatmul` 闭环测试发现 v1 提示词产出 `iter_001` 时，
11 个非隐式参数中只有 2 个有完整的三平台条目，导致 A3/A2 平台用例无法生成，且
`transposeX2` 因 `allowed_range_value.value=[]` + `type=range` 被生成器错误填充为浮点。
`iter_002` 通过 prompt_v2（含本节所有变更）将这两个问题一次修复（用例从 0/10 → 9/30）。
本节变更同步到主提示词，使其它算子在首轮 EXTRACT 阶段即可避免同样根因。

### B++：v2 → v3 变更记录（一段式算子支持）

下列变更新增对**一段式算子**（无 `GetWorkspaceSize`，如 `aclnnCalculateMatmulWeightSize`）的解析支持，移植自参考项目 `operator-agent-wsl` 的 `get_primary_function_names` 机制（一段式判定由 `function_signature` 是否含 `GetWorkspaceSize` 隐式表达，不落盘为独立字段）：

1. **§3 schema**：一段式判定由 `function_signature` 是否含 `GetWorkspaceSize` 隐式表达，**不**新增独立字段（`is_single_function_mode` 不落盘）。
2. **§4.4 `function_signature` 拆两段式 / 一段式分支**：一段式取唯一函数声明（无 `GetWorkspaceSize` 后缀），**不得**写入 `is_single_function_mode` 字段。
3. **§4.6.1 流程参数排除补一段式例外**：标量指针输出（`uint64_t*`/`int64_t*` 等）进 `outputs`，不当框架参数排除。
4. **§4.6.3 aclIntArray 参数 dtype 固定为 int**：文档"数据类型"列给张量 dtype 时描述的是关联张量，`dtype.value` 固定 `["int"]`，不写入 `dtype` 或 `allowed_range_value`。
5. **§6.3 模式 4 补一段式 rank 区间样例**："支持 N-M 维"→ `len(param.shape)`，禁 `shape[0]`（对应 `weightNz-fix` 缺陷②）。
6. **§8 边缘场景新增三行**：一段式 function_signature、标量指针输出、aclIntArray dtype 固定为 int。
7. **§9 自检新增第 16 项**：一段式一致性（function_signature 不含 GetWorkspaceSize + 标量指针输出在 outputs）。
8. **附录 A 修正 `aclnnCalculateMatmulWeightSize` 注解**：原误注"workspaceSize/executor 是唯一输出"改为正确参数描述。
9. **§1.2 输入补一段式说明**。

**范围说明**：v3 仅支持一段式**解析/约束提取**。执行层（`generator.py` 标量指针输出识别、签名表、ATK 单函数调用、CPU golden）仍为两段式，不在 v3 范围。

### B+++：v3 增补记录（大小/数量语义参数的隐式 >0 约束）

下列变更新增对**大小/数量语义参数**的隐式 >0 约束提取规则，来自
大小/数量语义参数闭环：输出参数（如 `uint64_t*` 标量指针）的 description 含
"元素的数据量"/"空间大小"等短语，语义上必然 > 0，但文档未显式写"大于0"。
v3 无规则要求提取这种隐式 >0 约束，导致 `constraints_in_parameters` 漏掉该约束。
本规则按 description 语义短语触发，不按算子名硬编码：

1. **§4.6.9 新增**：隐式 >0 约束（大小/数量语义参数）——适用判定（触发短语清单 +
   标量取值参数前提 + 文档未显式给值域）、不适用场景（shape/dtype/format/枚举/bool/
   数组参数禁止套用）、必须产出的 `constraints_in_parameters` 条目（`expr: P.range_value > 0`、
   `expr_type=value_dependency`、`allowed_range_value.value=[]`）+ 6 条规则要点。
2. **§4.7.3 新增第 10 项**：大小/数量语义参数的隐式 >0 约束提取规则。
3. **§6.3 模式 4 补注**：隐式 >0 约束模板（`P.range_value > 0`）及 §4.6.9 引用。
4. **§8 边缘场景新增一行**：description 含"空间大小/数据量/元素个数/数量"的处理。
5. **§9 自检新增第 25 项**：大小/数量语义参数的隐式 >0 约束自检（5 个子项 a-e）。
6. **§10 调用模板更新**：知识库引用追加 §4.6.9；
   自检项数 19 → 25，补引 §9.25。

**范围说明**：本次增补不改变 `OperatorRule` schema 字段；所有新增规则均为已有字段的
更精细约束。规则按 description 语义短语触发，通用适用于所有含"大小/数量/个数"
语义参数的算子，不限于特定算子系列。

### B++++：v3 合并 v4 增补记录（shape_value_dependency 隐式 bool 强制门控 + is_optional 判定）

下列变更来自原 `operator_constraints_extract_v4.md` 增量补丁，现已回合并到 v3 主提示词；
后续不再保留独立 v4 文件。

**触发背景**：`aclnnBatchMatMulWeightNz` iter_001 闭环测试发现 v3 提示词产出时，
4 条 `shape_value_dependency`（编号 A/B/C/D）为无条件 expr，使 Z3 在
`mat2_transposed=True` 下 UNSAT，导致该分支覆盖率为 0、`self_transposed=True`
仅 12/300 = 4%。另一次 `aclnnSwinAttentionScoreQuant` 提取发现，参数名带
`Optional` 的必填入参被误判为可选参数。

**变更清单**：

1. **§4.6.3 D+ 新增**：`shape_value_dependency` 必须按 §4.6.5 B.1 隐式 bool
   门控分支，含隐式 bool 与轴位对应表、`out.shape` 门控说明、`relation_params` 与
   `src_text` 要求。
2. **§4.6.5 B.1 第 6 点新增**：对 `aclnnBatchMatMulWeightNz`，凡
   `shape_value_dependency` 引用 `mat2.shape[j]` 或 `self.shape[i]`，必须按
   `mat2_transposed` / `self_transposed` 分支。
3. **§6.3 模式 6.1 新增**：`shape_value_dependency` 弱门控模板，含 mat2 引用 if/else
   模板、等价 `not(...)/or` 多条约束写法、self 引用模板、典型反例与 `src_text` 要求。
4. **§8 边缘场景新增一行**：无条件 `shape_value_dependency` 改写为模式 6.1。
5. **§9.18 子项 f 新增**：`shape_value_dependency` 门控完整性自检。
6. **§10 调用模板更新**：知识库引用追加 §4.6.3 D+ 与 §6.3 模式 6.1。
7. **附录 A 更新**：`aclnnBatchMatMulWeightNz` 注解追加
   `shape_value_dependency` 必须按隐式 bool 分支。
8. **§4.6.3 `is_optional` 判定增补**：可选性必须依据"输入/输出/可选输入"等
   文档分类或正文显式可选语义，禁止按参数名中的 `Optional` 等字样推断；
   `"当前仅支持输入nullptr"` 应作为取值约束，不作为可选性证据。

**典型反例 → 修复案例**：

```text
# 错误：无条件 shape_value_dependency
expr = "((self.shape[2] + 15) // 16 == mat2.shape[2])"
relation_params = ["self", "mat2"]

# 正确：按 mat2_transposed 分支
expr = "((self.shape[2] + 15) // 16 == mat2.shape[2])
        if (mat2_transposed.range_value == False)
        else ((self.shape[2] + 15) // 16 == mat2.shape[1])
        if (mat2_transposed.range_value == True)
        else True"
relation_params = ["self", "mat2", "mat2_transposed"]
```

### B+++++：v3 增补记录（公共互推导关系 / broadcast 关系）

下列变更新增对官方公共文档 `互推导关系.md` 与 `broadcast关系.md` 的基础知识引用和
形式化约束提取要求，来自 `aclnnBatchMatMulWeightNz` NPU 参数校验失败闭环：

- `self` 最后一维与 `mat2` 逻辑 Reduce 维不一致，CPU golden 可执行但 NPU 报
  `self's last dim and mat2's penultimate dim should be same`；
- `self.dtype=FLOAT16`、`out.dtype=BFLOAT16`，CPU 计算可绕过但 NPU 报
  `self's dtype and out's dtype are not equal`。根因是文档引用公共互推导关系、
  broadcast 关系，但提示词没有把外部公共知识展开成硬约束。

**变更清单**：

1. 新增 `knowledge/common/type_promotion.md`，记录 CANN 互推导关系表及输出 dtype
   绑定要求。
2. 新增 `knowledge/common/broadcast.md`，记录 broadcast 右对齐、维度为 1 可拉伸、
   特殊 dtype 轴合并限制及输出 broadcast 结果约束。
3. **§4.6.10 新增**：外部公共知识引用展开规则，要求把
   `互推导关系.md` / `broadcast关系.md` 链接转为 `type_dependency`、
   `shape_broadcast`、`shape_value_dependency`。
4. **§4.7.3 新增第 11/12 点**：公共知识引用必须展开；MatMul Reduce 维度相等必须
   按真实布局落库。
5. **§8 边缘场景新增三行**：互推导、broadcast、MatMul Reduce 维度相等。
6. **§9 自检新增第 26 项**：公共互推导 / broadcast 知识展开自检。
7. **§10 调用模板更新**：知识库引用追加 §4.6.10 与 `knowledge/common/*`。

---

### B++++++：v3 增补记录（产品相关参数取值范围差异）

下列变更新增"同一参数在不同产品下候选值不同"的逐平台 `allowed_range_value`
提取要求，来自 `aclnnNpuFormatCast` 闭环：

- `additionalDtype` 在"参数说明"总表统一列出 `ACL_FLOAT16(1)、ACL_BF16(27)、
  INT8(2)、ACL_FLOAT8_E4M3FN(36)`（即 `1/27/2/36`），但在
  `Atlas A3 训练系列产品/Atlas A3 推理系列产品`、
  `Atlas A2 训练系列产品/Atlas A2 推理系列产品` 的"约束说明"分节与调用示例中
  固定为 `-1`（C0 改由 `srcTensor` 基础类型计算，示例代码 `int additionalDtype = -1;`）；
- v3 无规则要求按产品分别识别这种候选值分歧，提取器把总表 `{1,27,2,36}` 套用
  到所有平台，A3/A2 生成器采样出文档示例不支持的 `additionalDtype` 值。

**变更清单**：

1. **§4.6.11 新增**：产品相关参数取值范围差异规则，按"参数候选值随产品分歧"
   语义触发（总表候选 vs 产品分节/示例候选不一致、或文档显式标注产品相关差异），
   不按算子名硬编码；逐平台产出 `allowed_range_value`，占位产品为单元素列表
   （如 `[-1]`），不得追加总表候选、不得留空 `[]`。
2. **§8 边缘场景新增一行**：产品分节给出同一参数不同候选值 / 固定占位值的处理。
3. **§9 自检新增第 27 项**：产品相关参数取值范围差异自检，含
   `aclnnNpuFormatCast` `additionalDtype` 的逐平台期望值（A3/A2=`[-1]`，
   Atlas 350=`[1,27,2,36]`）。

---

### B+++++++：v3 增补记录（空 `expr` 禁令；`derived_value` 须可求解）

下列变更来自 `aclnnNpuFormatCast` 闭环复盘，贯彻"表达式为空就不提取"原则，同时
纠正早期"空 `expr` 的 `derived_value` 条目"与后续"完全废止 `derived_value`"两种
极端：

- iter_001 提取产物中有一条 `expr_type=derived_value`、`expr=""`、
  `relation_params=["dstTensor","srcTensor","dstFormat","additionalDtype"]` 的记录；
  空 `expr` 无法在生成期 `eval()`，生成器无法读出派生规则，对 `[DERIVED]` 输出参数
  独立随机赋值，导致 86/100 条 A3 用例 dstTensor.format/actualFormat 与期望不一致；
- `[DERIVED]` description 文本标记亦不足以单独约束生成器（生成器未识别该标记）；
- 文档 `dtype_support_description` 实际含 (srcTensor.dtype × dstFormat × additionalDtype
  → actualFormat) 的确定映射，可编码为可求解 expr（A3/A2 为恒等映射
  `actualFormat == dstFormat`），不应留空。

**变更清单**（"存在确定映射时必须产出可求解 expr；无映射时不产出空壳"）：

1. **§4.6.8 C/D/E 改写**：派生张量在文档存在确定映射时**必须**产出 `derived_value`
   条目，`expr` 编码映射为可 `eval()` 的布尔表达式（恒等映射用等式、查找表用析取、
   格式派生用 actualFormat→format 析取）；无确定映射时不产出条目，由 `[DERIVED]`
   description 承载；`format_rank_consistency` 守卫仍须落库。
2. **§4.7.2 字段表**：`expr` 字段"不得为空字符串"；无法形式化时不产出条目，
   改记入 `description`/`src_text`。
3. **§4.6.10 B.4**：broadcast 特殊 dtype 无法形式化时改记入
   `description`/`src_text`，不得产出空 `expr` 条目。
4. **§6.1 第 10 条**：整条约束无法形式化时不产出空 `expr` 条目，改记入
   `description`/`src_text`。
5. **§8 边缘场景**：自然语言公式行由"`expr=""`"改为"不产出条目，记入
   `description`/`src_text`"；新增派生值可求解与格式转换 dtype 等式两行。
6. **§9 自检第 28 项**：空 `expr` 禁令与 `derived_value` 可求解性自检
   （`derived_value` 允许存在但 `expr` 必须可求解、不得为空）；§9 开头计数 27→28→29。
7. **§4.6.11 D 反例**：引用 §4.6.8 B/C.1（`derived_value` 的 `relation_params`
   含 `additionalDtype`），删除参数会破坏派生约束一致性。
8. **§6.3 新增模式 9**：派生值可求解查找表达式模板（恒等/查找表/格式派生）。
9. **§7.1 登记 `derived_value`**：参数间约束字典补登 `derived_value` 行。

---

### B++++++++：v3 增补记录（格式转换算子 dtype 等式约束）

下列变更新增对**格式转换 / 布局变换**类算子的 `srcTensor.dtype == dstTensor.dtype`
跨参等式约束提取要求，来自 `aclnnNpuFormatCast` 闭环：

- iter_001 `constraints_in_parameters` 三平台均未提取 `srcTensor.dtype == dstTensor.dtype`
  跨参等式，300/300 条用例 src.dtype != dst.dtype（350: int32→int8；A3: uint8→int8；
  A2: uint8→int8），且 dstTensor.range_values 按 int8 负值域 [-255,-1] 生成；
- 文档 GetWorkspaceSize 表（doc:444-450）每行 src dtype == dst dtype；功能说明
  （doc:23-25）为纯格式转换（数据值不变）；示例代码用 srcDtype 构造 dstTensor；
  cases_executor.py 注释 "data values are preserved"。

**变更清单**（按算子语义与文档 dtype 表触发，不按算子名硬编码）：

1. **§4.6.12 新增**：格式转换算子 dtype 等式约束——适用判定（格式转换语义 +
   dtype 表每行 src==dst）、不适用场景（dtype 转换类算子）、必须产出的
   `type_equality` 条目（`srcTensor.dtype == dstTensor.dtype`）+ 4 条规则要点
   （dstTensor 值域沿用 src、逐平台落库、src_text 可溯源、不替代互推导规则）。
2. **§4.7.3 新增第 14 项**：格式转换算子 dtype 等式必须落库。
3. **§8 边缘场景新增一行**：格式转换算子 dtype 表每行 src.dtype == dst.dtype 的处理。
4. **§9 自检新增第 29 项**：格式转换算子 dtype 等式自检（4 个子项 a-d）；
   §9 开头计数 28→29。
5. **§10 调用模板更新**：知识库引用追加 §4.6.12。

---

### B+++++++++：v3 增补记录（联合交叉 dtype/format 组合表用 OR-of-ANDs 表达）

下列变更把「dtype 与 format 交叉联合的组合表」从 `dtype_support_description` /
`format_support_description` 迁移到 `constraints_in_parameters` 的 OR-of-ANDs expr，
来自 `aclnnNpuFormatCast` 闭环：

- iter_001 `dtype_support_description`（Atlas 350）把联合表拆成
  `{srcTensor:"INT8", dstFormat:"29", additionalDtype:"2", actualFormat:"29"}`——
  `additionalDtype` 抄了 `ACL_INT8(2)` 的括号码 `2` 而非 dtype 名，`dstFormat`/`actualFormat`
  同样抄数值枚举码，srcTensor 却抄 dtype 名，同行语义不一致；
- `format_support_description`（A3/A2）把 `srcTensor.format × dstFormat` 笛卡尔积、
  令 `actualFormat=dstFormat` 凭空捏造 25 行，与文档联合表无关；
- `constraints_in_parameters` 的 `derived_value.expr=""` 与 `cross_param_constraint.expr=""`
  均为空壳，违反 §4.7.2/§4.6.8 C.1，生成器无法读出派生规则，转而独立随机赋
  `dstTensor.format`/`actualFormat`。

**变更清单**（按组合表形态触发，不按算子名硬编码）：

1. **§4.9 / §4.10 加禁令**：dtype×format 交叉联合组合表（同一行同时含 dtype 列与
   format 列、且 dtype 与 format 存在行内依赖——不同 dtype 对应不同 format 候选、
   拆开会丢失信息）**不得**拆进 `dtype_support_description`/`format_support_description`；
   必须落库为 OR-of-ANDs expr；这两个字段对该算子留 `{}`。**纯 dtype 表**（只有 dtype
   列）仍填 `dtype_support_description`、**纯 format 表**（只有 format 列）仍填
   `format_support_description`、**同表但独立**的 dtype+format 表（任意 dtype 配任意
   format）按"单独 dtype + 单独 format"拆开——三者不属交叉表，不强求 OR-of-ANDs。
2. **§6.3 模式 9 新增「主接口联合组合表」示例**：`GetWorkspaceSize` 类主接口的
   dtype×format 联合表映射到 `dstTensor.dtype`/`dstTensor.format`（而非子接口的
   `actualFormat.range_value`），析取所有合法行；明确该 expr 同时是 `[DERIVED]`
   dstTensor 的派生规则，并与 §4.6.12 `type_equality` 并行不冲突。
3. **§8 边缘场景新增两行**：联合交叉表禁拆解、空 `expr` 空壳条目处置。
4. **§9 自检新增第 30 项**：联合交叉 dtype/format 组合表自检（6 个子项 a-f）；
   §9 开头计数 29→30。
5. **§0/§10 计数同步**：目录与调用模板「29 项」→「30 项」。

**范围说明**：本次增补不改变 `OperatorRule` schema 字段；规则按算子语义与文档 dtype
表触发，通用适用于所有格式转换/布局变换类算子，不限于特定算子系列。

---

### B++++++++++：增补记录（公共知识表内联进 broadcast 模块，删除 knowledge/ 目录）

下列变更把原 `knowledge/common/` 下两份独立参考文件内联进 `prompts/modules/broadcast.md`，
并删除 `knowledge/` 顶级目录。背景：`knowledge/` 除 `common/` 外的 `dimensions/`、
`allowed_range/`、`implicit_params/`、`relation_skills/` 等子路径早在 v1→v3 融合提示词时
已内联、目录不复存在，`common/` 是最后残存的外部参考文件。

**变更清单**：

1. **`prompts/modules/broadcast.md` §A 内联**：dtype 简写映射 + 16×16 互推导表
   （原 `knowledge/common/type_promotion.md`）写入 §A；§A.1「按上表枚举」取代外链。
2. **`prompts/modules/broadcast.md` §B 内联**：3 条广播规则 + 特殊 dtype 轴合并 `<6`
   限制（原 `knowledge/common/broadcast.md`）写入 §B；§B.4 不再外链。
3. **删除 `knowledge/` 目录**：`common/{type_promotion,broadcast}.md` 及上层目录一并删除。
4. **v4 base 路径引用改内联**（仅改路径串，不动 §编号）：§9.26 自检表改「按 §4.6.10
   A/B 的推导表/广播规则生成」；§10 调用模板改「参考 §4.6.10（推导表与广播规则已内联
   于该节）」；附录 C 速查表两行改指向 `prompts/modules/broadcast.md §A/§B`。
5. **B+++++ 历史记录保留不改写**：其「新增 `knowledge/common/*.md`」为当时事实；
   本次内联为后续独立变更，据此新条目追溯。

**范围说明**：不改变 `OperatorRule` schema 字段，不改变约束提取语义；仅把外部参考文件
内联进模块、消除外链脆性。`select_prompt.py` 不受影响（broadcast 模块 frontmatter 未变）。

---

### B+++++++++++：增补记录（§4.6.7 rank 表补全 HWCN/NC/NC1HWC0_C04，§5.3/§B 同步）

下列变更补全 `prompts/modules/format_cast.md` §4.6.7 §A「昇腾格式标准 rank 对应表」
缺失的三种格式，并同步 §5.3 受控字典与 `acl_format_enum.md` §B 的短名，使提取器对
含这些格式的张量能正确落库 `format_rank_consistency` 逐格式守卫。背景：用户提供
ACL_FORMAT↔维度数权威对照表，与 §4.6.7 §A 既有表逐行吻合，但 §A 缺 `HWCN`(4D)、
`NC`(2D)、`NC1HWC0_C04`(5D) 三项；`HWCN`、`NC1HWC0_C04` 亦不在 §5.3 受控字典。

**变更清单**：

1. **`format_cast.md` §4.6.7 §A 补三行**：新增 `NC`(rank 2，`(N,C)` 2D 排布)；
   `NCHW`/`NHWC` 行并入 `HWCN`(rank 4，Height×Width×Channel×Batch，图像处理专用)；
   `NC1HWC0` 行并入 `NC1HWC0_C04`(rank 5，`C0=4` 变体)。
2. **v4 base §5.3 受控字典补短名**：列表新增 `HWCN`、`NC1HWC0_C04`（`NC` 已在）。
3. **`acl_format_enum.md` §B 补行**：新增 `NC1HWC0_C04` ↔ `ACL_FORMAT_NC1HWC0_C04`(12)
   对照（`HWCN` 已在 §B；§A 已含二者整数枚举）。
4. **规则不变**：复用既有 `format_rank_consistency`（§4.6.7 §C）与 §9 自检 #24；
   不新增 `expr_type`、不改 Python 求解器 / 生成器 / 执行器。

**范围说明**：作用域维持 FormatCast 算子（§4.6.7 随 `format_cast` 模块装配），
不泛化为通用规则；不改变 schema 字段。`NC` 仅作 §5.3 短名出现（无整数枚举，同 `NCL`），
不进 `acl_format_enum.md` §B。另：`FRACTAL_NZ_C0_32` 仍缺于 §5.3（既有遗留，本次不动）。

---

### B++++++++++++：增补记录（§5.3 补 FRACTAL_NZ_C0_32；复核 10 格式表，rank 冲突保留现值）

下列变更补全 §5.3 受控字典遗留缺口，并记录对一份 10 格式权威对照表的复核结论。
背景：用户提供 ACL_FORMAT 枚举值 / 维度数 / 维度结构对照表（NCHW / NHWC / ND /
NC1HWC0 / FRACTAL_Z / NCDHW / NDC1HWC0 / FRACTAL_Z_3D / FRACTAL_NZ_C0_16 /
FRACTAL_NZ_C0_32）。

**变更清单**：

1. **v4 base §5.3 受控字典补 `FRACTAL_NZ_C0_32`**：补入 `FRACTAL_NZ_C0_16` 之后
   （`FRACTAL_NZ_C0_32` 原已在 §4.6.7 §A / §B / enum §A，独缺 §5.3，为 B+++++++++++
   遗留缺口，本次补齐）。
2. **10 格式 + 枚举值复核**：全部已存在于 §4.6.7 §A / §B / `acl_format_enum.md` §A /
   §5.3（§5.3 仅缺 `FRACTAL_NZ_C0_32`，本次补）；枚举值 0/1/2/3/4/30/32/33/50/51
   与 `acl_format_enum.md` §A 逐行一致。

**rank 冲突处置（保留现值，不改）**：用户表与 §4.6.7 §A 在三处 rank 矛盾——
`FRACTAL_Z_3D`（§A=8 storage 8D vs 用户=4 `[D*C1*H*W, N1, N0, C0]`）、
`FRACTAL_NZ_C0_16` / `FRACTAL_NZ_C0_32`（§A=5 NZ 族 vs 用户=4）。决定**保留 §A 现值
不动**，用户表的维度数不写入，理由：
- §A 现值来自 `aclnnNpuFormatCast` 闭环（NPU 真机校验拒 `FRACTAL_Z_3D + 6D` 等，
  见 §4.6.7 引言）；
- NZ=5 级联到 §4.6.3（line 454「5D NZ 张量 shape[3]/shape[4]」）、§4.6.5、
  §4.6.7 §C 规则 5、§6.3 模式 5、§9 #17；改 4D 会让 `shape[4]` 越界、断 NZ 块尺寸链；
- 差异疑源于折叠 vs 展开 storage 视角（用户 `[D*C1*H*W, ...]` 折叠前 4 维）；
  `format_rank_consistency` + 块尺寸约束按展开 storage rank 建，不接纳折叠值。

**范围说明**：本次仅改 §5.3 一行；不改 §4.6.7 §A rank、不改 §4.6.3 / §4.6.5 /
§6.3 / §9，不改 Python 求解器 / 生成器 / 执行器。若后续 CANN 文档证实折叠视角需单独
表达，再按「view rank / storage rank 双列」方案复核（本次选项之三，未取）。

---

### B+++++++++++++：校正记录（FRACTAL_Z_3D rank 8→4，取代 B++++++++++++ 的保留-8 决定）

下列变更把 §4.6.7 §A / §C / §E 与 §9 #24 d 中 `FRACTAL_Z_3D` 的 rank 由 8 改为 4。
背景：B++++++++++++ 曾决定对用户表的 FRACTAL_Z_3D=4「保留现值 8 不改」；复核后发现
原值 8 无文档支撑——`aclnnNpuFormatCast.md` 仅给 dstTensor storage shape 区间 `[4,8]`
（line 316/250），从未逐格式说 FRACTAL_Z_3D 为 8 维；§4.6.7 §E srcTensor expr 写
`FRACTAL_Z_3D and len==8` 更与 srcTensor view shape 区间 `[2,6]`（line 95/240）自相
矛盾（8∉[2,6]，该分支永不满足）。用户给出 FRACTAL_Z_3D 实际结构 `[D*C1*H*W, N1, N0, C0]`
= 4D，故改 4。

**变更清单**：

1. **`format_cast.md` §4.6.7 §A**：`FRACTAL_Z_3D` 行 `8 / storage shape 8D` →
   `4 / storage shape 4D（[D*C1*H*W, N1, N0, C0]）`。
2. **`format_cast.md` §4.6.7 §C 模板**：`(T.format == "FRACTAL_Z_3D" and len(T.shape) == 8)` → `== 4`。
3. **`format_cast.md` §4.6.7 §E srcTensor expr**：`== 8` → `== 4`（4∈[2,6]，解原矛盾）。
4. **`format_cast.md` §4.6.7 §E dstTensor expr**：`== 8` → `== 4`（4∈[4,8]）。
5. **v4 base §9 自检 #24 d**：`FRACTAL_Z_3D + 非8D` → `FRACTAL_Z_3D + 非4D`。

**范围说明**：
- **NZ 族不动**：`FRACTAL_NZ_C0_16` / `FRACTAL_NZ_C0_32` 仍 = 5（B++++++++++++ 保留-5
  决定不变；NZ=5 级联到 §4.6.3 / §4.6.5 / §6.3 / §9 #17，改 4 会让 `shape[4]` 越界，
  无用户新指示不改）。
- **v3 base 不动**：`operator_constraints_extract_v3.md` 同款 5 处（§A line 895、§C line 927、
  §E src line 1004、§E dst line 1014、§9 #24 d line 2257）仍为 8；v3 已被 v4 取代、不装配，
  按惯例不动。
- 不改 schema、不改 Python 求解器 / 生成器 / 执行器；`format_rank_consistency` 机制不变。
- 取代 B++++++++++++ 中「FRACTAL_Z_3D 保留 8」的处置；B++++++++++++ 作为历史记录保留不改写。
