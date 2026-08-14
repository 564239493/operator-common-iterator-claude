# 生成器层 null 语义 gap（待生成器/执行层跟进）

> 本文原为 ACLNN 提示词附录，已从 `prompts/operator_constraints/base.md` 移出，
> 因其内容是**生成器/执行层**的 gap 分析（extractor 不直接执行），保留在提示词中
> 会增加无关节的上下文。提示词层（`base.md` §3 / §6 自检）已先行确立语义，本文
> 记录 GENERATE/EXECUTE 层尚未对齐的已知后果与后续改造点。

提示词层已把"必选 + 只支持 nullptr"的取值语义确立为
`allowed_range_value={value:[null], type:enum, src_text 含 nullptr 关键词}`，并禁止在
`constraints_in_parameters` 追加 `param is None`。**但 GENERATE/执行层尚未支持该语义**，
已知后果与后续改造点如下（不改提示词，列为后续）：

1. **必选 [null] 丢约束**：`agent/generators/param_constraint_solve/param_constraint_utils.py:636-670` `analysis_param_is_present` 对必选参数在 645-646 行 `continue`，跳过 647-660 行的"`allowed_range_value` 全 `None` → `force_false_params`"分支，并于 670 行 `solver.add(is_present)` 强制 `is_present=True`。后果：必选 + `[null]` 的 `null` 取值语义被丢弃，生成的用例 `is_present=True` 且 `range_values` 被随机采样（非 `None`）。**后续改造点**：必选参数亦应进入 `[null]` 识别分支，把"必选 + `value=[null]` + `type=enum`"识别为"值恒为 None"而非"必出现"——需引入 `is_null`/`value_is_none` 标志，与 `is_present` 解耦。

2. **可选 [null] 误判缺席**：同函数 647-660 行对可选参数 `value=[null]` 走 `force_false_params` → `is_present=False`，把"取值语义"错当"缺席语义"。后果：可选 + `[null]` 的参数在生成用例里被当作"未传"而非"传 nullptr"。**后续改造点**：区分"`is_present=False`（缺席）"与"`is_present=True` 且 `value=None`（出现且为空指针）"两条路径。

3. **`param is None` 无独立建模**：`agent/generators/param_constraint_solve/expression_preprocess_utils.py:237-258` 把 `param is None` 翻成 `z3.Not(is_present)`，`null` 无独立 Z3 变量。后果：即使提示词允许补 `param is None`，也无法表达"出现且值为 None"。**后续改造点**：为每个可空参数引入 `is_null` Bool 变量，`param is None` → `is_null`，`param is not None` → `Not(is_null)`，`is_present` 仅表达"是否传入"。

4. **resolve_model 不落 None**：`agent/generators/param_constraint_solve/param_var_definition.py` 各 `resolve_model` 系列没有把 `range_values` 落成 `None` 的路径；`ScalarVar.resolve_model` 行 1025-1026 early-return `{'type': self.type}`（缺 `is_present`/`range_values` 键）会在下游触发 `KeyError`。**后续改造点**：`resolve_model` 在 `is_null=True` 时输出 `range_values=None`（或专用 `value_is_none=True` 字段），并修复 early-return 丢键。

5. **执行层 attr null crash**：`executer/resources/aclnn_api_template.py.j2:151-176` `handle_attr_param` 行 160 `range_val.encode('utf-8')` 对 `None` 必崩（`AttributeError: 'NoneType' object has no attribute 'encode'`）；行 161 `ctype(None)` 对 `c_int`/`c_bool` 静默归 0/False；仅行 169-176 的 `attr_tuple`/`attrs` 分支正确处理 `None`。后果：单 attr 参数取 `None` 会 crash 或静默变 0/False。**后续改造点**：单 attr 分支增加 `if range_val is None: input_tmp[config.name] = <对应类型的 null 指针>; continue`，对齐 `attr_tuple` 已有逻辑。tensor 必选参数传 nullptr 已有现成兜底（行 67-87），隐式可用。

**`is_null` 建模改造关键点（汇总）**：(a) `param_var_definition` 各 Var 类增 `is_null` Z3 Bool；(b) `analysis_param_is_present` 改为同时设置 `is_present` 与 `is_null`，必选 + `[null]` → `is_present=True, is_null=True`；(c) `expression_preprocess_utils` `is None`/`is not None` 改译 `is_null`/`Not(is_null)`；(d) `resolve_model` 输出 `value_is_none` 字段；(e) 用例序列化层（`create_dataset`/template）按 `value_is_none` 输出 `nullptr`；(f) 校验层 `validate_artifacts.py` 已兼容（`_EXPLICIT_NULL_RE` 放行），无需改；(g) 提示词层已就绪，待 (a)-(e) 落地后 GENERATE 阶段自然符合。

> 关联：`prompts/operator_constraints/base.md` §6 自检条目（"必选参数只支持 nullptr"取值语义）
> 是提示词侧的对应约束；本文是生成器/执行侧的对齐缺口。
