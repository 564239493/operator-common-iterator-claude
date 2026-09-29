---
description: 根据 analysis.json 的所有约束类失败簇，对复制后的 constraints.json 做最小增量更新并记录 constraint_update.json。
---

# 执行反馈约束更新

## 前置条件

- `analysis.json` 已通过 `validate_artifacts.py analysis`；
- `overall_action == UPDATE_CONSTRAINTS`；
- 所有 failure cluster 均为 `constraint_extraction`；
- `constraint_findings` 非空且每项关联 cluster/case/证据；
- 新一轮 constraints 已由 `constraint_update_state.py prepare` 从上一轮实际使用版本复制；
- 不存在未裁决 conflict、engine_error、generator_bug 或 executor_bug。

## 更新边界

1. 只处理 findings 指定的字段和关系，不重新阅读文档生成整份 constraints。
2. 单参数问题直接最小修改对应参数字段；关系问题只增删替换对应关系。
3. 每条 finding 必须由至少一条 change 覆盖；一条 change 可以覆盖多个相关 findings。
4. `constraints_in_parameters` 条目的 `src_txt_line`（文档快照行号数组）是溯源字段：
   未触及语义来源的条目必须原样保留；新增条目必须带该字段（新引用文档条款的行号，
   从 `inputs/` 快照核实）；修改条目表达式但来源条款不变时保持原值，引用了新条款时
   追加/更正对应行号。
4. `before` 必须来自 `.pre_update`，`after` 必须来自更新后的 constraints；basis 必须可追溯。
5. expected_effect 必须说明哪些失败 case 将被拒绝/修正，并检查代表性已通过 case 不被排除。
6. 不允许空改、格式重写、无关数组重排或把特定失败 shape/value 写成黑名单。

## change 记录与知识 skill

每项实际修改写入当前轮 `constraint_update.json.changes`，字段：`id`、`finding_ids`、
`op`、`target`、`before`、`after`、`basis`、`expected_effect`；允许的 op 为
`set_parameter_field`、`add_relation`、`replace_relation`、`remove_relation`、
`update_product_support`。无法安全修改时停止并说明，不得伪造 change。

**知识 skill 按需加载**：finding 涉及量化、NZ/格式、广播、dtype 推导等主题时，先
Skill 加载对应 `aclnn-*` / `torch-npu-*` 知识 skill（description 按信号匹配）再写
修改——skill 载有该类约束的正确表达规则；修改表达与 skill 规则冲突而证据又不足以
推翻时，停止并说明而不是硬改。

## 校验与交接

更新后运行：

```text
python scripts/validate_operator_rule.py <iter>/constraints.json
python scripts/normalize_constraints.py <iter>/constraints.json
python scripts/validate_artifacts.py constraints <iter>/constraints.json
python scripts/diff_constraints_by_id.py <prev-iter>/constraints.json <iter>/constraints.json
```

**id 对应门禁（`diff_constraints_by_id.py`，强制）**：本条命令校验本轮是在上一轮
constraints 基础上原位修改——

- exit 2（`renumbered` 非空，同一份约束内容换了 id）= 违反原位修改约定（重新提取/
  整份重写的特征），必须改回在上轮文件上按 id 最小修改，禁止带病进入 CHECK/REPAIR；
- exit 0 时 `modified`/`added`/`removed` 清单必须能逐条对应到本次
  `constraint_update.json` 的 `changes`（modified 的 id 应出现在某条 change 的
  before/after 中，added 为新分配 id，removed 须有删除依据）；对不上的修改属于越界
  修改，回退后按 finding 范围重做。

**回归校验（强制，最多3次自修正）**：完成上述校验且通过后，必须运行回归校验：

```text
python scripts/validate_constraint_regression.py \
  --cases <prev-iter>/cases.json \
  --execution-result <prev-iter>/execution_result.json \
  --constraints <iter>/constraints.json \
  --output <iter>/regression_check.json \
  --attempt <N> --max-attempts 3
```

其中 `<prev-iter>` 是上一轮实际生成用例所用的 iteration 目录（从 `execution_result.input_artifacts.constraints.path` 反推）。`<N>` 是当前自修正轮次（首次为1）。

**自修正流程**：
- 若脚本返回 exit code 0（`regression_count=0`）→ 无回归，继续。
- 若脚本返回 exit code 1（`regression_count>0` 且 `attempt < max_attempts`）→ 有回归但仍有自修正机会。你必须调整约束使回归用例在新约束下仍合法。调整后重跑校验（从 `<iter>/regression_check.json` 读取 `attempt` 值，+1 后传入 `--attempt`），直至 exit code 0 或 exit code 3。
- 若脚本返回 exit code 3（`attempt >= max_attempts` 且仍有回归）→ **立即停止自修正**，将当前 `constraints.json` 和 `regression_check.json` 交给主协调器，由主协调器弹框让用户选择（继续修改/接受回归/终止）。

回归校验通过后（exit code 0），继续：

```text
python scripts/constraint_update_state.py finalize --report <iter>/constraint_update.json
python scripts/validate_artifacts.py constraint_update <iter>/constraint_update.json
```

随后必须进入独立 CHECK/REPAIR；只有 checker 通过后才能 GENERATE。
