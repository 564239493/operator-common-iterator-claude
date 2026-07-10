---
description: 从补充约束 Markdown 与已提取 constraints.json 产出结构化 constraints_patch.json（op=add/replace），供 constraint-supplementer 使用。
---

# 约束补充规范

输入必须包含：补充约束 Markdown 快照（`run_state.supplement_constraints`）、
当前轮已提取的 `constraints.json`、当前轮目录。

> 本阶段**不重新提取约束**，只对 EXTRACT 已产出的 `constraints.json` 做关系
> 补充：追加（add）补充文件描述的新关系约束、替换（replace）文档提取过宽/过窄
> 的约束。合并（写回 `constraints.json`）由确定性脚本
> `scripts/apply_supplement_constraints.py` 完成，本 skill 只产 patch。

1. 逐节阅读补充约束 Markdown，识别其中描述的「参数间关系约束」：shape 广播/
   相等/依赖、dtype 一致/依赖、value 依赖、format 一致、presence 依赖等。
2. 表达式规范**严格沿用 `extract-constraints` 的 expr 规范**（裸 `null` 规范化
   为 Python `None`、数值范围用不等式、禁止 `.array_length` 改用 `len(container)`、
   `aclDataType` 参数 dtype 固定 `["string"]` 等），以及
   `prompts/modules/broadcast.md` 的关系展开规范（broadcast 右对齐表达、dtype
   互推导），保证生成器可消费。
3. 对每条关系区分操作：
   - **add_constraint**：补充文件描述了 `constraints.json` 中没有的新关系。
     `target_platform` 为该关系生效的平台（中文产品名，须与 `constraints.json`
     的 `constraints_in_parameters` 平台 key 一致）；跨平台通用关系用 `"all"`
     （合并器 `apply_supplement_constraints.py` 会把条目**展开写入每个平台桶**，
     最终 `constraints.json` 不产生 `common` 桶——不要用 `"common"`，会被合并器拒绝）。
   - **replace_constraint**：补充文件修正了 `constraints.json` 中已存在但过宽/
     过窄的约束。必须提供 `match_expr`：被替换条目的**原 expr 精确文本**（从
     `constraints.json` 复制，不得改写）；`proposed` 为新约束。**自检时先在
     `constraints.json` 中 grep `match_expr` 确认存在**，避免合并器精确匹配失败。
4. patch 项结构（JSON 数组，每项）：
   ```json
   {
     "op": "add_constraint | replace_constraint",
     "target_platform": "<平台名 | all>",
     "match_expr": "<仅 replace 必填：被替换条目原 expr 精确文本>",
     "proposed": {"expr_type": "...", "expr": "...", "relation_params": ["..."]},
     "basis": "<来自补充文件的依据，如 '§2: 输入张量需可广播'>"
   }
   ```
   - `proposed` **只含** `expr_type`/`expr`/`relation_params` 三字段；
     `src_text`/`origin` 由合并器填（`src_text=basis`、`origin="supplement"`），
     **不要**塞进 `proposed`（`InterParamConstraint` 为 `extra:forbid`）。
   - `basis` 是补充文件依据，不是源码依据（源码分析已删除）。
5. 写入 `<iter-dir>/constraints_patch.json`。
6. schema 自检（逐项核对或 Python 脚本）：
   - `op ∈ {add_constraint, replace_constraint}`
   - `target_platform` 非空
   - `proposed` 含 `expr_type`/`expr`/`relation_params`
   - `replace_constraint` 必有 `match_expr`，且 `match_expr` 在 `constraints.json`
     对应平台的 `constraints_in_parameters` 中能找到（`item.expr == match_expr`）；
     `target_platform="all"` 时须在**每个**平台桶中都能找到，否则合并器精确匹配
     失败阻断
   - `expr` 可被 `ast.parse` 解析
7. 自检不通过时修正，最多三次；仍失败则明确返回阻断原因。
8. 返回：add/replace 计数、涉及平台、产物绝对路径。不修改 `constraints.json`。
