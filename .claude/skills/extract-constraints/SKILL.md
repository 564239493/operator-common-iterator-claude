---
description: 从算子 Markdown 提取符合生成器模型的 constraints.json，供 constraint-extractor 使用。
---

# 约束提取规范

输入必须包含：算子文档、当前提示词、当前轮目录。若 `inputs/scene_directive.md`
存在（由 SCENE_SCAN 子步骤产出），也必须读取并严格按其场景指令屏蔽非选定场景。

> 当前提示词可能是 `scripts/select_prompt.py` 按算子特征装配的「基线 + 命中模块」快照（见 `run_state.current_prompt_modules`）；按快照中实际存在的章节工作，§ 编号引用以快照为准。未命中的模块章节不在快照中，其对应的 §9 条件自检项不触发。

**场景屏蔽规则**（仅当 `inputs/scene_directive.md` 存在时执行；不存在则按全场景
提取，行为不变）：按 directive 中列出的合法 (方式, 位宽) 组合，仅保留符合该组合的
参数存在性路径与约束——屏蔽非选定场景的专属 Optional 参数（其
`presence_dependency` 不产出）、剔除 `allowed_range_value` 中与未选场景绑定的枚举
候选、删除未选场景专属的 `constraints_in_parameters` 约束行；**保留**与所有场景
通用的约束（`shape_equality`、维度、`dtype`、`format`、`groupType` 等）。场景只做
"屏蔽"，不臆造文档未声明的限制；结果仍须满足 `OperatorRule` 与
`validate_artifacts.py constraints` 校验。

1. 逐节阅读文档，区分明确约束、示例和说明性文字。
2. **模式判定**：看"函数原型"章节是否含 `aclnnXxxGetWorkspaceSize`。
   - 含 → 两段式（默认）。
   - 不含、只有 `aclnnXxx(...)` 单函数 → 一段式：按当前提示词 §4.4 一段式分支取 `function_signature`（唯一函数声明，不含 `workspaceSize`/`executor`）；标量指针输出（如 `uint64_t*`）进 `outputs`，不当流程参数排除（见提示词 §4.6.1 一段式例外、§4.6.3 aclIntArray 固定 dtype 规则）。**不得**在 JSON 中写入 `is_single_function_mode` 字段。
3. 按当前提示词要求输出完整 JSON，不在 JSON 外夹带解释。
4. `operator_name` 必须与文档一致；平台、dtype、format、shape、取值范围和跨参数
   约束必须可追溯到原文。
   - `allowed_range_value.type=range` 的边界必须是实际数值，不允许 `null`；
     `type=enum` 允许 `null` 作为离散候选。
   - 原文“空”若表示未传值、缺省、空指针或 `nullptr`，枚举候选必须写 JSON
     `null`，禁止写字符串 `"空"`；仅原文明示零长度容器时才使用空容器候选。
   - `expr` 中裸 `null` 会规范化为 Python `None`，只用于空值/存在性判断。
   - 数值范围使用不等式，不使用 `.range_value in [[min, max]]`。
   - `epsilon`/`eps` 明确作为除0或分母保护值时推导严格正值，并与文档上界合并。
   - `type.value=="aclDataType"` 的参数：`dtype.value` 固定为 `["string"]`，文档"数据类型"列候选写入 `allowed_range_value`（`type="enum"`），**不**写入 `dtype`（见提示词 §4.6.3 aclDataType 固定 dtype 规则）。
   - `type.value=="aclIntArray"` 的参数：`dtype.value` 固定为 `["int"]`；文档"数据类型"列若列张量 dtype，描述的是关联张量，**不**写入 `dtype`（见提示词 §4.6.3 aclIntArray 固定 dtype 规则）。
5. 写入 `<iter-dir>/constraints.json`。
6. 执行：
   `python scripts/normalize_constraints.py <iter-dir>/constraints.json`
7. 执行：
   `python scripts/validate_artifacts.py constraints <iter-dir>/constraints.json`
8. 校验不通过时依据错误修正，最多三次；仍失败则明确返回阻断原因。
9. **场景屏蔽完整性自检**（仅当 `inputs/scene_directive.md` 存在时）：确认被屏蔽
   场景的专属 Optional 参数其 `presence_dependency` 未产出、通用约束
   （shape/dtype/format 等）未被误删、`allowed_range_value` 候选已按场景收窄。
   自检不通过则回到步骤 1 重提，不放过半屏蔽的 constraints.json。
