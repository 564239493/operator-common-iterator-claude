---
description: 从补充约束 Markdown 与已提取 constraints.json 产出结构化 constraints_patch.json（op=add/replace），供 constraint-supplementer 使用。
---

# 约束补充规范

输入必须包含：补充约束 Markdown、当前轮已提取的 `constraints.json`、当前轮目录。

补充约束 Markdown 有两个来源，**都读**（合并消费）：
1. `inputs/supplementary-doc.md`（**主源**，source-analyst 从源码分析自动产出）。
   仅当 `run_state.operator_src_snapshot` 非空时存在。
2. `inputs/supplement_constraints.md`（用户 `--supplement-constraints` 手写快照，
   可选）。仅当 `run_state.supplement_constraints` 非空时存在。

两者都为空时跳过补充阶段。条目去重：若同一 `expr` 在两个文件都出现，以
supplementary-doc.md（源码分析）为准。

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
   **【int 标量参数取值必用 `.range_value`】** int 型标量参数（如 `dstFormat`、
   `additionalDtype`、`actualFormat`）在跨参 expr 里引用其取值时**必须**用
   `<param>.range_value`（如 `dstFormat.range_value == 29`、
   `additionalDtype.range_value in [1,27,2,36]`、`additionalDtype.range_value == -1`），
   对齐 `prompts/modules/acl_format_enum.md` §C.4 与 extractor 产出；**禁止**裸名
   （`dstFormat in [...]` / `additionalDtype == -1`）。裸名虽对 `ScalarVar` 在 Z3 编码
   上等价（都解析到同一 `z3_var`），但违反规范、与 extractor 不一致，且 post_check 把
   int 标量映射成对象后裸名会 `AttributeError`（见 generate-cases 的 post_check 命名空间
   约定）。
   **【跨 sort 比较必展开析取】** 凡涉及「int 枚举码 attr 与 `tensor.dtype`
   比较」的约束，**必须**展开成显式析取，**禁止**直接写
   `attr == tensor.dtype` / `attr != tensor.dtype`。
   - 原因：constraints.json 里 scalar attr（如 `additionalDtype`）的
     `allowed_range_value.value` 是 **int 枚举码**（ACL dtype 码），而
     `tensor.dtype` 在 Z3 求解器里是 **DType EnumSort**（规范化名）。直接
     `additionalDtype == srcTensor.dtype` 会触发 Z3 sort mismatch（IntSort vs
     DType），整条 `or` 守卫被 `add_constraint` 丢弃（见
     `agent/generators/param_constraint_solve/z3_expression_solver_utils.py`
     `add_constraint` 的 dropped_constraints），致 WeightQuant 条件守卫全部失效。
   - 正确写法：把相等/不等关系展开为 `(attr==<int码> and tensor.dtype=="<DType名>")`
     的析取，每个析取项是同 sort 的字面量比较，用 `or`/`and` 串联。DType 名用大写
     规范名（预处理自动映成 Z3 DType enum 常量）。
   - ACL dtype 码表（attr int 码 ↔ DType 名）：
     `0=FLOAT, 1=FLOAT16, 2=INT8, 3=INT32, 4=UINT8, 6=INT16, 7=UINT16, 8=UINT32,
     9=INT64, 10=UINT64, 12=BOOL, 27=BFLOAT16, 35=FLOAT8_E5M2, 36=FLOAT8_E4M3FN,
     40=FLOAT4_E2M1, -1=UNDEFINED`。只展开补充文件实际声明的码集。
   - 示例（`additionalDtype == srcTensor.dtype` 表示非 WeightQuant 路径，码集
     `[1,27,2,36]`）：
     - 禁止：`(additionalDtype == srcTensor.dtype) or (len(srcTensor.shape) in {2,3})`
     - 正确：`((additionalDtype.range_value == 1 and srcTensor.dtype == "FLOAT16") or
       (additionalDtype.range_value == 27 and srcTensor.dtype == "BFLOAT16") or
       (additionalDtype.range_value == 2 and srcTensor.dtype == "INT8") or
       (additionalDtype.range_value == 36 and srcTensor.dtype == "FLOAT8_E4M3FN")) or
       (len(srcTensor.shape) in {2,3})`
   - 同理适用于 `format` 码 attr（`acl_format` int 码 ↔ format 名字符串）等其他
     int 枚举码 attr 与 tensor 属性的跨 sort 比较：一律展开成同 sort 字面量析取。
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
   - `basis` 是补充文件依据：supplementary-doc.md 的 basis 来自源码分析
     （`source_location` + `error_string`）；supplement_constraints.md 的 basis
     来自手写说明。写入 patch 时取条目内给出的依据文本。
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
   - 引用 int 标量参数取值的 expr 必用 `<param>.range_value`，不得裸名（见 §2 int 标量规则）
7. 自检不通过时修正，最多三次；仍失败则明确返回阻断原因。
8. 返回：add/replace 计数、涉及平台、产物绝对路径。不修改 `constraints.json`。
