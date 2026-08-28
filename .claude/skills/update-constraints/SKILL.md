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
4. `before` 必须来自 `.pre_update`，`after` 必须来自更新后的 constraints；basis 必须可追溯。
5. expected_effect 必须说明哪些失败 case 将被拒绝/修正，并检查代表性已通过 case 不被排除。
6. 不允许空改、格式重写、无关数组重排或把特定失败 shape/value 写成黑名单。

## 校验与交接

更新后运行：

```text
python scripts/validate_operator_rule.py <iter>/constraints.json
python scripts/normalize_constraints.py <iter>/constraints.json
python scripts/validate_artifacts.py constraints <iter>/constraints.json
python scripts/constraint_update_state.py finalize --report <iter>/constraint_update.json
python scripts/validate_artifacts.py constraint_update <iter>/constraint_update.json
```

随后必须进入独立 CHECK/REPAIR；只有 checker 通过后才能 GENERATE。
