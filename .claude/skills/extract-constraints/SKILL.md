---
description: 从算子 Markdown 提取符合生成器模型的 constraints.json，供 constraint-extractor 使用。
---

# 约束提取规范

输入必须包含：算子文档、当前提示词、当前轮目录。模式判定时 `Read` 当前轮目录的
`../run_state.json` 取 `toolchain`（若调度消息已告知则以调度消息为准，默认 `atk`）。

> 当前提示词可能是 `scripts/select_prompt.py` 按算子特征装配的「基线 + 命中模块」快照（见 `run_state.current_prompt_modules`）；按快照中实际存在的章节工作，§ 编号引用以快照为准。未命中的模块章节不在快照中，其对应的 §9 条件自检项不触发。

1. 逐节阅读文档，区分明确约束、示例和说明性文字。
2. **模式判定**：先确定 `toolchain`——优先取调度消息告知值；未告知时 `Read` 当前轮目录的 `../run_state.json` 取 `toolchain` 字段，默认 `atk`。再按工具链分支：
   - `atk`：看“函数原型”章节是否含 `aclnnXxxGetWorkspaceSize`。
     - 含 → 两段式（默认）。
     - 不含、只有 `aclnnXxx(...)` 单函数 → 一段式：按当前提示词 §4.4 一段式分支取 `function_signature`（唯一函数声明，不含 `workspaceSize`/`executor`）；标量指针输出（如 `uint64_t*`）进 `outputs`，不当流程参数排除（见提示词 §4.6.1 一段式例外、§4.6.3 aclIntArray 固定 dtype 规则）。
   - `ttk`：torch_npu Python 原型（如 `torch_npu.xxx(query, key, value, *, pse_shift=None, ...) -> (Tensor, Tensor)`，无 `GetWorkspaceSize`/`workspaceSize`/`executor`/`stream`/`aclnnStatus`）。按当前提示词 **ttk 章节**取 `function_signature`（Python 签名逐字）、参数分类（`*` 前位置参数 / `*` 后 keyword-only，按 `Tensor`/`List[int]`/`int`/`float`/`bool`/`str` 映射 type）、`outputs` 取自返回标注 `(Tensor, ...)`；dtype 文档小写 → 规范大写。
   - 任一模式都**不得**在 JSON 中写入 `is_single_function_mode` 或 `toolchain` 字段。
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
