---
description: 从算子源码快照提取确定性约束事实并判读为 supplementary/uncertain/conflict 三 markdown，供约束补充与失败反向推导。仅在源码快照存在时使用。
---

# 算子源码证据分析规范

源码是**交叉校验证据 + 失败反向推导的证据源**，不是约束提取主输入。
`constraint-extractor` 永不直接读源码；本 skill 产 `source_raw.json` + 3 个
markdown 落盘，下游 `constraint-supplementer` / `failure-analyst` 仍"只读落盘"。

## 触发条件

仅当 `run_state.json` 的 `operator_src_snapshot` 非空时使用。为空则跳过，
退回纯文档驱动，保持提示词可移植性。

## 第一步：确定性提取（两域共用）

用 Bash 执行（snapshot 路径取自 `run_state.operator_src_snapshot`）：
```
python scripts/extract_source_constraints.py \
  --snapshot <operator_src_snapshot> --out <iter-dir>/source_raw.json
```
拿到 `source_raw.json`：
- `aclnn_interfaces`（list，aclnn 接口签名，已过滤 aclnnInner）。
- `platform_matrix`（`soc_versions`/`is_reg_base_used`/`dtypes`/`by_file`）。
- `raw_checks`（每项 `{macro, condition, error_string, source_location}`，
  来自 OP_CHECK/OP_LOGE(ACLNN_ERR_PARAM 族)/OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE/
  OP_TILING_CHECK + canndev 的 VECTOR_INNER_ERR_REPORT_* 族）。

**快照范围**：`init_run._snapshot_operator_source` 在树根+算子名场景默认走
`collect_operator_source.collect`（include 不动点闭包 + canndev 多层 + 后缀变体
+ L0 反查，覆盖跨目录 L0 实现，如 npu_format_cast → transdata/contiguous/
reshape/transpose），并落 `manifest.json` + `closure_report.md`；单算子目录或
collect 失败时回退 SEED 固定位置 glob + L1 stem 闭包（不解析 include，跨目录
L0 会丢）。默认流程不传 `--extra-stem`，故 L0 反查的已知盲区（`l0op` 函数与声明头
不同 stem，如 `ViewCopy` 声明在 `contiguous.h`、实现在 `view_copy.cpp`）仍漏，
需 `collect-operator-source` skill 手动补 `--extra-stem`。
`extract_source_constraints.py` 只扫快照内文件，不跨快照边界；正则已支持
canndev 的 `VECTOR_INNER_ERR_REPORT_*` 族（canndev 不用
`ACLNN_ERR_PARAM_INVALID`，全树 0 命中）。仅当 include 指向外部 SDK 头（`opdev/`/
`graph/`/`securec.h` 等）时标 `external`，项目内找不到的标 `missing`，在
uncertain-doc.md 标 `missing_evidence`。

## extract 域（EXTRACT 阶段，与 constraint-extractor 并行）

输入：`operator_src_snapshot`、`inputs/<doc>.md`（算子文档快照）、当前轮目录。
两步：

### 第二步：LLM 判读（本 agent 核心）

对 `raw_checks` 做语义判读，产出 `hard_constraints`（内部中间表示，不直接落盘）：

- 把每条 `raw_checks[*].condition` 归类为 `expr_type`，**必须属**
  `agent/generators/common_model_definition.py` 的 `InterConstraintsRuleType`
  枚举 11 值：`shape_broadcast`/`shape_choice`/`shape_equality`/`shape_dependency`/
  `shape_value_dependency`/`type_dependency`/`type_equality`/`value_dependency`/
  `format_equality`/`presence_dependency`/`parameter_representation`。
  - `q.shape[-1] <= 1024` / `viewShapeDim >= 2 && viewShapeDim <= 6` →
    `shape_value_dependency`。
  - `len(q.shape) in (3,4)` → `shape_dependency`。
  - `additionalDtype == ge::DT_INT8` → `value_dependency`。
  - dtype 一致/互推 → `type_equality`/`type_dependency`。
  - format 配对 → `format_equality`。
  - **禁止 `self_shape_axis_value`**（不在枚举）；`shape[axis]==value` 用
    `shape_value_dependency`，`relation_params` 列出张量参数。
- 产出对齐 extract-constraints skill 的 expr 语法（裸 `null`→`None`、数值范围
  用不等式、`len()` 不用 `.array_length`、禁止 `in [[min,max]]`、`null` 不做
  数值边界）。
- `relation_params` 列出涉及参数（与 constraints.json 的参数名一致）。
- 保留 `source_location`、`error_string`、原始 condition。
- **过滤非约束性检查**：nullptr 检查、纯日志打印、`GRAPH_SUCCESS` 返回判断、
  `OP_LOGW`（warning，非 ACLNN_ERR_PARAM 族）不产出硬约束。

### 产 3 个 markdown + 1 个结构化 json（落 `inputs/`）

对照 `inputs/<doc>.md` 该关系是否存在，分三类产出：

1. **`inputs/supplementary-doc.md`**（源码强制 + 文档缺/弱的约束，add 候选）：
   - 格式 = **constraint-supplementer 可读**：每条 = 参数关系约束（shape
     广播/相等/依赖、dtype 一致/依赖、value 依赖、format 一致、presence 依赖）
     + 源码依据（`source_location` + `error_string`）+ 平台 + 可选 expr 片段。
   - `expr_type` 用枚举值；`expr` 对齐 extract-constraints 规范。
   - 范本：`operator_other_docs/supplement_constraints.md`（aclnnNpuFormatCast
     C1–C13），但其中 `self_shape_axis_value` 全部改 `shape_value_dependency`。
   - 平台条件性约束：按 `platform_matrix.soc_versions`/`is_reg_base_used` 标注
     生效平台（与 constraints.json 的 `constraints_in_parameters` 平台 key 一致）；
     跨平台通用写 `target_platform="all"`。

2. **`inputs/uncertain-doc.md`**（候选关系，不确定是否约束，留待第 2 轮提升）：
   - 每条 = 候选关系 + 源码证据 + **`error_string`**（触发该约束会报的错误串，
     供 diagnose 域模糊匹配）+ 不确定原因。
   - canndev 深层闭包遗漏（带后缀变体如 `_tiling_arch35`、include 闭包）标
     `missing_evidence`。

3. **`inputs/conflict-doc.md`**（源码 vs 文档冲突，人工裁决候选）+ 结构化
   `inputs/conflict_candidates.json`：
   - conflict-doc.md（人读）：每条 = `{conflict_id, 参数/关系, 文档约束+原文
     依据, 源码约束+source_location/error_string, suggested_winner(仅供参考)}`。
   - conflict_candidates.json（机读，供 `apply_conflict_resolution.py`）：
     ```json
     [{
       "conflict_id": "CF1",
       "target_platform": "<平台名|all>",
       "doc_expr": "<文档提取的原 expr 精确文本，replace 的 match_expr>",
       "proposed_source": {"expr_type": "...", "expr": "...", "relation_params": ["..."]},
       "source_location": "...",
       "error_string": "..."
     }]
     ```
   - `doc_expr` 必须从 `constraints.json` 对应平台桶**精确复制**（不得改写），
     否则 `apply_conflict_resolution.py` 精确匹配失败。先 grep 确认存在。

### 自校

```
python scripts/validate_artifacts.py supplementary_doc <inputs>/supplementary-doc.md
python scripts/validate_artifacts.py uncertain_doc <inputs>/uncertain-doc.md
python scripts/validate_artifacts.py conflict_doc <inputs>/conflict-doc.md
python scripts/validate_artifacts.py source_raw <iter-dir>/source_raw.json
```
空文件允许（uncertain/conflict 可空，supplementary 空则补充逻辑跳过）。失败
修正最多三次。

## diagnose 域（GATE 失败后，failure-analyst 之前或之内）

触发：GATE 判有用例失败，主协调器在委派 `failure-analyst` **之前**委派本域。
输入：`operator_src_snapshot`、`source_raw.json`、`inputs/uncertain-doc.md`、
`execution_result.json`。

流程：
1. 第一步确定性提取（如 source_raw.json 已存在可复用）。
2. 把 `execution_result.json` 中失败 case 的日志/错误信息，与 uncertain-doc.md
   的 `error_string` + raw_checks 的 error_string 做**模糊匹配**（把
   `%ld`/`%zu`/`%s`/`%d` 当通配符）。命中的记入 `log_match`。
3. 对每条 `log_match`，查该 uncertain 关系是否可确认：
   - 可确认 → 追加到 `inputs/supplementary-doc.md`（在条目末尾标注
     `origin=diagnose_confirmed` + 命中的 failed_case_id + error_string）。
   - 不可确认 → 留 uncertain-doc.md，记 `missing_evidence`。
4. 读 `conflict-doc.md` + `inputs/conflict_resolution.json`：若失败命中
   **未裁决** conflict，在 `source_evidence.json` 标注提示用户先裁决（不自动
   转约束——冲突永远走人工通道）。

产物：`<iter-dir>/source_evidence.json`（含 `operator_name`、`aclnn_interfaces`、
`platform_matrix`、`hard_constraints`、`error_string_catalog`、`log_match`、
`conflict_pending`）。供 failure-analyst 引用，不替代其根因判定。

## 根因矩阵（diagnose 域 `suggested_root_cause` 仅供参考，最终根因由 failure-analyst 下）

| 源码有此约束 | constraints 有 | 文档有 | suggested_root_cause |
|---|---|---|---|
| Y | Y | Y | `generator_bug`（生成器没满足约束） |
| Y | N | N | `constraint_extraction`（文档+源码缺口，救回盲区） |
| Y | N | Y | `constraint_extraction`（提取漏抽文档条款） |
| Y | Y | N | `constraint_extraction`（源码比文档严，建议文档补说明） |
| N | N | N | 不路由（留 failure-analyst 判，可能 executor_bug） |

矩阵第 2 行正是 failure-analyst "证据不足保守归 executor_bug" 的盲区——
源码证据把它救回 `constraint_extraction`，使补充扩充闭环生效。

## 边界

- 不改 constraints.json/cases/源码，不下最终根因。
- `expr_type` 必须属 `InterConstraintsRuleType` 11 值；**禁 `self_shape_axis_value`**。
- 只读源码快照（项目内 `operator_src_snapshot`），不触外部源码树。
- 只写 `inputs/supplementary-doc.md`、`inputs/uncertain-doc.md`、
  `inputs/conflict-doc.md`、`inputs/conflict_candidates.json`、
  `<iter-dir>/source_raw.json`、`<iter-dir>/source_evidence.json`。
- 证据不足时标注 `missing_evidence`，不猜测。
- 不做 precheck 域（z3 预检 cases 不在本 skill 范围）。
