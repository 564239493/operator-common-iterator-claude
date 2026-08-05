---
description: 从算子 Markdown 提取符合生成器模型的 constraints.json，供 constraint-extractor 使用。
---

# 约束提取规范

输入必须包含：算子文档、当前提示词、当前轮目录。若 `inputs/scene_directive.md`
存在（由 SCENE_SCAN 子步骤产出），也必须读取并严格按其场景指令屏蔽非选定场景。

> 当前提示词是 family 隔离的完整快照（见 `run_state.current_prompt_modules`）：ACLNN
> 可能由 `scripts/select_prompt.py` 装配，torch_npu 由
> `scripts/select_torch_npu_prompt.py` 装配。只按快照中实际存在的章节工作；不得从另一
> family 的 canonical prompt/模块补规则。

## 输入隔离（强制）

约束事实只能来自调度消息明确指定的以下输入：

- 当前任务的 `run_state.json`；
- 当前任务 `inputs/` 下的算子文档和提示词快照；
- 当前项目的约束数据结构、规范化脚本和校验脚本（只用于理解结构及执行校验）。

严禁将以下内容作为读取、搜索、复制或改写来源：

- 当前任务以外的 `runs/**`，尤其是历史 `constraints.json`、supplement 和迭代产物；
- `.claude/projects/**/memory/**`、用户级 memory、历史会话记录或其他 Agent 的记忆文件；
- `scripts/_build*constraints.py`、`agent/**/_build*constraints.py` 等历史算子构建脚本；
- 其他算子的约束文件，即使算子名称、参数或版本看起来相似。

不得复制或局部修补历史 `constraints.json` 作为本轮提取结果。每个参数卡片和跨参数
关系都必须根据本轮文档与本轮提示词重新生成并审查。若当前输入不足以确定约束，应
保留为空或按提示词标注不确定性，不得从历史产物补齐。

**场景屏蔽规则**（仅当 `inputs/scene_directive.md` 存在时执行；不存在则按全场景
提取，行为不变）：按 directive 中列出的合法 (方式, 位宽) 组合，仅保留符合该组合的
参数存在性路径与约束——屏蔽非选定场景的专属 Optional 参数（其
`presence_dependency` 不产出）、剔除 `allowed_range_value` 中与未选场景绑定的枚举
候选、删除未选场景专属的 `constraints_in_parameters` 约束行；**保留**与所有场景
通用的约束（`shape_equality`、维度、`dtype`、`format`、`groupType` 等）。场景只做
"屏蔽"，不臆造文档未声明的限制；结果仍须满足 `OperatorRule` 与
`validate_artifacts.py constraints` 校验。

1. 逐节阅读文档，区分明确约束、示例和说明性文字。
2. **模式判定**：先读取 `run_state.json.operator_family`。
   - `hs`：按当前海思 prompt 处理 Python `torch_npu.*` 函数原型；不得要求
     `GetWorkspaceSize`，不得伪造 ACLNN 名称；`*` 和默认值决定 optional。
   - `aclnn`：看"函数原型"章节是否含 `aclnnXxxGetWorkspaceSize`。
   - 含 → 两段式（默认）。
   - 不含、只有 `aclnnXxx(...)` 单函数 → 一段式：按当前提示词 §4.4 一段式分支取 `function_signature`（唯一函数声明，不含 `workspaceSize`/`executor`）；标量指针输出（如 `uint64_t*`）进 `outputs`，不当流程参数排除（见提示词 §4.6.1 一段式例外、§4.6.3 aclIntArray 固定 dtype 规则）。**不得**在 JSON 中写入 `is_single_function_mode` 字段。
3. 按当前提示词要求输出完整 JSON，不在 JSON 外夹带解释。
4. `operator_name` 必须与文档一致；平台、dtype、format、shape、取值范围和跨参数
   约束必须可追溯到原文。所有 family 共用的结构门禁只有：
   - `allowed_range_value.type=range` 的边界必须是实际数值，不允许 `null`，且遵循当前
     family 快照定义的开闭语义；torch_npu 中它只能表示双边开区间，闭/半开区间改用
     精确不等式并令 allowed range 为空。`type=enum` 只有在文档明确把 null/None 列为
     合法候选时才可包含 `null`。
   - `expr` 中裸 `null` 会规范化为 Python `None`，只用于空值/存在性判断。
   - 数值范围使用不等式，不使用 `.range_value in [[min, max]]`。
5. family 专用规则：
   - `hs` / torch_npu：以当前 torch_npu 快照为唯一规则源。默认 None 不自动成为
     `allowed_range_value` 候选；Tensor 缺省、空 Tensor、空 list 分别表达。不得套用
     ACLNN 的 epsilon 推导、workspace、aclDataType 或 C 指针规则。
   - `aclnn`：只有当前 ACLNN 快照要求时，才应用以下 ACLNN 规则：
     - 原文“空”表示空指针/nullptr 且形成合法枚举时用 JSON `null`，禁止字符串
       `"空"`；仅原文明示零长度容器时使用空容器候选。
     - `epsilon`/`eps` 明确作为除 0 或分母保护值且当前提示词允许推导时，合并严格
       正值与文档上界。
     - `type.value=="aclDataType"` 时按 ACLNN 快照处理 dtype 与 enum；
       `type.value=="aclIntArray"` 时按 ACLNN 快照处理元素 dtype，不能把关联 Tensor
       dtype 误写给数组。
6. 写入 `<iter-dir>/constraints.json`。
7. 执行：
   `python scripts/normalize_constraints.py <iter-dir>/constraints.json`
8. 执行：
   `python scripts/validate_artifacts.py constraints <iter-dir>/constraints.json`
9. 写入后逐项复核有限标量候选：原文出现“传入 A 或 B”“仅支持 A、B、C”“共有
   N 种模式”等明确候选语义时，必须使用扁平 `enum`；不得使用 `range` 或
   `[[A,B]]`。特别是 `src_text="传入0或1"` 必须为
   `{"value":[0,1],"type":"enum"}`。
10. 校验不通过时依据错误修正，最多三次；仍失败则明确返回阻断原因。
11. **场景屏蔽完整性自检**（仅当 `inputs/scene_directive.md` 存在时）：确认被屏蔽
    场景的专属 Optional 参数其 `presence_dependency` 未产出、通用约束
    （shape/dtype/format 等）未被误删、`allowed_range_value` 候选已按场景收窄。
    自检不通过则回到步骤 1 重提，不放过半屏蔽的 constraints.json。

开始写文件前必须确认调度已将 run state 更新为 `EXTRACT`。成功后回报非空
`constraints.json` 的绝对路径；不得只返回聊天中的摘要。
