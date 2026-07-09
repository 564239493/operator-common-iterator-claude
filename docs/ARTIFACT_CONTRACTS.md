# 运行产物契约

## 目录

```text
runs/<operator>-<timestamp>/
  run_state.json
  inputs/
    <原算子文档文件名>.md
    prompt_v1.md
    src_snapshot/              # 可选：--source-root/--src-tree 时只读复制。种子=op_host/**+op_api/** 的
                              # .cpp/.cc/.h/.hpp/.json + docs/aclnn*.md（不含 op_kernel/op_graph）；
                              # --src-tree 另经 #include 闭包把算子目录外共享头拉到 _closure/
                              # （如 common/inc/error_util.h、op_host/tiling_base.h）
      _closure/               # 可选：闭包拉取的共享头, 按相对 ops-* 子树路径存放
        MANIFEST.json         # copied_count/unresolved_includes/ambiguous_resolutions/skipped_test_stub_count
                              # /delegated_impl_count/delegated_symbols_resolved/delegated_symbols_unresolved
                              # /legacy_optiling_opnames/legacy_optiling_unresolved/legacy_optiling_truncated
                              # /s1_files/s1_truncated/s1_generic_skipped/comake_files/comake_truncated/backend_trees
        delegated/            # 可选：仅 --src-tree。声明头驱动的 l0op 内核 impl .cpp(TransData/ViewCopy/
                              # Contiguous/Reshape 等), 按相对 ops-* 子树路径存放; OP_CHECK 进 raw_checks
        legacy_optiling/      # 可选：--backend-tree(默认 <src-tree>/canndev)。R1 按 op-type 名(IMPL_OP_OPTILING_LEGACY
                              # 等)拉的 canndev op_tiling legacy 图模式 tiling .cc + S1 拉的同族 sibling .cc;
                              # OP_TILING_CHECK 进 raw_checks, source-analyst 按 path_relevance=graph_mode_inferred 裁决
        comake/               # 可选：S2 按 op-stem 在 op_tiling/ 拉的同前缀 co-member(补 S1 漏的 static helper .cc)
  iter_001/
    constraints.json
    source_raw.json            # 可选：源码启用时，extract_source_constraints.py 确定性产物（raw_checks 等）
    source_evidence.json       # 可选：source-analyst 产（hard_constraints + cross_check + doc_error [+missing_evidence]）
    constraints_patch.json     # 可选：source-analyst 产，由 apply_constraints_patch.py 机械应用
    generation_summary.json
    cases.json
    execution_result.json
    quality_gate.json
    analysis.json
    prompt_v2.md
    prompt_changes_v2.md
```

## run_state.json

必须包含 `run_id`、`operator_doc_source`、`operator_doc`、`operator_src_source`、
`operator_src_snapshot`、`operator_src_tree`、`operator_backend_trees`、`current_prompt_source`、`current_prompt`、
`current_prompt_modules`、`mode`、`server_config`、`max_iterations`、`case_count`、
`current_iteration`、`state`、`history` 和时间戳。state 只能取 WORKFLOW.md 定义的状态。
`operator_src_source`/`operator_src_snapshot` 在未启用源码分析时为空串，此时源码校验
全程跳过。源码分析两种启用方式：`--source-root <算子目录>` 直传目录快照；`--src-tree
<operators-src 根>` 由 init_run 调 `locate_operator_source.locate_in_tree` 跨 ops-* 子树
按 aclnn 名（doc 文件名派生，可 `--aclnn-name` 覆盖）自动定位算子目录后快照，未命中不
阻断（回退纯文档）。`operator_src_tree` 记录 `--src-tree` 值（直传模式为空串）。

快照由 `scripts/snapshot_source.py:snapshot_operator_source` 生成：种子复制算子自身
op_host/op_api/docs/config 后，`--src-tree` 模式额外经 `#include` 闭包把算子目录外共享头
（`common/inc/error_util.h`、`op_host/tiling_base.h` 等）拉到 `src_snapshot/_closure/`
（搜索范围收紧到算子所属 `ops-*` 子树，跳过 tests/stub 路径），并写 `_closure/MANIFEST.json`
（`unresolved_includes` 多为 SDK 头如 `register/`/`opdev/`）。`--src-tree` 模式在 `#include` 闭包后
额外做**声明头驱动 l0op impl 拉取**：闭包复制的 `aclnn_kernels/*.h`（含 `namespace l0op`）里声明的
symbol，按定义在所属 `ops-*` 子树内定位 impl `.cpp`，整文件复制到 `_closure/delegated/<相对子树路径>`
（不追逐 impl 自身 `#include`，防连锁雪崩；其内部 l0op 调用经 `delegated_kernels` 标
`missing_evidence`），写 MANIFEST 的 `delegated_impl_count`/`delegated_symbols_resolved`/
`delegated_symbols_unresolved`。`--source-root` 单目录模式不闭包、不拉 impl，算子目录外共享头不可达
→ 常触发 `source_evidence.missing_evidence`。

`operator_doc_source` 可以指向项目外部，只允许读取；`operator_doc` 必须指向 run
目录内的快照，后续 Agent 只使用快照。

`current_prompt_source` 指向项目内 `prompts/operator_constraints_extract_vN.md` 基线
（v4 起为模块化基线）；`current_prompt` 指向 run 内 `inputs/prompt_v1.md` 快照。
默认（未传 `--prompt`）由 `scripts/select_prompt.py` 按算子文档特征装配基线 + 命中的
`prompts/modules/*.md` 模块写入该快照，`current_prompt_modules` 记录命中的模块名清单
（可为空）；显式 `--prompt` 为逃生口，原样复制指定文件、`current_prompt_modules=[]`。
constraint-extractor 始终只读 `current_prompt` 快照，不感知装配过程。

## constraints.json

必须满足 `agent.generators.common_model_definition.OperatorRule`。关键字段包括
operator_name、product_support、parameters 和 constraints_in_parameters。每个约束
应来自原文，不用聊天内容补充。每条约束带 `origin` 字段：`doc`（文档提取，默认）或
`source_analysis`（源码校验 patch 写入）；`origin=source_analysis` 的约束只能由
`scripts/apply_constraints_patch.py` 机械写入，source-analyst 不直接写 constraints.json。

`allowed_range_value.value` 非空时，`type` 必须显式标注为 `enum`（离散枚举，如
格式码/bool/字符串候选）或 `range`（数值区间）；缺失或非法值由
`scripts/validate_artifacts.py` 的 `validate_constraints` 兜底报错，GATE 拦回
re-EXTRACT。`value=[]`（空）时不强制 `type`（tensor 参数无值域约束常留空）。

`allowed_range_value.type=range` 的区间端点必须为实际数值，不允许用 `null` 表示
无界；单边或开区间写入 `constraints_in_parameters`，使用不等式表达。
`type=enum` 允许 `null` 作为明确的离散候选。`expr` 中允许裸 `null`，校验和求解前
会规范化为 Python `None`，但只能用于空值/存在性判断，不能参与数值大小比较。

## source_raw.json（可选，源码启用时）

`extract_source_constraints.py` 的确定性产物（不经 LLM 判读，不进 `validate_artifacts.py`）。
含 `platform_matrix`、`aclnn_interfaces`、`raw_checks`、`delegated_kernels`、`soc_branches`。`raw_checks` 每项字段：

- `kind`：`OP_TILING_CHECK` / `OP_CHECK` / `OP_CHECK_IF` / `CHECK_COND` /
  `OP_CHECK_DTYPE_NOT_SUPPORT` / `OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE` / `OP_LOGE`。
  `OP_TILING_CHECK(cond, log_func, return_expr)` 为 3 参宏，
  语义同 OP_CHECK（cond 真→报错），多见于 `*_tiling.cpp`，混有维度取值/参数间关系/委派/
  状态传播/纯资源多类检查，由 source-analyst 按类分类（见 analyze-source skill）。
  `OP_LOGE(errcode, fmt, ...)` 为无条件日志宏（非检查宏），仅收 `args[0]` 含 `ACLNN_ERR_PARAM`
  的调用点，抓 L0 impl `Check*` 函数参数校验消息（如 "TransData not support: %s -> %s"）。
- `condition`/`error_string`/`source_location`/`owning_function`（限定名 `Class::method`）/
  `function_errors`（所属函数内 error_string 去重，作"函数自述前提"线索）。`OP_LOGE` 项
  `condition` 留空——其触发条件在外层 `if`（本提取器不解析控制流），仅 `error_string` 有效，
  主要丰富 `function_errors`，由 source-analyst 结合所属函数判读。
- `callees`：条件里调了且快照内有定义的函数，每项 `{name, source_location, qualified_name}`，
  供 source-analyst 跟进被调用函数体找真实约束（委派型 `OP_TILING_CHECK(!CheckParamsShape(), …)`
  尤需）。
- `unresolved_calls`：条件里调了但快照无定义的疑似用户函数（全小写名视作 std/内置不入列），
  作调用链完整性信号。
- `soc_scope`：平台维度。该 check 行号落入的 SoC 分支 if 块的 `soc_token` 去重列表（空=通用，
  所有产品都满足），由 `soc_branches`（见下）的 `if_start_line`/`if_end_line` 归属。source-analyst
  据此设 patch 的 `target_platform`（空→`common`；非空→经 `soc_product_matrix` 映射产品名；未映射→
  `source_evidence.unknown_socnames`）。多数 SoC 分支只影响 tiling 逻辑不绑参数约束→其 raw_check
  soc_scope 空→通用；SoC 分支内的 OP_CHECK 才是平台特定约束。
- `resolved_constants`/`var_bindings`/`signature_params`（可选，仅非空出现）：变量绑定脚手架，
  供 source-analyst 免全文逆向。`resolved_constants`=`{token: 字面值}`（condition 里命中跨文件
  const_map 的常量，如 `{C0_8:8, C0_16:16, C0_32:32}`）；`var_bindings`=条件里局部变量的赋值回溯列表，
  每项 `{token, rhs, rhs_local_vars, source_location}`（沿 `rhs_local_vars` 链回溯到真实谓词，如
  `axisC0`→`outShape[outShape.GetDimNum()-1]`）；`signature_params`=所属函数签名形参
  `[{name, type_raw, role_hint?}]`（`role_hint` 启发式标 input/output/c0，LLM 可推翻）。同名异值常量
  落顶层 `const_ambiguities`（可选）。source-analyst 据此判读，不足时按 `source_location` Read 源码
  补全（详见 analyze-source skill「变量绑定脚手架」）。

`delegated_kernels` 每项 `{symbol, source_location, owning_function}`：算子 `op_host`/impl 函数体里
对 level-0 共享内核（`l0op::<Symbol>`，如 `l0op::TransData`/`l0op::ViewCopy`/`l0op::Contiguous`/
`l0op::Reshape`/`l0op::ReFormat`）或通用二段执行器（`CommonOpExecutorRun`）的调用点。`l0op::<Symbol>`
的 impl 实际在算子所属 `ops-*` 子树内的 conversion 族 `.cpp`（同子树，经链接期符号关联非 `#include`），
`--src-tree` 模式由 `snapshot_source.py` 声明头驱动拉入 `_closure/delegated/`，其 `OP_CHECK` 进
`raw_checks`（`source_location` 指向 `_closure/delegated/...`）；impl 内部再调的别的 l0op 内核
（如 `l0op::Cast`/`l0op::BroadcastTo`，其 impl 未拉）也经本字段补信号 → `missing_evidence`。
`CommonOpExecutorRun` 在 opbase 跨仓库框架执行器，不在任何 l0op 声明头，声明头驱动天然不拉 →
`missing_evidence`。`raw_checks` 的 callee 解析只扫 check 宏条件，抓不到函数体里这类调用，本字段补
确定性信号。注：仅匹配 `l0op::` 限定形式；算子若 `using namespace l0op;` 后裸调 `TransData()` 不被
捕获（裸名歧义），属已知残差。

`soc_branches`（平台维度）每项 `{soc_token, match_mode("string_literal"/"enum"),
source_location, owning_function, if_start_line, if_end_line}`：源码 SoC 分支标记点，两类写法——
风格A `aclrtGetSocName()` 返回值比较的 `"Ascend..."` 字面量（主机侧 aclnn 接口）；风格B
`SocVersion::ASCEND\w+` 枚举比较（device 侧 tiling 主流，如 `ASCEND910B`/`ASCEND310P`/`ASCEND950`）。
每条取外层 if 块范围（`_matching_brace_end`），供 `raw_checks[*].soc_scope` 归属（行号落入则收该
token）。无花括号单语句 if 跳过（计 `soc_skipped_bare_if` 留痕）。soc_branches 是信号，不直接产约束；
只有 raw_check.soc_scope 非空（OP_CHECK 宏落在 SoC if 块内）才驱动 target_platform。SocName↔产品名
映射见 `agent/generators/data_definition/soc_product_matrix.py`（用户产品-芯片对应表 6 行），未映射
SoC（如 `ASCEND310P`，用户表 200I/500 A2 是 `Ascend310B` 非 `310P`）落 `source_evidence.unknown_socnames`。

`.h/.hpp` 扫描前剥 `#define` 续行块，避免匹配宏定义本身；`func_starts` 仅取"定义"（后跟 `{`），
使 `owning_function` 指向最近外层函数定义。source-analyst 在此基础上做 expr_type 归类与
约束差异判读，产 `source_evidence.json`。

## source_evidence.json（可选，源码启用时）

source-analyst 在每轮 EXTRACT 后产出。必含 `operator_name`、`aclnn_interfaces`、
`platform_matrix`、`hard_constraints`（每项 constraint_id/expr_type/expr/
relation_params/source_location/error_string/src_text）、`cross_check`（mismatch_overbroad/
mismatch_overnarrow）、`doc_error`（源码否决文档的条目列表，可为空）。`hard_constraints.expr_type`
必须属 `InterConstraintsRuleType` 枚举，`expr` 对齐当前提示词 §6 语法。不产
诊断/预检类额外字段。`validate_artifacts.py source_evidence` 校验上述字段存在性，
quality-reviewer 读 `cross_check.mismatch_overbroad` 残留作 blocking。允许可选
`missing_evidence`（调用链不完整时的未解析项列表），三路触发：当 `raw_checks[*].unresolved_calls`
非空、或 `_closure/MANIFEST.json` 的 `unresolved_includes` 含算子相关头（非纯 SDK 头）、或
`source_raw.delegated_kernels` 中有**未解析的 symbol**（∉ `_closure/MANIFEST.json` 的
`delegated_symbols_resolved`）时，source-analyst 标 `missing_evidence` 而不猜测约束。已解析的
l0op impl 其 `OP_CHECK` 已进 `raw_checks`（`source_location` 指向 `_closure/delegated/...`），按正常
`OP_TILING_CHECK` 分类判读 + 冗余过滤（与 aclnn 层冗余的一致性断言不重复产约束），不触发
`missing_evidence`。未解析的委派 symbol（含 `CommonOpExecutorRun`——跨仓库框架执行器，不在 l0op 声明
头；及声明了但 `ops-*` 子树内无定义的；impl 内部调的 `l0op::Cast`/`l0op::BroadcastTo` 等其 impl 未拉）
按**去重后的 symbol** 每个产一条
`{kind: "delegated_level0_kernel", symbol, source_location, reason: "impl 未进快照
（CommonOpExecutorRun 为跨仓库框架执行器，或声明了但子树内无定义），约束未进快照"}`，
显式标盲区而非静默假设覆盖。**判定依据以 `MANIFEST.delegated_symbols_resolved` 为准**，
不用 `delegated_kernels.source_location` 是否指向 `_closure/delegated/` 作判据（impl `.cpp` 内部
调别的 l0op 内核时调用点 location 也在 `_closure/delegated/`，但被调内核 impl 未必在快照，会误判）。

另允许 `kind:"redundant_with_aclnn"`（判"与 aclnn 层冗余"而过滤时的留证，非盲区）：source-analyst
判某 check 与 aclnn 层（op_host）冗余而不产约束时，必须留证，每项 `{kind, check_source_location,
owning_function, binding_summary, aclnn_counterpart_location, derivation_rule, path_relevance}`，
禁止空泛"已过滤"。比 aclnn 层更紧的冲突则进 `cross_check.mismatch_overbroad`（标
`path_relevance:"graph_mode_inferred"`，交 quality-reviewer 降权）。详见 analyze-source skill
「冗余留证与冲突上报」。`missing_evidence` 不被 `validate_artifacts.py` 校验结构，新 `kind` 安全。

**`--source-root` 单目录模式局限**：仅复制该目录内文件 + 同目录可达的 `#include`，算子目录外
共享头（`error_util.h`/`tiling_base.h`/`norm_tiling_check_common.h` 等）不可达 → `unresolved_includes`
非空 → 常触发 `missing_evidence`。需完整调用链源码（尤其 norm/conv/mc2 族共享 tiling helper）
须用 `--src-tree`（由 `init_run` 把闭包搜索范围收紧到算子所属 `ops-*` 子树）。

另允许 `unknown_socnames`（平台维度，可选）：源码 SoC 分支 token 不在
`agent/generators/data_definition/soc_product_matrix.py` 产品-芯片映射表时，每项
`{soc_token, source_location, owning_function}`，供用户手动补表。source-analyst 不为未映射
SoC 产 patch。`validate_artifacts.py source_evidence` 校验其格式（存在则须为 list、每项含
soc_token/source_location，格式错阻断、存在本身不阻断）。

## constraints_patch.json（可选，源码启用时）

source-analyst 产出的补丁建议数组，由 `scripts/apply_constraints_patch.py` 机械应用
（单轮内仅一次，失败回滚不重试）。每项 `op` 为 `add_constraint`、`narrow_param_range` 或
`replace_constraint`；`basis_type` 为 `doc_quote`（文档有但 constraints 漏）或
`source_authoritative`（源码强制但文档无）；`origin` 为 `doc` 或 `source_analysis`；
`target_platform` 为产品名（须逐字符匹配 `constraints.json` 的 `product_support` 列表项）或
`"common"`（通用，生成器 `data_handle_utils` 自动 fan-out 到 `product_support` 每个平台）。
source-analyst 按 `raw_check.soc_scope` 取值：空→`common`；非空→经 `soc_product_matrix` 映射命中
的产品名（每命中产品各一条 patch）；未映射 SoC 不产 patch 而落 `source_evidence.unknown_socnames`。
apply 按 `target_platform` 写入 `constraints_in_parameters` 对应产品桶（或 `common` 桶）。
`narrow_param_range` 在 `basis_type=source_authoritative` 时由 apply 脚本把
`allowed_range_value` 规整为 `ValueWithSrcText` 形式并写入 `src_text=basis`，使范围修改在
constraints.json 留“按源码改”痕迹。`replace_constraint` 按 `target_platform`+`match_expr`
（旧 expr 精确串）替换已有约束的 `expr`/`expr_type`/`relation_params`，设 `src_text=basis`、
`origin=source_analysis`，用于“已有表达式错误按源码修正”（旧错 expr 不残留）。
`apply_constraints_patch.py` 写回后重跑 `OperatorRule` 校验，不通过则不写输出、返回结构化
错误。`validate_artifacts.py constraints_patch` 校验 op/basis_type/origin/proposed 取值受控。

## cases.json

JSON 数组，每项为生成器 CaseConfig 的 model_dump 结果。禁止 Agent 手工伪造。

`cases.json` 是执行前的紧凑表示。对于带 `length` 的列表类输入，只保留一个输入
描述，由执行阶段生成 `cases_expanded.json`：

- `range_values` 为标量时，表示列表中每个元素共用该取值规格；
- `range_values` 为列表且长度等于 `length` 时，表示逐元素取值规格；
- 生成阶段不得为了匹配 `length`，在 `ListVar.resolve_model()` 中把标量复制成列表。

诊断用例格式问题时必须同时检查 `cases.json` 和 `cases_expanded.json`。如果紧凑
表示已被正确展开，不能把标量 `range_values` 判为 generator_bug；如果展开过程
本身有误，应归入执行适配层的 executor_bug。

## execution_result.json

至少包含：

```json
{
  "status": "success | failed | error | timeout",
  "mode": "mock | real",
  "passed": 0,
  "failed": 0,
  "total": 0,
  "records": [],
  "engine_error": ""
}
```

必须满足 passed + failed = total。engine_error 非空时不能宣称业务成功。

## analysis.json

root_cause 只能为 constraint_extraction、generator_bug、executor_bug。每项
specific_issues 应关联 case id、日志或文档证据。

## quality_gate.json

至少包含 status、checks、blocking_issues、next_state。blocking_issues 非空时
status 必须为 blocked，主协调器不得越过门禁。

## 目录批次产物

```text
runs/batches/<batch-id>/
  batch_state.json
  batch_summary.json
```

`batch_state.json` 必须冻结 source_directory、glob、recursive、prompt、
max_iterations、case_count、mode、server_config、continue_on_error 和有序 operators。
每个 operator 包含原文档绝对路径、PENDING/RUNNING/COMPLETED 状态、单算子 run_id、
run_dir 与 terminal_state。任意时刻最多只能有一个 RUNNING 项。

`batch_summary.json` 是由批次状态确定性生成的只读汇总视图，包含 total、pending、
running、completed、success 和 failed。仅 `SUCCESS` 计入 success；`BLOCKED`、
`MAX_ITERATIONS`、`STOP_GENERATOR_BUG` 和 `STOP_EXECUTOR_BUG` 计入 failed。
