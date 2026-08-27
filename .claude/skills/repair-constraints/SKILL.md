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
- 修复一个数组元素时避免改写、重排无关数组元素。

## 修复和校验

1. 按 issue id 逐项定位当前文件行和约束。
2. 用 Edit 直接修改当前 `constraints.json`。
3. 运行 `python scripts/validate_operator_rule.py <constraints>`。
4. 运行 `python scripts/normalize_constraints.py <constraints>`。
5. 运行 `python scripts/validate_artifacts.py constraints <constraints>`。
6. 结构失败只修正本次改动，最多三次；无法安全修复时停止并保留 issue 未关闭。
7. 返回尝试的 issue id；随后必须由新的 constraint-checker 上下文执行下一轮完整复检。

