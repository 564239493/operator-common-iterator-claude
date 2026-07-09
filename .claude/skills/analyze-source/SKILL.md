---
name: analyze-source
description: 每轮 EXTRACT 后从算子源码快照校验约束类型/范围/表达式,产交叉校验证据与约束补丁建议。仅在源码快照存在时使用。
---

# 算子源码证据分析规范

源码是**EXTRACT 后约束校验的证据源**,不是约束提取主输入。`constraint-extractor`
永不直接读源码;本 skill 产 `source_evidence.json` 落盘,下游 `quality-reviewer`
"只读落盘"。**不参与失败诊断**——迭代中失败仍由 `failure-analyst` 按纯文档证据下根因。

## 触发条件

仅当 `run_state.json` 的 `operator_src_snapshot` 非空时使用。为空则跳过,退回纯文档
驱动(EXTRACT→GENERATE→EXECUTE→GATE,无源码产物)。

## 第一步:确定性提取

用 Bash 执行:
```
python scripts/extract_source_constraints.py \
  --snapshot <operator_src_snapshot> \
  --out <iter-dir>/source_raw.json --only all
```
拿到 `source_raw.json`:`platform_matrix`(按平台 dtype/format/attrs)、`aclnn_interfaces`
(处理一对多,过滤 aclnnInner)、`raw_checks`(`kind` 为 OP_TILING_CHECK / OP_CHECK /
OP_CHECK_IF / CHECK_COND / OP_CHECK_DTYPE_NOT_SUPPORT / OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE /
OP_LOGE; 每条带 `condition`+`error_string`+
`source_location`+所属函数 `owning_function`(限定名 Class::method)+函数内 error_string 线索
`function_errors`; 另带 `callees`(条件里调了且快照内有定义的函数 + def 位置, 作调用链导航)
与 `unresolved_calls`(条件里调了但快照无定义的疑似用户函数, 作调用链完整性信号))。

## 第二步:LLM 判读(业务推理,本 agent 核心)

对 `raw_checks` 做语义判读,产出 `hard_constraints`:

- 把每条 `raw_checks[*].condition` 归类为 `expr_type`,**复用**
  `agent.generators.common_model_definition` 的 `InterConstraintsRuleType` 枚举:
  `shape_value_dependency`(如 `q.shape[-1] <= 1024`)、`value_dependency`、
  `shape_dependency`(如 `len(q.shape) in (3,4)`)、`type_equality`、`shape_equality` 等。
- 产出对齐**当前约束提取提示词 §6** `expr` 语法的 Python 表达式,`relation_params` 列出涉及参数。
- 保留 `constraint_id`、`source_location`、`error_string`、原始 condition 作 `src_text`。
- **过滤非约束性检查**:nullptr 检查、纯日志打印、`GRAPH_SUCCESS` 返回判断不产出硬约束。
- **`OP_LOGE` 项特殊处理**:`kind=OP_LOGE` 仅收 `ACLNN_ERR_PARAM` 族(参数校验日志,非"纯日志"
  降噪范围),但 `condition` 为空——其触发条件在外层 `if`(提取器不解析控制流,见
  `extract_source_constraints.py`)。判读方式:(a) 优先作 `function_errors` 线索,结合 `owning_function`
  (多为 L0 impl 的 `Check*` 函数,如 `CheckTransDataSupport`/`CheckFormatShapeMatch`)理解该函数校验的
  参数语义;(b) 若需精确 `expr`,按 `source_location` Read 源码恢复外层 `if` 条件(如
  `transdata.cpp:227` `CheckPrimaryFormatValid` 的 "TransData not support: %s -> %s" 对应 kSupportMap
  格式支持矩阵,需读函数体抽 srcFormat/dstFormat 支持对);(c) `OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE`
  的 `condition` 非空(`retAicore != ACLNN_SUCCESS`),多为 launch/返回码检查,通常不产参数硬约束,
  归"状态传播"类过滤,除非其 `error_string` 暴露真实参数前提。
- **expr 必须吞并分支前提**:若一条 OP_CHECK 位于某验证函数内(由 `owning_function`/`function_errors`/`error_string` 的 "when / 仅当 / only support ... when" 子句给出调用分支前提),必须像 HC-003 那样把前提展进 `expr`(如 `(前提) or (OP_CHECK 本体)`),而不只写进 `src_text`。正例 HC-003:`expr` 含 `(srcTensor.dtype=='FLOAT16' and additionalDtype.range_value==1) or (srcTensor.dtype=='BFLOAT16' and additionalDtype.range_value==27)` 前提 + `dstFormat==29` + `shape[-2]==1`。反例 HC-006:`expr` 无条件 `dstFormat.range_value==29`,丢了 `additionalDtype != srcDtype` 前提(该 OP_CHECK 在 `ValidateWeightQuantMatmulParams` 内,仅 WeightQuant 分支生效)。若前提无法并进 `expr`(如纯平台/调用上下文),必须在 `cross_check` 检查文档侧对应约束是否带同前提,否则报 `mismatch_overbroad`(文档过宽会放行源码拒绝的用例)。

## 第三步:校验域(类型/范围/表达式)——每轮 EXTRACT 后一次

### OP_TILING_CHECK 判读(尤其维度取值与参数间关系)

`raw_checks` 中 `kind=OP_TILING_CHECK` 的条目语义与 OP_CHECK 一致(cond 真→报错),但混有
多类检查,必须按下表分类处置。OP_TILING_CHECK 的真实约束常在被调用 helper 体内
(如 `OP_TILING_CHECK(!CheckParamsShape(), ...)` 的真值在 `CheckParamsShape`),故判读必跟
`callees`。

| 类别 | 识别特征 | 处置 | expr_type |
| ---- | -------- | ---- | --------- |
| **维度取值**(保留) | `shape[i]`/`shape[-1]`、`dim`/`axis`、`shape.size()`、size 常量、`shape` 元素数边界 | 产硬约束,expr 对齐 §6(`param.shape[i]`、`param.range_value`、`len(x.shape)`) | `shape_value_dependency`/`value_dependency` |
| **参数间关系**(保留) | `a.shape==b.shape`、`a.dim()==b.dim()`、`src1DimNum != src2DimNum`、跨参等/不等 | 产硬约束 | `shape_equality`/`type_equality`/`shape_value_dependency` |
| **委派型**(必跟 callee) | `!CheckParamsShape()`、`!CheckAttr()`、`GetMMTilingData() != GRAPH_SUCCESS` 等——cond 本身是函数调用 | 本条不产约束;读 `callees[].source_location` 跟进被调用函数体,把体内维度/关系检查展成 `hard_constraints`(再按上两类分类) | 同被调用函数体内检查 |
| **状态传播**(过滤) | `ret == -1`、`!= GRAPH_SUCCESS`、`op_ret != 0` | 不产硬约束(内部 mm/接口返回值传播) | — |
| **纯资源**(过滤) | `aicCoreNum <= 0`、`aivCoreNum <= 0`、`ubSize <= ...`、`totalCore < 0` | 不产硬约束(硬件资源,非用户输入约束) | — |

**框架内部检查过滤**:来自共享框架头(`_closure/common/inc/op_host/tiling_*.h`、
`tiling_templates_registry.h` 等)的 `OP_CHECK_IF` 常是 registry/map 内部状态检查
(`cases_`/`registryMap_`/`registry_map_`、`.end()` 迭代器、`nullptr` 注册检查、
`find(...) != ...end()`),不绑定算子输入参数 → 过滤,不产硬约束。判据:条件引用的是
框架内部容器/迭代器而非算子参数(`shape`/`dtype`/`range_value`/`dim`)。注意区分:
共享头里也有**真实**约束(如 `norm_tiling_check_common.h` 的 `src1DimNum != src2DimNum`),
这类绑定算子参数的必须保留。

### 变量绑定脚手架(先于分类判读, 必须消费)

`raw_checks[*]` 若带以下任一字段(extractor 确定性产物, 仅非空出现), 判读**必须先消费**,
不得仅凭 `condition` 字面串或 `error_string` 文本下结论:

- `resolved_constants`: `{token: 字面值}`——把 `condition` 里的常量 token(如 `C0_8`/`C0_16`/`C0_32`)
  替换为字面值(8/16/32)再判读语义, 而非从 `error_string` 人话反推。
- `var_bindings`: 条件里局部变量的赋值回溯, 每项 `{token, rhs, rhs_local_vars, source_location}`。
  沿 `rhs_local_vars` 链式追溯(如 `axisC0 → rhs=(targetC0>0)?targetC0:c0Size,
  rhs_local_vars=[targetC0,c0Size] → targetC0 → rhs=outShape[outShape.GetDimNum()-1]`), 得出该
  token 的真实谓词(如 "输出 shape 末维", 即 `dstTensor.shape[-1]`)。
- `signature_params`: 所属函数签名形参 `[{name, type_raw, role_hint?}]`。用 `role_hint`/`type_raw`
  判定形参是输入/输出 shape 还是 c0(如 `outShape` role_hint=output → 绑 `dstTensor`; `c0Size`
  role_hint=c0 → 算子无直传 c0, 是 dstFormat 派生量)。

脚手架不足时(`rhs_local_vars` 指向的变量无绑定, 或 `role_hint` 歧义), **按 `source_location`/
`var_bindings[].source_location` Read 源码补全**——不得退回纯 `error_string` 文本启发式。旧产物
(无此三字段)退回原行为。本规则是 OP_TILING_CHECK/OP_CHECK 判读的前置步骤, 优先于下表分类。

### 调用链与完整性信号

每条 `raw_checks` 带 `callees` 与 `unresolved_calls`,判读时:

1. **跟进 callee 找真实约束**:委派型 OP_TILING_CHECK 必读 `callees` 指向的函数体;非委派
   型若 cond 含函数调用(如 `GetDim(x) > 0`),同样跟进 callee 读懂真实语义。
2. **折叠分支前提**:用 `owning_function`+`source_location` **Grep 调用点**(谁调用了
   `owning_function`),Read 调用方函数体的 enclosing `if/switch`(如 `if (weightQuant) {...}`)
   作为分支前提,按 HC-003/HC-006 规则并进 `expr` 或在 `cross_check` 标 `mismatch_overbroad`。
   前提在**调用点**(反向边),不是从 Tiling 入口顺推。
3. **完整性信号 → missing_evidence**:`unresolved_calls` 非空(条件调了但快照无定义的疑似用户
   函数),或 `_closure/MANIFEST.json` 的 `unresolved_includes` 含算子相关头(非纯 SDK 头如
   `register/`/`opdev/`/`graph/`),表示调用链源码不完整 → 在 `source_evidence` 标
   `missing_evidence` 列出未解析项,**不猜测**其约束。`--source-root` 单目录模式下算子目录外
   共享头(`error_util.h`/`tiling_base.h` 等)本就不可达,常触发此信号;需完整链改用 `--src-tree`。
4. **委派 level-0 内核 — 分情况判定**:读 `source_raw.delegated_kernels`(算子 `op_host`/impl
   函数体里对 `l0op::<Symbol>` 或 `CommonOpExecutorRun` 的调用点)。对**去重后的 symbol** 逐个
   查 `_closure/MANIFEST.json` 的 `delegated_symbols_resolved`:
   - **impl 已解析**(symbol ∈ `delegated_symbols_resolved`):该 symbol 的 impl `.cpp` 已在
     `_closure/delegated/<相对 ops-* 子树路径>`(`l0op::TransData/ViewCopy/Contiguous/Reshape/ReFormat`
     等 impl 实际在算子所属 ops-* 子树内 conversion 族 `.cpp`,经链接期符号关联非 `#include`,
     由 `snapshot_source.py` 声明头驱动拉入),`raw_checks` 已含其 `OP_CHECK`(`source_location`
     指向 `_closure/delegated/...`)。按第三步 `OP_TILING_CHECK` 分类表正常判读(维度取值/参数间
     关系保留,委派型跟 callee,状态传播/纯资源过滤)+ **冗余过滤**:l0op impl 检查多为与 aclnn 层
     冗余的一致性断言(如 c0∈{8,16,32}、5D/6D 维度配对),**仅当 aclnn 层(op_host)无对应语义约束时**
     才产 hard_constraint(走现有 `source_authoritative` add_constraint/replace_constraint patch 路径),
     否则记冗余确认不重复产约束。
   - **impl 未解析**(symbol ∉ `delegated_symbols_resolved`,含 `CommonOpExecutorRun`——它不在任何
     l0op 声明头,声明头驱动天然不解析,impl 在 opbase 跨仓库框架执行器;及声明了但 tree_root 内无
     定义的):产 `{kind:"delegated_level0_kernel", symbol, source_location, reason:"impl 未进快照
     (CommonOpExecutorRun 为跨仓库框架执行器,或声明了但 tree_root 内无定义),约束未进快照"}`,
     **不猜测**其约束。
   **判定依据以 `MANIFEST.delegated_symbols_resolved` 为准**。**不要**用 `delegated_kernels.source_location`
   是否指向 `_closure/delegated/` 作判据——impl `.cpp` 内部调别的 l0op 内核(如 `contiguous.cpp` 调
   `l0op::Cast`、`reshape.cpp` 调 `l0op::ReFormat`)时调用点 location 也在 `_closure/delegated/`,但
   被调内核 impl 未必在快照,会误判已解析(关键陷阱)。
   注:`delegated_kernels` 仅匹配 `l0op::` 限定形式与 `CommonOpExecutorRun(`;算子若
   `using namespace l0op;` 后裸调 `TransData()` 不被捕获,读源码时手工补。

5. **legacy 图模式 tiling(R1/S1/S2 拉取)— 相关性裁决**:`raw_checks` 中 `source_location`
   以 `_closure/legacy_optiling/` 或 `_closure/comake/` 开头的条目,来自 canndev `op_tiling/`
   的 legacy 图模式 tiling(`IMPL_OP_OPTILING_LEGACY(<Op>,...)` 注册,如 `trans_data.cc` 的
   17× OP_TILING_CHECK),与算子 aclnn L0 路径(`l0op::<Symbol>` opbase 内核)非同代码对象/调用栈。
   - **先判相关性**:查 `MANIFEST.legacy_optiling_opnames`(R1 解析的 op-type 名,如 `TransData`),
     确认该 op-type 确被算子调用点发射——即 `delegated_kernels` 里的 `l0op::<Symbol>` 或 op_host
     `INFER_SHAPE/ADD_TO_LAUNCHER_LIST_AICORE(<Op>,...)`。命中才继续,否则只进
     `source_evidence.missing_evidence`(标 `path_relevance=graph_mode_unreachable`),**不提升**约束。
   - **命中后**:legacy tiling 的 OP_TILING_CHECK 多为与 aclnn 层冗余的一致性断言(C0∈{8,16,32}、
     5D/6D 维度配对、in/out shape 对应),按第三步 `OP_TILING_CHECK` 分类表判读 + **冗余过滤**(同第 4 点
     l0op impl 规则:仅当 aclnn 层无对应语义时才产 hard_constraint),并必循下文「冗余留证与冲突上报」
     (判冗余须留证、比 aclnn 层更紧须上报, 不得静默过滤)。提升的约束标 `origin=source_analysis`、
     `path_relevance=graph_mode_inferred`(图模式推断,非 aclnn 直接路径,quality-reviewer 据此降权)。
   - `s1_files`/`comake_files` 是同族 sibling impl(`trans_data_fz2fzg.cc`/`transdata_dsl_general.cc` 等),
     其 OP_TILING_CHECK 同上裁决;`s1_generic_skipped` 是通用名(DoTiling/CalcTiling 等)跳过项,不补;
     `legacy_optiling_truncated`/`s1_truncated`/`comake_truncated` 为真时表示该层超上限截断,未拉全,
     相关 callee 可能未解析,读源码时手工补。

### 冗余留证与冲突上报(判"与 aclnn 层冗余"必循)

判某 check 与 aclnn 层(op_host)冗余而过滤时——无论 legacy 图模式 tiling(第 5 点)还是已解析
l0op impl(第 4 点)——**必须留证, 不得静默过滤**:

1. **留证**:在 `source_evidence.missing_evidence` 记一条 `kind:"redundant_with_aclnn"`, 含
   `{check_source_location, owning_function, binding_summary, aclnn_counterpart_location,
   derivation_rule, path_relevance}`:
   - `binding_summary`:从 var_bindings/Read 源码得出的真实谓词(如 `dstTensor.shape[-1] ∈ {8,16,32}`),
     而非空泛的"已过滤";
   - `aclnn_counterpart_location`:对标 aclnn 层位置(如 `op_host/op_api/aclnn_npu_format_cast.cpp:384-401 CalcNdToNz`);
   - `derivation_rule`:一致的不变量(如 `C0 * sizeof(dtype) = 32B`);
   - `path_relevance`:`graph_mode_inferred`(legacy)/ `l0op_resolved`(已解析 impl)。
2. **冲突上报**:若 var_bindings/Read 揭示的约束**比 aclnn 层更紧**(如 legacy `CheckC0Value` 要求
   b8→c0=32 唯一, 但 aclnn `CheckFormatValid` 允许 FLOAT8→C0_16), **不静默过滤**:在
   `cross_check.mismatch_overbroad` 记一条, 附 `path_relevance:"graph_mode_inferred"` + `detail`
   说明冲突(源码 tiling 拒绝但 aclnn 层放行的组合), 交 quality-reviewer 降权裁决。不直接产
   hard_constraint, 避免误伤 aclnn L0 路径合法用例(legacy 路径未必在 aclnn opbase 路径上真跑)。

`missing_evidence` 不被 `validate_artifacts.py` 校验结构(自由), 新 `kind` 安全; `cross_check.mismatch_overbroad`
为既有必填字段, quality-reviewer 读其残留作 blocking——冲突上报项会进入其视野。

### 平台维度（SoC 分支裁决）— target_platform 取值

`raw_checks[*].soc_scope` 标记该 check 落在哪些 SoC 分支 if 块内（空=通用，所有产品都满足）。判读：

- `soc_scope` 空 → 通用约束，patch `target_platform="common"`（apply 写 common 桶，生成器 `data_handle_utils` 自动 fan-out 到 `product_support` 每个平台）。
- `soc_scope` 非空 → 调 `scripts/match_soc_platform.py --soc <soc_scope 逗号串> --constraints <constraints.json>` 拿 `{matched, unknown}`：
  - `matched` 命中的产品名 → patch `target_platform=<产品名>`；一条 check 命中多个产品 → 每个产品各产一条 patch（apply 各写对应产品桶）。
  - `unknown` 未映射的 SoC token（芯片族不在产品-芯片映射表，或 `product_support` 无对应产品）→ 写 `source_evidence.unknown_socnames`（每项 `{soc_token, source_location, owning_function}`），**不产该平台 patch**，返回时提示主协调器"请补产品-芯片映射表"。
- **soc_branches 是信号，不直接产约束**：`source_raw.soc_branches` 列出源码所有 SoC 分支点（aclrtGetSocName 字面量 + SocVersion 枚举），但只有 `raw_check.soc_scope` 非空（OP_CHECK 宏直接落在 SoC if 块内）才驱动 target_platform。多数 SoC 分支只影响 tiling 逻辑/资源计算（如 `if(soc==ASCEND310P){apiTmpSize=...}`），不绑参数约束 → 其 raw_check soc_scope 空 → 通用；SoC 分支内的 OP_CHECK 才是平台特定约束。
- legacy 图模式 tiling 的 SoC 分支同样先过 `path_relevance=graph_mode_inferred` 裁决（与第 5 点一致），命中调用点才提升。
- **映射表**：`agent/generators/data_definition/soc_product_matrix.py`（用户产品-芯片对应表 6 行）经 `canonical_soc`/`canonical_product` 规范化子串匹配。SocVersion 枚举 `ASCEND910B`/`ASCEND910C`/`ASCEND950`/`Ascend910B3` 映射到产品族；`ASCEND310P` 不在表（用户表 200I/500 A2 是 `Ascend310B` 非 `310P`）→ unknown，提示用户补表。

## 第三步:校验域(类型/范围/表达式)——每轮 EXTRACT 后一次

触发:每轮 EXTRACT 产出 `constraints.json` 之后、GENERATE 之前。首轮与 re-EXTRACT
(prompt 优化后下一轮)后**都跑**;单轮内只跑一次。输入:`src_snapshot`、`constraints.json`、
`inputs/operator_doc` 快照。

1. **类型一致性**:比对 `platform_matrix` 与 `constraints.json` 的 `inputs/outputs`
   dtype/format/attr,产出 `cross_check`:
   - `mismatch_overbroad`:constraints 允许源码不支持的 dtype/format(必败,阻断);
   - `mismatch_overnarrow`:源码支持但 constraints 漏列(覆盖不足,警告)。
2. **范围合理性**:对照 `raw_checks` 的 OP_CHECK_IF/CHECK_COND 条件,核对
   `allowed_range_value` 是否允许源码拒绝的值(如源码 `size > 0` 但范围含负数)。
3. **表达式准确性**:核对约束 `expr` 与源码校验语义是否一致(如源码 `axis < dim_size`
   但约束写 `axis <= dim.size`)。
4. 对每条 cross_check 命中(以及 `hard_constraints` 中源码强制但 constraints 无的项),
   **回查原文档** `inputs/operator_doc` 该关系是否存在,产出 `constraints_patch.json`:
   - 文档有该关系但 constraints 遗漏 → `op=add_constraint`,`basis_type=doc_quote`,
     `basis=<文档原文>`,`origin=doc`。
   - 文档无但源码强制 → `op=add_constraint`(尽量 enum `expr_type`,生成器 enforce)或
     `op=narrow_param_range`(值域,改 `allowed_range_value`),`basis_type=source_authoritative`,
     `basis=<source_location + error_string>`,`origin=source_analysis`。无法映射到 enum 的
     退化为声明式约束行(生成器不 enforce,仅作记录)。
     注:`narrow_param_range` 的 `proposed.allowed_range_value` 宜用 `ValueWithSrcText` 形式 `{value, src_text=basis}`,使范围修改在 constraints.json 留“按源码改”痕迹(apply 脚本在 source_authoritative 时也会补 src_text)。
   - **已有 `expr` 错误**(源码否决约束表达式语义,如源码 `axis < dim_size` 但约束写 `axis <= dim.size`)→ `op=replace_constraint`,`match_expr=<旧 expr 精确串>`,`proposed={expr_type,expr,relation_params}`(新值),`basis_type=source_authoritative`,`basis=<source_location + error_string>`,`origin=source_analysis`。apply 按平台+`match_expr` 精确匹配替换并设 `src_text=basis`,旧错 expr 不残留。

产物:`source_evidence.json`(`operator_name`/`aclnn_interfaces`/`platform_matrix`/
`hard_constraints`/`cross_check`/`doc_error`,仅上述六字段);`constraints_patch.json`
(独立文件)。`constraints_patch.json` 由主协调器调 `scripts/apply_constraints_patch.py`
**单次**机械应用并重校验,source-analyst **不直接写 constraints.json**。apply 失败/回滚
不重试源码分析,残留 `cross_check.overbroad` 交 GATE 阻断(回退路径)。

文档与源码不一致时**以源码为准**修正约束,并在 `source_evidence.json`/
`constraints_patch.json` 标注 `doc_error`(文档哪条错 + 源码依据)。

## source_evidence.json 契约(详见 docs/ARTIFACT_CONTRACTS.md)

必填:`operator_name`、`aclnn_interfaces`(list,处理一对多)、`platform_matrix`、
`hard_constraints`(list,每项 `constraint_id`/`expr_type`/`expr`/`relation_params`/
`source_location`/`error_string`/`src_text`)、`cross_check`(`mismatch_overbroad`/
`mismatch_overnarrow`)、`doc_error`(源码否决文档的条目列表,可为空)。上述六字段为必填;
允许 `missing_evidence`(调用链不完整时的未解析项列表: `unresolved_calls`/`unresolved_includes`
触发的未解析项,或 `delegated_kernels` 中**未解析的 symbol**(∉ `delegated_symbols_resolved`)触发的
`kind:"delegated_level0_kernel"` 条目,见上「调用链与完整性信号」)等可选额外字段。另允许 `unknown_socnames`（平台维度：源码 SoC 分支 token 不在产品-芯片映射表时，每项 `{soc_token, source_location, owning_function}`，供用户补表，见上「平台维度」）为可选字段。不产诊断/预检类额外产物。

## 边界

- 不改 constraints/cases/源码,不进 patch 子循环,不参与失败诊断。
- `hard_constraints.expr_type` 必须属 `InterConstraintsRuleType` 枚举,`expr` 对齐
  当前提示词 §6。
- 只读源码快照(项目内 `operator_src_snapshot`),不触外部源码树。只写
  `source_evidence.json` + `constraints_patch.json` 到当前 iter 目录。
- 证据不足时在 `source_evidence` 标注 `missing_evidence`,不猜测。
