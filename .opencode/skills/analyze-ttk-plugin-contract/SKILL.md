---
name: analyze-ttk-plugin-contract
description: 读 TTK --plugin 插件源码，核对值属性契约、场景属性与插件派生约束建议，供 plugin-contract-analyst 使用。
---

# 插件契约核对规范

## 输入

- `inputs/ttk_plugin_snapshot/`（只读插件快照）；
- `inputs/plugin_requirements.json`（静态分析产物，schema 1.1）；
- `inputs/value_constraints.candidates.json`；
- 算子文档快照与 `inputs/scene_directive.md`；
- 当前 `run_state.json`（只读）。

## 步骤

1. **读入口绑定**：`spec.py` 的 `__spec__` 找到目标算子对应的 Spec 类，
   沿 `customize_inputs`/`golden` 找到实际输入构造模块。
2. **枚举真实取值通道**：
   - `kwargs.get(...)` / `kwargs[...]` 的直接 key；
   - `f"{name}<suffix>"` 模板 + 函数参数 + 调用点字面量（helper 链）；
   - 常量元组循环（如 `_VECTOR_NAMES`）展开；
   - sidecar/私有协议（如 `MANUAL_DATA_DIRS`）单独标注；
   - `AUX_TARGETS` 等显式映射优先。
3. **对照静态产物**：逐条核对 `plugin_requirements.json.required_value_attrs`
   与 `required_attributes`：
   - 名称、target_param、suffix/模板是否与源码一致；
   - 是否漏掉非 `_values` 名称（如 `template_run_mode`、`group_list`）；
   - `confidence=unresolved` 的条目是否有可解析的调用点/常量。
4. **场景属性**：判断哪些属性是插件运行必需（如 `template_run_mode`、
   `*_kv_topk_mode`），哪些有安全默认值（如 `has_cmp_kv`/`has_ori_kv`），
   并标注生成侧是否已有注入（看 `plugin_case_contract_report.json` 或生成代码契约）。
5. **参数级约束建议**：从插件语义中提取可由 constraints 表达的规则，至少检查：
   - 模板与 `cmpRatio`/`cmpMaskMode` 的联动；
   - dtype 一致性（含输出）；
   - presence 反向（blockTable/cuSeqlens/sequsedCmpKv/cmpSparseIndices/oriSparseIndices）；
   - sparse indices 的 rank/轴；
   - PA 容量与稠密 cmp 的序列长度派生。
   每条写 `expr` 建议、依据 `plugin: file:line`、以及是否已有等价约束。
6. **写产物** `inputs/plugin_contract_review.json`：

```json
{
  "schema_version": "1.0",
  "plugin_sha256": "<与 plugin_requirements 一致>",
  "confirmed_value_attrs": [
    {"name": "...", "target_param": "...", "evidence": "impl/inputs.py:201", "status": "confirmed|corrected|missing"}
  ],
  "scenario_attributes": [
    {"name": "template_run_mode", "required": true, "evidence": "impl/inputs.py:171",
     "generator_coverage": "apply_plugin_case_contract|none",
     "note": "SWA/CSA/HCA 显式路由"}
  ],
  "constraint_suggestions": [
    {"expr": "...", "relation_params": ["..."], "evidence": "plugin: ...", "already_present": false}
  ],
  "gaps": [
    {"item": "...", "reason": "无法从源码确认", "evidence": "..."}
  ]
}
```

## 纪律

- 只读快照，不 import/执行插件；不修改 constraints、候选、cases、CSV。
- 每条结论必须有 `file:line` 或明确标注 gap；不得用算子名/参数名猜测。
- 与文档冲突时保留双方证据，交 constraint-checker 裁决；不自行选边。
- `gaps` 非空不会自动阻断，但必须逐条说明影响面。
