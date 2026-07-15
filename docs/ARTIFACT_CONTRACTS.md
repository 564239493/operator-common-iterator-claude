# 运行产物契约

## 目录

```text
runs/<operator>-<timestamp>/
  run_state.json
  inputs/
    <原算子文档文件名>.md
    prompt_v1.md
    src_snapshot/                      # 可选：--src 浅快照（op_host/op_api/docs/config）
    supplementary-doc.md               # 可选：source-analyst 产，constraint-supplementer 主输入
    uncertain-doc.md                   # 可选：source-analyst 产，候选关系待第 2 轮提升
    conflict-doc.md                    # 可选：source-analyst 产，人工裁决候选（人读）
    conflict_candidates.json           # 可选：source-analyst 产，结构化冲突候选（机读）
    conflict_resolution.json           # 可选：用户裁决 [{conflict_id, winner}]
    supplement_constraints.md          # 可选：--supplement-constraints 手写快照
  iter_001/
    constraints.json
    constraints.json.pre_supplement   # 可选：合并补充前的 EXTRACT 原始备份（每轮覆盖）
    constraints.json.pre_conflict      # 可选：冲突合并前备份
    constraints_patch.json             # 可选：约束补充阶段产出的 add/replace patch
    source_raw.json                    # 可选：source-analyst 确定性提取的源码事实
    source_evidence.json               # 可选：source-analyst diagnose 域产（log_match）
    supplement_additions.md            # 可选：failure-analyst 推的补充增量
    generation_summary.json
    cases.json
    cases_ttk.csv
    execution_result.json
    quality_gate.json
    analysis.json
    prompt_v2.md
    prompt_changes_v2.md
```

## run_state.json

必须包含 `run_id`、`operator_doc_source`、`operator_doc`、`operator_src_source`、`operator_src_snapshot`、`current_prompt_source`、`current_prompt`、
`current_prompt_modules`、`supplement_constraints_source`、`supplement_constraints`、`mode`、
`server_config`、`max_iterations`、`case_count`、`operator_family`、`test_framework`、
`current_iteration`、`state`、
`history` 和时间戳。state 只能取
WORKFLOW.md 定义的状态。

`operator_doc_source` 可以指向项目外部，只允许读取；`operator_doc` 必须指向 run
目录内的快照，后续 Agent 只使用快照。

`supplement_constraints_source` 可指向项目外部的补充约束 Markdown（可选，未提供时为空串）；
`supplement_constraints` 指向 run 内 `inputs/supplement_constraints.md` 快照。为空串时跳过
约束补充阶段，回退纯文档驱动流程。

`current_prompt_source` 指向项目内 `prompts/operator_constraints_extract_vN.md` 基线
（v4 起为模块化基线）；`current_prompt` 指向 run 内 `inputs/prompt_v1.md` 快照。
默认（未传 `--prompt`）由 `scripts/select_prompt.py` 按算子文档特征装配基线 + 命中的
`prompts/modules/*.md` 模块写入该快照，`current_prompt_modules` 记录命中的模块名清单
（可为空）；显式 `--prompt` 为逃生口，原样复制指定文件、`current_prompt_modules=[]`。
constraint-extractor 始终只读 `current_prompt` 快照，不感知装配过程。

## constraints.json

必须满足 `agent.generators.common_model_definition.OperatorRule`。关键字段包括
operator_name、product_support、parameters 和 constraints_in_parameters。每个约束
应来自原文，不用聊天内容补充。每条约束带 `origin` 字段：`doc`（文档提取）或
`supplement`（约束补充阶段合并）。约束补充阶段产出的 `constraints_patch.json` 经
`scripts/apply_supplement_constraints.py` 确定性合并后追加/替换条目并标 `origin="supplement"`。

`allowed_range_value.value` 非空时，`type` 必须显式标注为 `enum`（离散枚举，如
格式码/bool/字符串候选）或 `range`（数值区间）；缺失或非法值由
`scripts/validate_artifacts.py` 的 `validate_constraints` 兜底报错，GATE 拦回
re-EXTRACT。`value=[]`（空）时不强制 `type`（tensor 参数无值域约束常留空）。

`allowed_range_value.type=range` 的区间端点必须为实际数值，不允许用 `null` 表示
无界；单边或开区间写入 `constraints_in_parameters`，使用不等式表达。
`type=enum` 允许 `null` 作为明确的离散候选。`expr` 中允许裸 `null`，校验和求解前
会规范化为 Python `None`，但只能用于空值/存在性判断，不能参与数值大小比较。

## constraints_patch.json

约束补充阶段（条件触发，仅 `run_state.supplement_constraints` 非空时执行）的产物。
`constraint-supplementer` 读 `inputs/supplement_constraints.md` 与已提取
`constraints.json`，产出 JSON 数组，每项：

```json
{
  "op": "add_constraint | replace_constraint",
  "target_platform": "<平台名 | all>",
  "match_expr": "<仅 replace 必填：被替换条目原 expr 精确文本>",
  "proposed": {"expr_type": "...", "expr": "...", "relation_params": ["..."]},
  "basis": "<来自补充文件的依据>"
}
```

`proposed` 只含 `expr_type`/`expr`/`relation_params` 三字段；`src_text`/`origin` 由
`scripts/apply_supplement_constraints.py` 合并时填（`src_text=basis`、`origin="supplement"`），
patch 层字段（`op`/`match_expr`/`proposed`/`basis`）不进 `constraints.json`
（`InterParamConstraint` 为 `extra:forbid`）。合并后重跑 `normalize_constraints` +
`validate_artifacts constraints`，失败则阻断、不进 GENERATE。`target_platform="all"`
的条目由合并器**展开写入 `constraints_in_parameters` 中每个平台桶**（不产生 `common`
桶；`"common"` 已废弃，合并器拒绝并引导改用 `"all"`）。

## conflict_candidates.json / conflict_resolution.json

source-analyst extract 域产 `inputs/conflict_candidates.json`（结构化冲突候选），
用户裁决写 `inputs/conflict_resolution.json`。`scripts/apply_conflict_resolution.py`
join 两者，source-wins 转 `replace_constraint` patch（`origin="conflict_resolution"`），
复用 `apply_supplement_constraints.apply_patch` 合并 + revalidate；doc-wins 丢弃。

`conflict_candidates.json` = JSON 数组，每项：
```json
{
  "conflict_id": "CF1",
  "target_platform": "<平台名|all>",
  "doc_expr": "<constraints.json 中文档提取的原 expr 精确文本>",
  "proposed_source": {"expr_type": "...", "expr": "...", "relation_params": ["..."]},
  "source_location": "...",
  "error_string": "..."
}
```
`conflict_resolution.json` = JSON 数组，每项 `{"conflict_id": "CF1", "winner": "source|doc", "note": ""}`。
`doc_expr` 必须从 `constraints.json` 精确复制，否则合并器精确匹配失败阻断。

## source_raw.json / source_evidence.json

`source_raw.json`（source-analyst 确定性提取，落 `<iter>/`）：`aclnn_interfaces`/
`platform_matrix`（`soc_versions`/`is_reg_base_used`/`dtypes`/`by_file`）/`raw_checks`
（每项 `macro`/`condition`/`error_string`/`source_location`）。浅快照，canndev legacy
漏提标 `missing_evidence`。
`source_evidence.json`（diagnose 域产，落 `<iter>/`）：含 `log_match`（失败日志↔
error_string 模糊匹配命中）/`conflict_pending`（未裁决冲突提示）。

## cases.json

JSON 数组，每项为生成器 CaseConfig 的 model_dump 结果。禁止 Agent 手工伪造。

`cases.json` 是 ATK/TTK 共用的统一具体场景中间模型，也是执行前的紧凑表示。TTK
必须先生成该文件，再由 adapter 产生 `cases_ttk.csv`；禁止直接跳过中间模型硬编码 CSV。
adapter 按 case id 将标量属性的 `range_values` 确定性选择为具体值；Tensor 的
`range_values` 映射为 `input_data_ranges`，具体 Tensor 数据由 TTK 执行期生成。
对于带 `length` 的列表类输入，只保留一个输入
描述，由执行阶段生成 `cases_expanded.json`：

- `range_values` 为标量时，表示列表中每个元素共用该取值规格；
- `range_values` 为列表且长度等于 `length` 时，表示逐元素取值规格；
- 生成阶段不得为了匹配 `length`，在 `ListVar.resolve_model()` 中把标量复制成列表。

诊断用例格式问题时必须同时检查 `cases.json` 和 `cases_expanded.json`。如果紧凑
表示已被正确展开，不能把标量 `range_values` 判为 generator_bug；如果展开过程
本身有误，应归入执行适配层的 executor_bug。

## cases_ttk.csv

仅当 `run_state.test_framework == "ttk"` 时使用。必须具有 `testcase_name`、
`api_name`、`tensor_view_shapes`、`tensor_dtypes`；`api_name` 为非 aclnn 的
`torch_npu.*` E2E API。使用：

`python scripts/validate_artifacts.py ttk_cases <iter>/cases_ttk.csv`

TTK 路径消费统一 `cases.json`，但不得生成或消费 ATK `cases_executor.py/cases_expanded.json`。
同时生成 `ttk_conversion_audit.json`、`golden_manifest.json` 和算子独立 Golden plugin。
manifest 未标记 `verified` 时不得进入远程精度执行，应先调用 `derive-ttk-golden`。

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

root_cause 只能为 constraint_extraction、generator_bug、executor_bug、ttk_adapter、
golden_derivation、execution_environment。每项
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
max_iterations、case_count、mode、server_config、supplement_constraints（可选，整批共享）、
continue_on_error 和有序 operators。
每个 operator 包含原文档绝对路径、PENDING/RUNNING/COMPLETED 状态、单算子 run_id、
run_dir 与 terminal_state。任意时刻最多只能有一个 RUNNING 项。

`batch_summary.json` 是由批次状态确定性生成的只读汇总视图，包含 total、pending、
running、completed、success 和 failed。仅 `SUCCESS` 计入 success；`BLOCKED`、
`MAX_ITERATIONS`、`STOP_GENERATOR_BUG` 和 `STOP_EXECUTOR_BUG` 计入 failed。
