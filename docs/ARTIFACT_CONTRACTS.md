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
    scene_scan.json                    # 可选：scene-scanner 产，设备→量化模板→特性参数三级嵌套
    scene_directive.md                 # 可选：render_scene_directive.py 渲染（含 param_modes/selection_policy/known_conflicts 机读块），extractor 据此适配
    scene_conflicts.json               # 可选：check_scene_conflicts.py 产，Q3 后特性参数冲突识别报告（advisory，allow-continue）
    selection.json                     # 可选：主协调器 Q1/Q2/Q3 答案汇总（值级），render_scene_directive.py 据此渲染 directive
  iter_001/
    constraints.json
    constraint_check.json              # 本轮最终约束的语义检查/修复累计报告
    constraints.json.pre_supplement    # 可选：合并补充前的 EXTRACT 原始备份（每轮覆盖）
    constraints.json.pre_conflict      # 可选：冲突合并前备份
    constraints_patch.json             # 可选：约束补充阶段产出的 add/replace patch
    source_raw.json                    # 可选：source-analyst 确定性提取的源码事实
    source_evidence.json               # 可选：source-analyst diagnose 域产（match 与 confirmed 分离）
    supplement_additions.md            # 仅旧 schema 2.0 run 兼容；2.1 不再生成
    generation_summary.json
    cases.json
    cases_ttk.csv
    execution_result.json
    ttk_artifacts/plog/                 # TTK E2E：本次执行 PLOG
      manifest.json
      error_summary.log                 # 远端 grep -rn ERROR 完整输出
      raw/                              # 解包后的原始 PLOG
    ttk_aclnn_artifacts/plog/           # TTK ACLNN：结构同上
    quality_gate.json
    analysis.json
  iter_002/                           # 执行反馈轮示例
    constraints.json                  # 从上一轮实际用例所用版本复制后最小修改
    constraints.json.pre_update       # updater 修改前的不可变基线
    constraint_update.json            # finding/change/hash 审计
    constraint_check.json
```

## run_state.json

必须包含 `run_id`、`operator_doc_source`、`operator_doc`、`operator_src_source`、`operator_src_snapshot`、
`current_prompt_source`、`current_prompt`、
`current_prompt_modules`、`source_analysis_knowledge`、`supplement_constraints_source`、`supplement_constraints`、`mode`、
`server_config`、`max_iterations`、`constraint_check`、`case_count`、`human_checkpoint_round`、
`human_checkpoint_resolved_iteration`、
`supplement_revision`、`supplement_hash`、`last_consumed_supplement_hash`、`supplement_updated_iteration`、
`operator_family`、`test_framework`、
`hs_scenario_mode`、`run_scope`、`scene`、`current_iteration`、`state`、`history` 和时间戳。

`operator_doc_source` 可以指向项目外部，只允许读取；`operator_doc` 必须指向 run 目录内的快照，后续 Agent 只使用快照。

`supplement_constraints_source` 可指向项目外部的补充约束 Markdown（可选，未提供时为空串）；
`supplement_constraints` 指向 run 内 `inputs/supplement_constraints.md` 快照。为空串时跳过 约束补充阶段，回退纯文档驱动流程。
`supplement_revision/hash` 对 `supplementary-doc.md` 与 `supplement_constraints.md` 的 非空内容做持久化版本记录；
`last_consumed_supplement_hash` 只在 SUPPLEMENT 合并及检查 成功后更新。
人工或诊断追加后必须运行 `update_supplement_state.py`，不能依赖会话记忆 判断“刚刚 append”。

`current_prompt_source` 指向项目内当前 family 的基线：
ACLNN 默认基础提示词为`prompts/operator_constraints/base.md`，
运行时结合 `knowledge/aclnn/manifest.json` 路由装配。
torch_npu 为`prompts/torch_npu_constraints/base.md`；
`current_prompt` 指向 run 内 `inputs/prompt_v1.md` 完整快照。

ACLNN selector 先生成 `prompt_preanalysis.json` 与适用性判断，再追加 当前文档命中的知识模块并写 `prompt_assembly.json`；

默认（未传 `--prompt`）时，
ACLNN 由 `scripts/select_prompt.py` 装配 `knowledge/aclnn/manifest.json` 中的模块；
torch_npu 由 `scripts/select_torch_npu_prompt.py` 装配`knowledge/torch_npu/**/*.md`。
两个选择器不扫描对方的根目录。
`current_prompt_modules` 记录命中的模块名清单（torch_npu 始终含`common/documentation_conventions`）；

显式 `--prompt` 时，原样复制指定文件、`current_prompt_modules=[]`。
constraint-extractor 始终只读 `current_prompt` 快照， 不感知装配过程。

`source_analysis_knowledge` 默认为 `false`。仅在 ACLNN 初始化显式传入`--source-analysis-knowledge` 时为 `true`；
此时仍须通过模块自身的 `operator_name_eq` 精准命中才会进入 `current_prompt_modules`。
开关状态与命中/拒绝证据同时冻结在 `prompt_assembly.json`。执行反馈轮不重新提取，也不得动态改变该装配记录。

`constraint_check={max_rounds, iteration, current_round, status, report}` 保存当前约束版本的内部检查进度。
`max_rounds` 默认 3；首轮 EXTRACT 或 UPDATE_CONSTRAINTS 创建新 iteration 时重置其他字段并保留上限。
同 iteration 恢复时不得重置已通过结果。

`hs_scenario_mode` 为 `original` 或 `planned`，默认 `original`。它只影响 torch_npu + TTK 的 GENERATE：
`original` 使用原生生成器，`planned` 才启用 TND/BSND/paged-attention 场景拆分和投影。
case-generator 必须从 run_state 透传该值。

state 状态合法值见 WORKFLOW.md §3 状态机。状态迁移操作见 iterate-operator/SKILL.md。

## constraints.json

必须满足 `agent.generators.common_model_definition.OperatorRule`。
关键字段包括 operator_name、product_support、parameters 和 constraints_in_parameters。
每个约束应来自已冻结的有效事实源，不用聊天内容补充。

每条约束带 `origin` 字段：
`doc`（文档提取）、
`source_analysis`（显式启用且精准命中的锁定源码分析知识）
`supplement`（约束补充阶段合并，约束补充阶段产出的 `constraints_patch.json` 经 `scripts/apply_supplement_constraints.py`
确定性合并后追加/替换条目并标 `origin="supplement"`。）

`allowed_range_value.value` 非空时：
`type` 必须显式标注为 `enum`（离散枚举，如 格式码/bool/字符串候选）或 `range`（数值区间）；
缺失或非法值由 `scripts/validate_artifacts.py` 的 `validate_constraints` 兜底报错并阻断流程。
`value=[]`（空）时：
不强制 `type`（tensor 参数无值域约束常留空）。

被 validate_artifacts.py constraints 校验（构造 OperatorRule Pydantic 模型）。
由 constraint-extractor 产出、 constraint-repairer/updater 修改。

## constraint_check.json

每个新约束版本生成一个累计报告：
首轮在 SUPPLEMENT/已裁决 conflict 合并后生成，
后续在 constraint-updater 完成最小更新后生成。
只保留当前轮单文件，不创建逐 check 轮目录。

最小结构如下：

```json
{
  "schema_version": "1.0",
  "iteration": 1,
  "max_rounds": 3,
  "current_round": 1,
  "status": "needs_repair",
  "constraints_file": "<constraints.json 绝对路径>",
  "issues": [
    {
      "id": "CR-001",
      "found_round": 1,
      "last_checked_round": 1,
      "line": 86,
      "constraint": "groupType.allowed_range_value",
      "problem": "当前连续范围与文档有限枚举冲突。",
      "suggestion": "改为 enum [0,1]。",
      "status": "open"
    }
  ],
  "summary": {
    "total": 1,
    "open": 1,
    "fixed": 0,
    "unfixed": 0
  }
}
```

`status` 只能为 `passed|needs_repair|failed`，
issue status 只能为`open|fixed|unfixed`。
只有 constraint-checker 能确认 fixed；
constraint-repairer 不修改报告。
`passed` 不得含 active issue；
`needs_repair` 要求尚未到上限；
`failed` 要求已到 上限且仍有 active issue。

被 validate_artifacts.py constraint_check 校验，使用：
`python scripts/validate_artifacts.py constraint_check <iter>/constraint_check.json`
由 constraint-checker 产出，constraint-repairer 不修改此文件。
状态推进操作见 iterate-operator/SKILL.md 步骤 6。
// TODO 待skill优化后调整这里

## constraint_update.json

仅用于执行用例结束，执行结果分析后根据分析结果进行约束修改。
`constraint_update_state.py prepare` 先验证上一轮 analysis 的`overall_action=UPDATE_CONSTRAINTS`，
把上一轮实际生成用例所用的 constraints 复制到新 iteration，同时写 `.pre_update` 与 pending 报告；
`constraint-updater` 修改目标文件并填写 changes，最后由 `finalize` 校验。

核心结构如下：

```json
{
  "schema_version": "1.0",
  "status": "updated",
  "source_constraints": "<上一轮 constraints 绝对路径>",
  "target_constraints": "<新一轮 constraints 绝对路径>",
  "pre_update_constraints": "<新一轮 constraints.json.pre_update>",
  "analysis_file": "<上一轮 analysis.json>",
  "execution_result": "<上一轮 execution_result.json>",
  "base_sha256": "...",
  "result_sha256": "...",
  "finding_ids": ["CF-001"],
  "changes": [{
    "id": "CU-001",
    "finding_ids": ["CF-001"],
    "op": "set_parameter_field",
    "target": "parameters.axis.allowed_range_value",
    "before": {"type": "range", "value": [0, 7]},
    "after": {"type": "enum", "value": [0, 1]},
    "basis": "失败证据与算子文档共同表明 axis 仅支持 0/1",
    "expected_effect": "case_007 在生成阶段被拒绝"
  }]
}
```

允许的 op 为 `set_parameter_field|add_relation|replace_relation|remove_relation|update_product_support`。
所有 findings 必须至少被一项 change 覆盖，不得引用未知 finding；
`before` 与 `after` 必须不同，结果 hash 必须区别于基线。
该报告只证明“发生了可追踪修改”，不能替代后续独立 constraint-checker 的语义结论。

被 `validate_artifacts.py constraint_update` 校验，使用：
`python scripts/validate_artifacts.py constraint_update <iter>/constraint_update.json`
由 `constraint-updater` 产出（`constraint_update_state.py prepare/finalize` 管理生命周期），
`constraint-checker` 消费（验证 findings 覆盖与 expected_effect）。

## constraints_patch.json

约束补充阶段（条件触发，`inputs/supplementary-doc.md` 或`inputs/supplement_constraints.md` 任一非空时执行，两者都空则跳过）的产物。
`constraint-supplementer` 读补充输入与已提取的 `constraints.json`，产出 JSON 数组，每项数据结构如下：

```json
{
  "op": "add_constraint | replace_constraint",
  "target_platform": "<平台名 | all>",
  "match_expr": "<仅 replace 必填：被替换条目原 expr 精确文本>",
  "proposed": {"expr_type": "...", "expr": "...", "relation_params": ["..."]},
  "basis": "<来自补充文件的可追溯依据>",
  "finding_ids": ["CF-001"],
  "expected_effect": "case_001 应被该约束拒绝"
}
```

`proposed` 只含 `expr_type`/`expr`/`relation_params` 三字段；
`src_text`/`origin` 由 `scripts/apply_supplement_constraints.py` 合并时填（`src_text=basis`、`origin="supplement"`），
patch 层字段（`op`/`match_expr`/`proposed`/`basis`/`finding_ids`/`expected_effect`）不进 `constraints.json`
（`InterParamConstraint` 为 `extra:forbid`）。
合并后重跑 `normalize_constraints` + `validate_artifacts constraints`，失败则阻断、不进 GENERATE。
`target_platform="all"` 的条目由合并器**展开写入 `constraints_in_parameters` 中每个平台桶**（不产生 `common`桶；`"common"`
已废弃，合并器拒绝并引导改用 `"all"`）。
`add_constraint` 按规范化后的 `expr_type+expr+relation_params` 幂等去重；
等价项返回`noop-add`。
replace 先精确匹配 `match_expr`，再按表达式 AST 规范化匹配；等价替换返回`noop-replace`。
空 patch/noop 合法，但不得作为约束已经提升的证据。
patch 先通过`validate_artifacts.py constraints_patch`；
诊断 finding 还须通过`validate_supplement_effect.py` 的覆盖和非全量 noop 门禁。

被 `validate_artifacts.py constraints_patch` 校验（诊断 patch 还须过 `validate_supplement_effect.py`）；
由 `constraint-supplementer` 产出，
`apply_supplement_constraints.py` 消费（合并写入 constraints.json），
`constraint-checker` 消费（验证 finding 覆盖与 expected_effect）。

# conflict 段拆分后完整替换内容

> 替换范围：ARTIFACT_CONTRACTS.md 中从
> `## conflict_candidates.json / conflict_resolution.json`
> 到
> `## scene_scan.json / scene_directive.md / run_state.scene`
> 之前的全部内容。
> 即删除旧的合并段，粘贴下面的两个独立段。

---

## conflict_candidates.json

source-analyst extract 产出 `inputs/conflict_candidates.json`（结构化冲突候选）。
JSON 数组，每项：

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

`doc_expr` 必须从 `constraints.json` 精确复制（不得改写），否则合并器精确匹配失败阻断。
`proposed_source` 含 `expr_type`/`expr`/`relation_params` 三字段，语义同 `constraints_patch.json` 的 `proposed`。

被 `apply_conflict_resolution.py` 消费
（与 `conflict_resolution.json` join：source-wins 转 `replace_constraint` patch，`origin="conflict_resolution"`，
复用 `apply_supplement_constraints.apply_patch` 合并 + revalidate；doc-wins 丢弃）；
由 `source-analyst`（extract 域）产出。
无独立结构校验命令——合并器对 `doc_expr` 精确匹配失败时阻断（exit 非 0），是唯一的结构门禁。

## conflict_resolution.json

用户人工裁决写 `inputs/conflict_resolution.json`。
JSON 数组，每项：
`{"conflict_id": "CF1", "winner": "source|doc", "note": ""}`。
`conflict_id` 须与 `conflict_candidates.json` 中的条目对应。

冲突永远走人工通道，不由 checker 或其他 Agent 自动选边。
用户在任意时刻写入此文件后，下一轮 re-supplement 前由`apply_conflict_resolution.py` 合并；
source-wins 的条目转为 `replace_constraint` patch 并入 `constraints.json`（`origin="conflict_resolution"`），doc-wins
的条目丢弃。

被 `apply_conflict_resolution.py` 消费（与 `conflict_candidates.json` join）；
由用户人工产出。无独立结构校验命令——合并器对`conflict_id` 匹配和 `winner` 取值处理是唯一门禁。

## scene_scan.json

`scene-scanner` 产 `<run-dir>/inputs/scene_scan.json`（调度时必须显式传入 `<run-dir>`，不得按仓库 cwd 解析相对 `inputs/`）。
按**设备类型 → 量化模板 → 特性参数**三级嵌套提取文档中有测试需求的场景，**禁止输出"通用"设备类型**
（无明确设备标注的内容，需要分别记录到每个具体设备组下）。
顶层含 `operator`/`has_scenarios`/`device_types`（"产品支持情况"具体设备名）/`devices[]`/`scan_notes`；
每设备 `device`/`templates[]`，
每模板 `template`/`definition`/`unsupported_features`/`feature_params[]`，
每特性 `feature`/`params[]`，
每参数 `name`/`values`/`description`/`constraint`/`related`/`value_conflicts`（可选）。

完整 schema、字段语义、JSON 示例见 `prompts/scan_scenes.md` §4。
提取规则见 `scan-scenes` skill 和 `prompts/scan_scenes.md` §3-§5。

被 `validate_artifacts.py scene_scan` 校验（该函数是 schema 的唯一强制真相源，校验 `has_scenarios`/`device_types`/
`devices[].device` 派生一致性/`value_conflicts` 结构合法性）；
由 `scene-scanner` 产出；主协调器消费（据此 Q1→Q2→Q3 三轮征询，征询协议见 `iterate-operator/SKILL.md` 步骤 5）。

## selection.json

主协调器 Q1→Q2→Q3 答案汇总落地到 `<run-dir>/inputs/selection.json`。
形态 `{device_types, selection}`，
`selection` 为`{device: {template: <tpl_value>}}`，
`<tpl_value>` ∈ `null`（保持自动）| `"fix_all_default"` | `{param:[values]}`（Other 用户输入，主协调器识别组装）。

供 `render_scene_directive.py` 解析 `param_modes` 和 `check_scene_conflicts.py` 做冲突识别。
无独立结构校验命令——`render_scene_directive.py` 和 `check_scene_conflicts.py` 对其内容的解析是唯一门禁。由主协调器产出。

## scene_directive.md

`render_scene_directive.py` 渲染，落 `<run-dir>/inputs/scene_directive.md`（仅 `scope=subset`时存在）。
逐设备逐模板列出选定模板及特性参数取值，末尾附机读块
`<!-- scene: {device_types, selection, param_modes, selection_policy, known_conflicts} -->`。
`param_modes[device][param]` ∈ `{"expand": [取值清单]}` | `{"fix": X}`；
缺键 = 按文档和已选场景自动适配，已选场景禁止的 Optional 参数显式生成 `param is None`。

`constraint-extractor` 消费（按 `param_modes` 收窄 `allowed_range_value`，按 `device_types` 收窄 `product_support`，消费规则见
`extract-constraints` skill 场景屏蔽规则段）；
执行反馈轮不改本文件，`constraint-updater` 必须遵守同一 directive 保持跨轮稳定。

被 `render_scene_directive.py` 渲染时校验（设备/模板/param 名/值 ∈ scan、解析 `param_modes`、非法选择 exit 2 阻断）；
由`render_scene_directive.py` 产出（从 `scene_scan.json` + `selection.json` 渲染），
`constraint-extractor`消费。无独立结构校验命令——渲染器的校验是唯一门禁。

## run_state.scene

`init_run` 写 `null`，SCENE_SCAN 子步骤由 `render_scene_directive.py` 回写。
形态`{enabled, scope, device_types, selection, param_modes, selection_policy, known_conflicts, directive, scan}`。
`scope=subset` 时 `directive` 指向 `inputs/scene_directive.md`；
`scope=all` 全设备全模板全特性参数全展开（不剪枝，不写 directive 文件）；
`scope=off` 时 `enabled=false`、`directive=""` 且不写 directive 文件（extractor 见无 directive 即按全场景提取，行为不变）。
纯无场景算子（`has_scenarios=false`）不触发征询，`run_state.scene=null`。

无独立结构校验命令——`render_scene_directive.py` 写入时校验合法性，`init_run` 读取时校验初始 `null`。由
`render_scene_directive.py` 回写。

## source_raw.json / source_evidence.json

`source_raw.json`（source-analyst 确定性提取，落 `<iter>/`）：`aclnn_interfaces`/
`platform_matrix`（`soc_versions`/`is_reg_base_used`/`dtypes`/`by_file`）/`raw_checks`
（每项 `macro`/`condition`/`error_string`/`source_location`）。浅快照，canndev legacy
漏提标 `missing_evidence`。
`source_evidence.json`（diagnose 域产，落 `<iter>/`）：含 `log_match`（失败日志↔
error_string 模糊匹配命中）、`confirmed_additions`、`confirmed_additions_count`、
`missing_evidence`、`conflict_pending`。`log_match` 只是线索；只有经过确认并已追加到
`supplementary-doc.md` 的事实才能进入 `confirmed_additions`。两者允许一方非空、另一方为空。

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
`api_name`、`tensor_view_shapes`、`tensor_dtypes`。`api_name=aclnn*` 时使用 TTK
ACLNN 模式；`api_name=torch_npu.*` 时使用 TTK E2E 模式。使用：

`python scripts/validate_artifacts.py ttk_cases <iter>/cases_ttk.csv`

TTK 路径消费统一 `cases.json`，但不得生成或消费 ATK `cases_executor.py/cases_expanded.json`。
所有 TTK 模式都生成 `ttk_conversion_audit.json`。只有 torch_npu/E2E 生成
`golden_manifest.json` 和算子独立 Golden plugin；manifest 未标记 `verified` 时不得进入
远程精度执行。ACLNN 调用原生 `ttk aclnn`，不要求 E2E Golden plugin/manifest。

torch_npu/TTK 在转换前必须按所选平台逐条执行 `constraints_in_parameters`；HS 手写专项
检查只能作为 schema 无法表达内容的补充，不能代替完整关系复核。任一硬关系为 false、
无法求值、TTK positional self-check 失败或转换审计存在 case issue 时，生成阶段必须
fail-closed，不得产出可执行成功结论。

当前 TTK CSV 只支持 `input_data_ranges`，不能无损表达动态前缀和、单调序列和有效/无效
索引排序。适配器必须在 `ttk_conversion_audit.json` 与 `generation_summary.json` 记录
`content_generation_mode`/`content_generation_limitations`。对需要这些内容语义的算子，
只能生成适配器能够证明正确的受限场景（当前 kv quant sparse attention 为精确 B=1），
不能把随机范围伪装成多元素前缀和支持。

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
  "engine_error": "",
  "input_artifacts": {
    "constraints": {"path": "<绝对路径>", "sha256": "...", "size": 0, "mtime_ns": 0}
  }
}
```

必须满足 passed + failed = total。engine_error 非空时不能宣称业务成功。所有实际执行模式
都必须在 `input_artifacts` 冻结 constraints/cases/generation_summary 的路径与 sha256；后续
UPDATE_CONSTRAINTS 只允许复制这里记录且哈希仍一致的 constraints，防止基于错误版本修改。

真实 TTK E2E/ACLNN NPU 执行还必须包含 `plog`：

```json
{
  "status": "collected | missing | error | disabled | not_attempted",
  "remote_plog_dir": "/root/ascend/log/debug",
  "local_dir": "<artifact>/plog",
  "raw_dir": "<artifact>/plog/raw",
  "error_summary": "<artifact>/plog/error_summary.log",
  "manifest": "<artifact>/plog/manifest.json",
  "file_count": 12,
  "error_count": 3,
  "collection_error": ""
}
```

`collected` 时四个本地路径必须存在。`missing|error` 必须给出 collection_error，但不能把
收集失败伪装成算子 case fail。PLOG 在远端打包前执行 `grep -rn ERROR`，完整摘要与原始
日志一起同步；failure-analyst 必须同时使用 TTK 日志和 PLOG，不能只看其一。

## analysis.json

failure-analyst 保持三大类根因：`constraint_extraction`、`generator_bug`、
`executor_bug`。每项 specific_issues 应关联 case id、日志或文档证据。

新产物使用 `schema_version="2.1"`，并包含按错误签名聚合的全部 `failure_clusters`、
每簇 `recommended_action`、有明确证据且包含 `suggested_change` 的 `constraint_findings`、
`root_cause_summary` 与唯一 `overall_action`。finding 必须用 `cluster_ids` 关联其覆盖的约束簇。
顶层 `root_cause` 只保留给旧消费者，不参与自动路由。

`overall_action` 聚合规则固定：全部约束簇且 findings 覆盖完整 → `UPDATE_CONSTRAINTS`；
全部 generator/executor → `STOP_GENERATOR_BUG`/`STOP_EXECUTOR_BUG`；两类及以上根因 →
`MIXED_FAILURE_REVIEW`；全部约束簇但 findings 不完整 → `NEEDS_HUMAN_EVIDENCE`。
validator 必须从 clusters 重算 summary 和 action，禁止 Agent 自报 action 绕过混合根因门禁。
旧 run 的 schema 2.0 和无 schema 产物保持只读兼容，但不能获得 2.1 的自动更新路由资格。

schema 2.1 不再生成 `supplement_additions.md`；问题、修复建议在 analysis findings 中记录，
实际修改与状态在下一轮 `constraint_update.json` 中记录。`merge_supplement_additions.py`
仅保留用于读取/迁移旧 schema 2.0 run。

## quality_gate.json

至少包含 status、checks、blocking_issues、next_state。blocking_issues 非空时
status 必须为 blocked，主协调器不得越过门禁。门禁必须重新校验当前 iteration 的
`constraint_check.json`，并确认 `status=passed` 且轮次与 run_state 一致。

## 目录批次产物

```text
runs/batches/<batch-id>/
  batch_state.json
  batch_summary.json
```

`batch_state.json` 必须冻结 source_directory、glob、recursive、prompt、
`prompt_explicit`、`prompt_sources`、operator_family、test_framework、max_iterations、
case_count、constraint_check_rounds、mode、server_config、supplement_constraints（可选，整批共享）、
continue_on_error 和有序 operators。`prompt` 只在用户显式指定原样 prompt 时非空；
自动模式通过 `prompt_sources` 记录初始化时可用的各 family baseline，并让每个单算子
`init_run` 自行选择/装配，防止混合目录把一个 family 的 prompt 传给另一个 family。
每个 operator 包含原文档绝对路径、PENDING/RUNNING/COMPLETED 状态、单算子 run_id、
run_dir 与 terminal_state。任意时刻最多只能有一个 RUNNING 项。

`batch_summary.json` 是由批次状态确定性生成的只读汇总视图，包含 total、pending、
running、completed、success 和 failed。仅 `SUCCESS` 计入 success；`BLOCKED`、
`MAX_ITERATIONS`、`STOP_GENERATOR_BUG`、`STOP_EXECUTOR_BUG` 和 `STOPPED_BY_USER` 计入 failed。
