---
description: 根据 constraint_check.json 对当前 constraints.json 做最小范围修复，供 constraint-repairer 使用。
---

# 约束精准修复

## 输入与边界

读取调度消息指定的当前轮 `constraints.json`、`constraint_check.json`、算子文档及存在的
场景/补充/冲突证据。只修复报告中状态为 `open` 或 `unfixed` 的问题。

强制边界：

- 不从文档重新完整提取 constraints；
- 不复制上一轮或其他 run 的 constraints；
- 不修改报告未指出的约束；
- 不修改 `constraint_check.json`，尤其不能把问题标为 fixed；
- 对同一问题采用满足文档与补充证据的最小改动；
- 修复一个数组元素时避免改写、重排无关数组元素；
- 修复条目必须保留原 `id`；修复中新增的约束条目必须分配新 `id`
  （当前最大编号 +1，全局唯一）。
- `src_txt_line`（文档快照行号数组）为溯源字段：修复保留原条目时原样保留，新增条目
  必须带该字段（从 `inputs/` 快照核实），来源条款不变时保持原值。

## 修复和校验

1. 按 issue id 逐项定位当前文件行和约束。
2. 用 Edit 直接修改当前 `constraints.json`。
3. 运行 `python scripts/validate_operator_rule.py <constraints>`。
4. 运行 `python scripts/normalize_constraints.py <constraints>`。
5. 运行 `python scripts/validate_artifacts.py constraints <constraints>`。
6. 运行 `python scripts/diff_constraints_by_id.py <baseline> <constraints>`：exit 2
   （同一份约束内容换 id / 重新编号）= 违反原位修改约定，必须改回按 id 原位修复。
   `<baseline>` 用本迭代 `constraints.json.pre_update`（反馈轮）或上一轮
   constraints.json（首轮 CHECK/REPAIR）。`modified` 条目应限于本次修复的 issue 对应
   id，新增条目必须为新分配 id。
7. 结构失败只修正本次改动，最多三次；无法安全修复时停止并保留 issue 未关闭。
7. 返回尝试的 issue id；随后必须由新的 constraint-checker 上下文执行下一轮完整复检。

