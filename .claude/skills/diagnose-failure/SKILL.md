---
description: 基于落盘证据将失败分类为 constraint_extraction、generator_bug 或 executor_bug。
---

# 失败诊断规范

按顺序读取当前提示词、原始文档、constraints.json、cases.json、存在时的
cases_expanded.json、execution_result.json。真实 TTK 执行还必须读取
`execution_result.plog.manifest`、`execution_result.plog.error_summary`，需要上下文时再读
`plog.raw_dir` 中对应原始日志。先检查 engine_error，再检查生成用例是否违反已提取约束，
最后检查约束是否遗漏或误解文档。

PLOG 分析规则：

- `error_summary.log` 是远端对本次清理后 PLOG 执行 `grep -rn ERROR` 的完整结果；引用时保留
  文件路径、行号、错误码和关键上下文。
- 将 PLOG 时间/设备/算子错误与具体失败 case、TTK result 行、stdout/stderr 对齐；无法对齐的
  历史或后台噪声不得作为 constraint finding。
- 参数、shape、dtype、format、tiling 校验错误只有在与文档/constraints 对照后才能归因；
  设备掉卡、内存损坏、通信/驱动/CANN 环境故障有直接 PLOG 证据时归 executor_bug。
- `plog.status=missing|error` 不是算子根因，只是诊断证据缺口，必须写入 specific_issues。

ACLNN 还必须读取 `prompt_preanalysis.json` 与 `prompt_assembly.json`：区分“模块未被
路由”“模块已加载但适用性判断/规则不足”“提取器未执行已加载规则”三种原因。分析中
记录相关 module_id 和命中证据，供沉淀时选择 common/feature/exact operator 目的地。

先读取 `run_state.operator_family`，诊断规则与当前 family 快照保持隔离。不得用 ACLNN
prompt/module 解释 torch_npu 失败，也不得反向移植 torch_npu 专项知识。

在归类 `generator_bug` 前，必须先完成约束语义与表达式检查：

- 将参数功能描述和取值说明合并阅读，检查是否漏掉当前文档和当前 family prompt 允许
  形式化的语义约束。`epsilon`/`eps` 的严格正值推导仅在 `operator_family=aclnn`
  且 ACLNN 快照明确允许时使用；torch_npu 不得从参数用途推导文档未写的合法域。
  “建议上界”在任何 family 都不是硬上界。
- `allowed_range_value.type=range` 不允许以 `null` 充当数值边界；
  `type=enum` 仅在文档明确允许未传/null 时才允许 `null` 候选；默认值本身不等于
  合法值域。
- `expr` 中裸 `null` 合法，按 Python `None` 解释，但只能用于空值/存在性判断，
  不能参与数值大小比较。
- 数值范围必须写为不等式，`.range_value in [[min, max]]` 属于提取表达错误。
- 只要约束遗漏、语义误解或表达式不合法足以解释失败，主根因应归为
  `constraint_extraction`；生成器没有友好报错可记录在 `generator_issue`，但不能
  因此覆盖上游主因。

还必须核对生成阶段和执行阶段的数据边界：

- `cases.json` 是紧凑表示；列表类输入以单个输入描述加 `length` 表示，展开由执行
  阶段生成 `cases_expanded.json` 完成。
- 带 `length` 的输入允许 `range_values` 为标量，语义是每个元素共用该取值规格。
  不得仅凭 `range_values` 是标量就判定 generator_bug，也不得建议在
  `ListVar.resolve_model()` 中按 `length`/`seq_len` 复制为列表。
- 必须对照同一 case 在 `cases.json` 与 `cases_expanded.json` 中的表示，并结合
  异常栈确认实际失败参数。紧凑表示已正确展开时，应继续查找真实根因；展开逻辑
  本身错误时归为 executor_bug。

写 `analysis.json`：

```json
{
  "schema_version": "2.1",
  "root_cause": "constraint_extraction | generator_bug | executor_bug",
  "analysis": "根因摘要",
  "specific_issues": ["带 case id 或文档证据的问题"],
  "failure_clusters": [{
    "id": "FC-001",
    "signature": "稳定的失败语义签名",
    "case_ids": ["case_001"],
    "root_cause": "constraint_extraction",
    "evidence": [{"source": "execution_result", "detail": "具体错误"}],
    "recommended_action": "UPDATE_CONSTRAINTS"
  }],
  "constraint_findings": [{
    "id": "CF-001",
    "kind": "missing",
    "fact": "可直接形式化的约束事实",
    "suggested_change": "对现有约束执行的最小修改建议",
    "affected_params": ["x"],
    "case_ids": ["case_001"],
    "cluster_ids": ["FC-001"],
    "evidence": [{"source": "operator_doc", "detail": "条款定位"}],
    "confidence": 0.9,
    "expected_effect": "case_001 应被新增约束拒绝"
  }],
  "supplement_decision": {
    "has_explicit_additions": true,
    "source": "diagnose_inferred",
    "reason": "存在可追溯的新约束事实"
  },
  "prompt_optimization": {"eligible": false, "reason": "优先消费明确补充"},
  "root_cause_summary": {
    "constraint_extraction": {"clusters": 1, "cases": 1},
    "generator_bug": {"clusters": 0, "cases": 0},
    "executor_bug": {"clusters": 0, "cases": 0}
  },
  "overall_action": "UPDATE_CONSTRAINTS",
  "modified_sections": [],
  "generator_issue": "",
  "executor_issue": ""
}
```

**源码证据与两级补救**（当 `run_state.operator_src_snapshot` 非空）：
- 读 `<iter-dir>/source_evidence.json`（source-analyst diagnose 域产）。它已把
  error_string 命中且确认成功的 uncertain 关系追加到 `inputs/supplementary-doc.md`，并给出
  `suggested_root_cause`（仅供参考，最终根因仍由本 agent 下）。
- root_cause=constraint_extraction 时两级补救：
  1. `source_evidence.confirmed_additions_count > 0`（补充已确认且已落库）→ analysis 标注
     "补充已扩充，直接 UPDATE_CONSTRAINTS"，逐条映射成同 id 的
     `constraint_findings` 并关联 cluster，置 decision source 为 `source_confirmed`，
     **不重新 EXTRACT**；
     不重新生成补充文件；finding 直接交给 constraint-updater。
  2. 没有 confirmed additions → 根据错误日志 + 原算子文档推约束关系；只有能写出
     结构化 `constraint_findings` 时才允许进入 UPDATE_CONSTRAINTS，不另产
     `supplement_additions.md`。推不出明确约束时，仅当能定位当前
     Prompt 的具体规则缺口可置 `prompt_optimization.eligible=true` 供离线沉淀，但当前任务
     仍请求人工补充，不重新 EXTRACT。
- 读 `inputs/conflict-doc.md` + `inputs/conflict_resolution.json`：失败命中未裁决
  conflict → `specific_issues` 提示用户先裁决（冲突永远走人工通道）。

证据不足时不得猜测，也不得仅因无法证明约束问题就归入 executor_bug。只有执行展开、
调用、环境或异常栈有直接证据时才能判 executor_bug；否则保持三分类不变的前提下，
在 `specific_issues` 列出缺失证据，置 supplement/prompt 两个决策均不可执行并请求人工补充。
