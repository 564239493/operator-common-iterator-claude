---
description: 根据 constraint_extraction 分析结果产生下一版完整通用提示词（per-iter 快照 + 提升候选契约）。
---

# 提示词优化规范

前置条件：analysis.json 的 root_cause 必须是 constraint_extraction。

先读取 `run_state.json.operator_family`。只修改由 specific_issues 支持的章节，保留原
提示词整体结构和所有无关规则。输出 `prompt_v<N+1>.md` 与
`prompt_changes_v<N+1>.md`（**两份**——见第 §3 节契约）。变更说明逐项映射：失败 case、
文档证据、原规则缺陷、新规则。禁止写入当前算子名称的硬编码特例。

**模块化提示词（v4 起）**：提示词为 `prompts/operator_constraints_extract_v4.md`（基线）+ `prompts/modules/*.md`（按算子类按需装配的模块）。读取 `run_state.json` 的 `current_prompt_modules` 可知本轮命中的模块。在 `prompt_changes_v<N+1>.md` 中，逐项标注 specific_issues 指向的规则所属文件（基线章节或 `modules/<name>.md` 的 §<节>），便于后续将修复定位到 canonical 模块。当前仍沿用 per-iter `prompt_v<N+1>.md` 输出契约（round 2+ 使用该覆盖快照，不走模块装配）。提升到全局 `prompts/` 由任务终态后用户显式批准并调用 `scripts/promote_prompt.py` 完成，详见 §5。

## family 隔离

- `operator_family=aclnn`：canonical 来源只能是
  `prompts/operator_constraints_extract_vN.md` 与 `prompts/modules/*.md`。禁止把
  torch_npu 的 Python 签名、layout、TensorList/返回槽规则写入 ACLNN 模块；禁止为
  单一算子在通用基线中硬编码名称特例。
- `operator_family=hs`（torch_npu）：canonical 来源只能是
  `prompts/torch_npu_constraints_extract_vN.md` 与
  `knowledge/torch_npu/**/*.md`。禁止引用或修改 `prompts/modules/*.md`，也禁止引入
  ACLNN workspace/GetWorkspaceSize/两段式 API 假设。通用缺陷定位到 torch_npu 基线或
  family 模块；仅对某个算子成立且有该文档证据的修复，可定位到其精确算子知识模块，
  不得写进通用模块。

---

## 1. 适用范围

仅当 analysis.json 的 `root_cause == "constraint_extraction"` 且计划进入下一轮 OPTIMIZE → EXTRACT 循环时才触发本 Skill。`generator_bug` / `executor_bug` / `MAX_ITERATIONS` 不触发。

## 2. per-iter 快照契约

新提示词**仅**落盘到当前 run 的 iter 目录：

- `runs/<run-id>/iter_<N+1>/prompt_v<N+1>.md` — 完整新提示词。
- `runs/<run-id>/iter_<N+1>/prompt_changes_v<N+1>.md` — 变更说明，格式见 §3。

门卫 `guard_project_writes.py:335-338` 限制 Agent 业务产物只能写到当前 `runs/<run-id>/`，本 Skill 不得尝试写 `prompts/`（写会被 deny，**且**违反与用户的隐式契约：提升必须经用户批准）。

## 3. `prompt_changes_v<N+1>.md` 必须包含四段（缺段视为产出不合格）

```text
# prompt_changes_v<N+1> 变更说明

## 1. 摘要表

| sections 编号 | 章节标题 | 改动类型 | 改动原因 |
| -------------- | -------- | -------- | -------- |
| §4.6.10 A | dtype 互推导 | 新增 | iter_N+1 失败 case #3 在 INT8 + BFLOAT16 组合下生成器误判 |
| §9 #14 | bool 参数 enum 化 | 收紧 | iter_N+1 失败 case #7 出现 bool 取值漂移 |
| ... | ... | ... | ... |

## 2. 改动前后逐 section diff

### §4.6.10 A — dtype 互推导

```diff
-  ### §4.6.10 A.  原状
+  ### §4.6.10 A.  修订
   ...
```

### §9 #14 — bool 参数 enum 化

```diff
-  ...
+  ...
```

## 3. 有效性凭证

本轮 `prompt_v<N+1>.md` → iter_<N+1> EXTRACT → iter_<N+1> EXECUTE 结果：

- `iteration_total`: <N+1>
- `execution_result.json` total / passed / failed: <回填实际数>
- 一句话定性: <"成功用例占比 X% / 全失败 / ...">

> **注意**：本节由主协调器在 run 终态时回填实际执行结果；优化器在生成 `prompt_changes`
> 时若尚未执行，先留占位 `<待回填>`。

## 4. 未生效风险

明确标注 v<N+1> 仍可能无法修复的边角：

- iter_<N> 的失败 case #5（生成器侧 shape 越界）仍属求解器 bug，非本提示词可解；
- iter_<N> 的失败 case #8（executor 编译失败）属于执行环境问题，提示词层面已无法触及；
- 新增章节 §4.6.x 仅在首轮 EXTRACT 中验证，跨轮稳定性待 run 到终态确认。
```

### 3.1 摘要表 schema

- `sections 编号`：v4 主提示词用 §X.Y.Z 形式；模块用 `modules/<name>.md §X.Y` 形式。
- `改动类型` ∈ {新增, 收紧, 放宽, 删除, 重命名}。
- `改动原因`：一行简短文本，须可追溯到 specific_issues 中的某条 issue_id 或失败 case 编号。

### 3.2 diff 代码块规范

使用 ` ```diff ` Markdown 代码块，**保留真实前导空白**（v4 提示词 § 编号深至 4 层，缩进错位会被人眼误读）。`+`/`-`/空行直接复制 v<N> 与 v<N+1> 对应片段，单条 diff **不超过 200 行**——超过时分多个 `### 段` 子节。

### 3.3 引用关系

- v4 主提示词的 § 编号 → `prompt_changes` 必须用 `§X.Y[.Z]` 精确引用。
- 模块的 § 编号 → `prompt_changes` 必须用 `modules/<name>.md §X.Y` 形式。
- 用 `prompts/CHANGELOG.md` 的 B+++++++++++++ 等条目编号作"原变更触发"引用（一句话简述即可）。

## 4. 与 SPECIFIC_ISSUES 的逐项映射

`prompt_changes_v<N+1>.md` 的摘要表每行**必须**对应 `analysis.json.specific_issues[]` 中至少一条 issue。映射规则：

- 一条 issue → 多条摘要表行（issue 影响多个章节时）：摘要表内用 `(issue=#X)` 后缀标注来源；
- 多条 issue → 一条摘要表行：合并原因写 `issue=#A,#B`。
- 摘要表找不到对应 issue 的行 → 必须从摘要表中删除；非缺陷性的同义改写（如仅调整排版、修正错别字）应归入"未生效风险"段而非摘要表。

## 5. 提升到全局基线

`prompt_v<N+1>.md` 与 `prompt_changes_v<N+1>.md` 仅落 iter 目录，**不**直接进入 `prompts/`。

提升到全局基线由**主协调器**在 run 终态后：

1. 读 iter_<N+1>/`execution_result.json`，定性 `total > 0 and passed > 0` 为"初步有效"；
2. 在主协调器 AskUserQuestion 中**显式**向用户呈现"摘要表 + 有效性凭证 + 改动前后逐 section diff"（即 `prompt_changes` 的前 3 段）；
3. 用户同意后主协调器调：

   ```bash
   python scripts/promote_prompt.py \
     --from runs/<run-id>/iter_<N+1>/prompt_v<N+1>.md \
     --to-version <N+1> \
     --run-dir runs/<run-id> \
     --changes runs/<run-id>/iter_<N+1>/prompt_changes_v<N+1>.md
   ```

   `scripts/promote_prompt.py` 是**唯一**允许向 `prompts/` 写入下一版的入口。本 Skill 在执行期不得直接调用它。

4. 用户不同意或希望"先看完整 diff 再决定"时，迭代已在 iter 目录留底，main session 可继续或结束。

## 6. 边界与禁区

- **不得**直接 Edit/Write `prompts/operator_constraints_extract_v*.md`；
- **不得**修改 `prompts/modules/*.md`（除非本次 specific_issues 命中某模块 § 编号并明确要扩展，此时新规则**作为新版本**写入对应模块的更新说明，由下一次 promote 时一并提升到全局基线）；
- **不得**修改 `scripts/validate_artifacts.py` / `scripts/normalize_constraints.py` / `scripts/generate_cases.py` / `scripts/execute_cases.py` 等任何执行链路脚本——约束提取质量的提升仅靠提示词自身；
- **不得**依赖会话历史与未落盘事实；所有引用必须可追溯到 `analysis.json.specific_issues[]` 或已落盘文档片段。

## 7. 范围说明

- v<N+1> 仅替换 v<N> 的相关章节；不影响 `OperatorRule` schema，不影响 Python 求解器 / 生成器 / 执行器。
- 模块装配（`scripts/select_prompt.py`）由 `operator_name` 等特征触发，与本 Skill 正交：本 Skill 输出 per-iter 快照在第 2 轮（含）后直接覆盖全局基线；模块按基线 + 模块装配产生下一轮 EXTRACT 用的输入提示词。
