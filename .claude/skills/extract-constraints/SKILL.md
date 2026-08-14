---
description: 从算子 Markdown 提取符合生成器模型的 constraints.json，供 constraint-extractor 使用。
---

# 约束提取规范

输入必须包含：算子文档、当前提示词、当前轮目录。若 `inputs/scene_directive.md`
存在（由 SCENE_SCAN 子步骤产出），也必须读取并严格按其场景指令屏蔽非选定场景，
并按其 `device_types` 收窄 `product_support`（详见
下文「设备→`product_support` 规则」）。

> 当前提示词是 family 隔离的完整快照（见 `run_state.current_prompt_modules`）：ACLNN
> 由 `scripts/select_prompt.py` 经预分析、知识路由、适用性判断后冻结，torch_npu 由
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
提取，行为不变）：优先读取 directive 末尾机读块作为权威锚点（prose 段仅供人读）。
机读块为 `<!-- scene: {device_types, param_modes} -->`。

**设备→`product_support` 规则**（同一 directive 存在条件下，覆盖提示词 §4.3 的
"仅取 √ 行"为"按选定设备收窄"）：先读文档"产品支持情况"表得到 √ 行全集 `D`；再按
机读块 `device_types` 决定本次 `product_support`——将 `device_types` 与 `D` 取交集
（剔除文档标 × 的设备，绝不生成算子不支持的平台），并按文档表格自上而下排序。
设备类型已来自"产品支持情况"、为具体设备名，**无"通用"通配符**，直接取交集。`device_types`
缺失或交集后为空时回退为 `D`（行为与无 directive 一致）。本规则与 §4.6.2 的"逐平台
复制 `ParamAttributes`"一致：`product_support` 收窄后，`inputs`/`outputs`/
`deterministic_computing`/`constraints_in_parameters` 的二级平台 key 仅按收窄后的
`product_support` 逐平台产出（仍不得用单一平台代笔）。

**屏蔽（`param_modes` 三态）**——按机读块 `param_modes[device][param]` 以**选择内容为基本限制**收窄每设备
每参数（未提及的按文档原文提取）：
- `{"expand": [取值清单]}` → **所选清单是该特性参数的基本限制**：凡依赖该参数取值的约束均按清单收窄——无论体现为该参数自身的 `allowed_range_value` 枚举候选，还是以该取值为条件的 `dtype`/`format`/`dimensions` 分支或 `constraints_in_parameters` 行（保留命中清单内取值者、丢弃绑定清单外取值者，清单为所选模板 `values` 并集，**禁止回文档拉全集**）；与该参数取值**无关联**的约束按文档原文提取、不受清单影响。
- `{"fix": X}` → **仅产单值候选** `X`（取该值、不展开取值分支）；
- **缺键** → 该参数仅出现在未选模板（Q2 未选）下，若是 Optional 参数则**不产
  `presence_dependency`**（presence 丢、不测试该量化路径）；非 Optional 参数不受此态影响。
  若该参数同时出现在已选模板下，则按已选模板的 `expand`/`fix` 决策处理（并集语义，
  `expand` 优先）。

**保留**与参数取值**无关**的通用约束（`shape_equality`、维度、`groupType` 等）；`dtype`/`format`/`dimensions` 以取值为条件分支者随选择收窄（保留命中已选取值的分支、丢弃绑定未选取值的分支），无条件者保留。
场景只做"屏蔽"，不臆造文档未声明的限制；结果仍须满足 `OperatorRule` 与
`validate_artifacts.py constraints` 校验。

1. 逐节阅读文档，区分明确约束、示例和说明性文字。
2. **模式判定**：先读取 `run_state.json.operator_family`。
   - `aclnn` 且 `run_state.prompt_assembly_record` 非空时，先执行
     `python scripts/validate_prompt_assembly.py --record <prompt_assembly_record>`；失败说明
     冻结上下文已漂移，必须阻断，不得在提取阶段重新路由或绕过哈希。
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
   `python scripts/validate_operator_rule.py <iter-dir>/constraints.json`
8. 执行：
   `python scripts/normalize_constraints.py <iter-dir>/constraints.json`
9. 执行：
   `python scripts/validate_artifacts.py constraints <iter-dir>/constraints.json`
   该命令会实际构造 `agent.generators.common_model_definition.OperatorRule`；只有 Python
   返回码为 0 才是有效数据结构，模型文字自检不能替代。
10. 写入后逐项复核有限标量候选：原文出现“传入 A 或 B”“仅支持 A、B、C”“共有
   N 种模式”等明确候选语义时，必须使用扁平 `enum`；不得使用 `range` 或
   `[[A,B]]`。特别是 `src_text="传入0或1"` 必须为
   `{"value":[0,1],"type":"enum"}`。
11. 校验不通过时依据错误修正，最多三次；仍失败则明确返回阻断原因。
12. **场景屏蔽完整性自检**（仅当 `inputs/scene_directive.md` 存在时）：确认
    `param_modes` 三态已应用——`{"expand": [取值清单]}` 参数凡依赖其取值的约束（自身 `allowed_range_value`、
    `dtype`/`format`/`dimensions` 条件分支、`constraints_in_parameters` 行）均已按清单收窄（**不得超出清单回文档拉全集**）、
    `{"fix": X}` 参数取单值 `X` 且依赖其取值的约束同此收窄、缺键的 Optional 参数其
    `presence_dependency` 未产出；与取值**无关**的通用约束（shape/维度/groupType 等）未被误删。
    确认 `product_support` 已按机读块 `device_types` 收窄并与文档 √ 行取交集
    （直接交集，无"通用"展开），`inputs`/
    `outputs` 等二级平台 key 仅含收窄后的平台、无遗漏无代笔。自检不通过则回到步骤 1
    重提，不放过半屏蔽的 constraints.json。

开始写文件前必须确认调度已将 run state 更新为 `EXTRACT`。成功后回报非空
`constraints.json` 的绝对路径；不得只返回聊天中的摘要。
