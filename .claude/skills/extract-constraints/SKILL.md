---
description: 从算子 Markdown 提取符合生成器模型的 constraints.json，供 constraint-extractor 使用。
---

# 约束提取规范

输入必须包含：算子文档、当前提示词、当前轮目录。

> 当前提示词是 family 隔离的完整快照（见 `run_state.current_prompt_modules`）：ACLNN
> 可能由 `scripts/select_prompt.py` 装配，torch_npu 由
> `scripts/select_torch_npu_prompt.py` 装配。只按快照中实际存在的章节工作；不得从另一
> family 的 canonical prompt/模块补规则。

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
9. 校验不通过时依据错误修正，最多三次；仍失败则明确返回阻断原因。

开始写文件前必须确认调度已将 run state 更新为 `EXTRACT`。成功后回报非空
`constraints.json` 的绝对路径；不得只返回聊天中的摘要。
