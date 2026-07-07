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
(处理一对多,过滤 aclnnInner)、`raw_checks`(OP_CHECK_IF/CHECK_COND 的条件+错误串+源码位置)。

## 第二步:LLM 判读(业务推理,本 agent 核心)

对 `raw_checks` 做语义判读,产出 `hard_constraints`:

- 把每条 `raw_checks[*].condition` 归类为 `expr_type`,**复用**
  `agent.generators.common_model_definition` 的 `InterConstraintsRuleType` 枚举:
  `shape_value_dependency`(如 `q.shape[-1] <= 1024`)、`value_dependency`、
  `shape_dependency`(如 `len(q.shape) in (3,4)`)、`type_equality`、`shape_equality` 等。
- 产出对齐**当前约束提取提示词(v3) §6** `expr` 语法的 Python 表达式,`relation_params` 列出涉及参数。
- 保留 `constraint_id`、`source_location`、`error_string`、原始 condition 作 `src_text`。
- **过滤非约束性检查**:nullptr 检查、纯日志打印、`GRAPH_SUCCESS` 返回判断不产出硬约束。

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
`mismatch_overnarrow`)、`doc_error`(源码否决文档的条目列表,可为空)。仅上述六字段,
不产诊断/预检类额外产物。

## 边界

- 不改 constraints/cases/源码,不进 patch 子循环,不参与失败诊断。
- `hard_constraints.expr_type` 必须属 `InterConstraintsRuleType` 枚举,`expr` 对齐
  当前提示词(v3) §6。
- 只读源码快照(项目内 `operator_src_snapshot`),不触外部源码树。只写
  `source_evidence.json` + `constraints_patch.json` 到当前 iter 目录。
- 证据不足时在 `source_evidence` 标注 `missing_evidence`,不猜测。
