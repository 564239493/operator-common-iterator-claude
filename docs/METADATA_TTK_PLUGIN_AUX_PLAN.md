# Metadata 类算子（TTK Plugin）逐元素 values 扩展方案

> 状态：设计稿（待评审）
> 目标算子：`aclnnLightningIndexerV2`（Atlas A2 + TTK + 前置 `aclnnLightningIndexerV2Metadata`）
> 关联流程：`/iterate-operator` 状态机（PLAN → EXTRACT → GENERATE → EXECUTE → GATE → DIAGNOSE）

---

## 1. 背景与问题

`aclnnLightningIndexerV2` 依赖前置算子 `aclnnLightningIndexerV2Metadata`。TTK 执行时必须通过
`--plugin`（本项目中对应 `--ttk-plugin`）加载 Metadata 插件；插件会读取若干**逐元素值**：

- `cu_seqlens_q_values`
- `cu_seqlens_k_values`
- `seqused_q_values`
- `seqused_k_values`
- `cmp_residual_k_values`

但当前项目生成链路只有“区间 + 随机”能力：

- `constraints.json` 只有 shape / presence / scalar；
- `cases.json` 只有 `range_values`，没有逐元素 values；
- `cases_ttk.csv` 只有 `input_data_ranges`，插件读不到这些值；
- 结果：插件 `build_case_params()` 得到 None，golden 直接
  `assert cu_seqlens_q is not None` / `TypeError: list(None)`。

同时，文档对其中一些参数有**值级约束**（非 shape）：

- `cuSeqlensQOptional`：长度 B+1、首元素 0、非负、非递减、TND 末值 = T1；
- `cuSeqlensKOptional`：同上，TND 末值 = T2；
- `sequsedQOptional`：长度 B，且 `sequsedQ[b] <= cuSeqlensQ[b+1] - cuSeqlensQ[b]`（TND）；
- `cmpResidualKOptional`：长度 B，每元素 `< cmpRatio`；
- `blockTableOptional`：二维、首维 B、容量覆盖 sequsedK、block id 合法；
- `metadataOptional`：TTK 槽位必须是 `(-1,)`，由插件填充。

这些约束不能放进 `constraints.json`（`OperatorRule` 是 `extra: forbid`，且 Z3 内容求解会显著变慢
甚至 fail-open），必须单独治理。

---

## 2. 目标

1. **早期拦截**：Metadata 类型算子未传 `--ttk-plugin` 时，在 PLAN 阶段立即失败并给出明确提示。
2. **插件需求可分析**：读取并静态分析插件，产出“最终用例需要提供哪些额外参数/值”的契约。
3. **值约束可落盘**：把文档中的 value 级约束抽取为独立 artifact，供生成/校验使用。
4. **逐元素 values 可生成**：复用现有 aux 流程，把 `*_values` 写入 `cases_ttk.csv` 的
   `attributes`，让原插件直接读取。
5. **可校验、可回归**：值生成有确定性 validator，CSV 门禁 fail-closed；不通过不进入执行。
6. **不破坏现有流程**：shape/presence 仍由 `constraints.json` + Z3；执行/诊断状态机不变。

---

## 3. 范围与非目标

### 范围（第一版）
- 以 `aclnnLightningIndexerV2` + Metadata 前置算子 + TTK 作为**首个验证样本**；
- **通用能力**（代码中不出现算子名，由文档 + 插件契约驱动）：
  - Metadata 类型识别 + `--ttk-plugin` 早期硬拦截；
  - 插件静态契约分析（`*_values` / AUX_TARGETS / metadata 槽位）；
  - `*_values` 以原生 list 写入 CSV attributes 的传输通道；
  - 基于 `plugin_requirements.json` + `value_constraints.json` 的 aux spec/validator 框架。
- **算子相关**：具体值语义（前缀和/≤差分/<比例等）由文档与插件契约配置，不写死在代码里。

### 非目标
- 不做面向所有未来 Metadata 算子的过度抽象框架；只沉淀可复用骨架；
- 不把内容值塞回 Z3 / `constraints.json`；
- 不修改 golden 的 TND Padding 语义（见 §11 风险）；
- 不自动修改受保护的 `agent/generators/**`。

---

## 4. 现状盘点

| 能力 | 状态 | 位置 |
|---|---|---|
| `--ttk-plugin` CLI | 已有 | `scripts/init_run.py` |
| `run_state.ttk_plugin` | 已有 | `scripts/run_state.py`、`init_run.py` |
| EXECUTE 读取 run_state 插件 | 已有 | `scripts/execute_cases.py::_run_state_ttk_plugin` |
| `metadataOptional` 哨兵 `(-1,)` | 已实现 | `scripts/atk_to_ttk_aclnn.py` |
| `groupListData` aux 链路 | 已有 | `operator_aux_params.py` / `generate_cases.py` / `materialize_group_list_data.py` |
| Metadata 类型识别 + 早期插件拦截 | **缺失** | 需新增 |
| 插件脚本需求分析 | **缺失** | 需新增 |
| `value_constraints.json` | **缺失** | 需新增 |
| `*_values` aux spec / generator | **缺失** | 需扩展 |
| `*_values` validator / CSV 门禁 | **缺失** | 需扩展 |

### 实现状态（本轮已落地）

| 项 | 状态 | 位置 |
|---|---|---|
| Metadata 识别 + `--ttk-plugin` 早期拦截 | **已完成** | `scripts/init_run.py`（`TTK_PLUGIN_REQUIRED`） |
| companion 文档自动识别 + 快照 | **已完成** | `scripts/init_run.py`（`_auto_detect_companion_docs`） |
| 插件静态契约分析（`*_values`/metadata/aux） | **已完成** | `scripts/analyze_ttk_plugin.py` |
| `*_values` 以原生 list 写入 CSV attributes | **已完成** | `scripts/atk_to_ttk_aclnn.py::_format_attrs` |
| `metadataOptional` 无条件 `(-1,)` | **已完成** | `scripts/atk_to_ttk_aclnn.py` |
| `value_constraints.json` 候选提取 | **已完成** | `scripts/extract_value_constraints.py`（`review_required=true`，需终审） |
| `value_constraints.json` 终审 | **已完成** | `scripts/finalize_value_constraints.py`（`--confirm` 后 `origin=doc`） |
| `value_constraints.json` 校验 | **已完成** | `scripts/validate_artifacts.py value_constraints` |
| EXTRACT 编排（分析→候选→终审） | **已完成** | `scripts/prepare_plugin_values.py`（`--confirm` 终审） |
| EXTRACT/CHECK/UPDATE 流程接入 | **已完成（技能）** | `.opencode/skills/iterate-operator/SKILL.md` |
| doc → aux spec / value generator | **已完成（通用原语）** | `operator_aux_params.py`：`prefix_sum_prefix0` / `within_prefix` / `lt_scalar` |
| `*_values` materializer/validator | **已完成** | `materialize_group_list_data.py`：`indexer_cu_seqlens` / `indexer_seqused` / `indexer_cmp_residual` |
| checker/updater 双约束治理 | **已完成** | `value_constraints_update_state.py`（prepare/finalize）+ 技能规则 |
| 单 case 契约测试 | **已完成** | TTK attributes→kwargs 实测 1/1 PASS；`TND requires cu_seqlens_q` 消失 |

---

## 5. 总体设计

```text
PLAN
  ├─ 识别 Metadata 类型（companion / metadataOptional / prerequisite）
  ├─ 检查 --ttk-plugin；缺失 → TTK_PLUGIN_REQUIRED（不建 run / 停 PLAN）
  └─ 冻结 run_state（ttk_plugin、scenes、case_count…）

EXTRACT
  ├─ constraint-extractor → constraints.json（shape/presence/scalar）
  ├─ analyze_ttk_plugin.py → inputs/plugin_requirements.json
  │     （required_value_attrs / metadata_slot / file:line）
  └─ value-constraint 提取 → iter/value_constraints.json
        （首0/非递减/末值、seqused<=cu、cmpResidual<cmpRatio…）

CHECK / REPAIR
  ├─ 检查 constraints.json
  └─ 检查 value_constraints.json（与文档/companion/插件契约一致）

GENERATE
  ├─ Z3 → cases.json（只有 shape/presence/scalar）
  ├─ operator_aux_params 生成 specs：
  │     cu_seqlens_q_values / seqused_q_values / cmp_residual_k_values
  ├─ inject_aux_params → cases.json 增加 aux 输入项（JSON list）
  ├─ atk_to_ttk_aclnn.convert_file → cases_ttk.csv
  │     attributes 写入 *_values；metadataOptional 写 (-1,)
  ├─ materialize_csv(validator=indexer_*) → 校验并写回 attributes
  └─ validate_ttk_aclnn_csv → fail-closed

EXECUTE
  └─ ttk aclnn ... --plugin <assets>
       原插件 build_case_params 读 kwargs["*_values"]
       golden 生成 → copy_tensor 写回 TTK 张量
       npu_preprocess 填充 metadata

GATE → DIAGNOSE → 迭代（约束类回写 constraints/value_constraints；插件类止损）
```

---

## 6. 改动清单（按文件）

### 6.1 `scripts/init_run.py`
- 恢复/增加 companion 自动识别（同目录 `*Metadata*.md`，或主文档引用前置算子）。
- 增加 Metadata 类型判定：
  - companion role=prerequisite；
  - `function_signature` 含 `metadataOptional`；
  - constraints inputs 含 `metadataOptional`。
- 若 Metadata 类型且 `--ttk-plugin` 为空：
  - 返回 `ok=false, code=TTK_PLUGIN_REQUIRED, requires_user_action=true`；
  - message 明确“对应 ttk 命令的 `--plugin`”；
  - 不创建 run（或停 PLAN，禁止进 EXTRACT）。
- `constraints-only` 模式放行，不要求插件。

### 6.2 新增 `scripts/analyze_ttk_plugin.py`
- 输入：`--plugin <path>`（本地文件/目录；远端 `/` 路径先快照到 `inputs/ttk_plugin_snapshot/`）。
- 静态扫描（AST/regex，**不执行**插件）：
  - `kwargs.get("<name>_values")` / `kwargs["<name>_values"]`；
  - `AUX_TARGETS` / `__input__` / `apply_aux_params`；
  - `metadataOptional` / `(-1,)`；
  - `copy_tensor` shape 校验点。
- 输出 `inputs/plugin_requirements.json`：
  ```json
  {
    "plugin": "<path>",
    "plugin_sha256": "<sha256>",
    "metadata_slot": {"name": "metadataOptional", "sentinel": "(-1,)"},
    "required_value_attrs": [
      {"name": "cu_seqlens_q_values", "target": "cuSeqlensQOptional", "evidence": "impl/inputs.py:230"},
      {"name": "seqused_q_values", "target": "sequsedQOptional", "evidence": "impl/inputs.py:232"},
      {"name": "cmp_residual_k_values", "target": "cmpResidualKOptional", "evidence": "impl/inputs.py:234"}
    ]
  }
  ```
- 可选：模型读该 JSON + 插件源码，补全 `inputs/plugin_analysis.md`（语义解释）。

### 6.3 新增 `value_constraints.json`
- 位置：`iter_<N>/value_constraints.json`（或 `inputs/`，按最终约定）。
- 结构：
  ```json
  [
    {"param": "cuSeqlensQOptional",
     "rules": ["len==B+1", "first==0", "non_negative", "non_decreasing", "last==T1(TND)"],
     "src_txt_line": [140, 141, 427],
     "origin": "doc"},
    {"param": "sequsedQOptional",
     "rules": ["len==B", "0<=v", "v<=cu[b+1]-cu[b]"],
     "src_txt_line": [426, 427],
     "origin": "doc"},
    {"param": "cmpResidualKOptional",
     "rules": ["len==B", "0<=v<cmpRatio"],
     "src_txt_line": [436],
     "origin": "doc"}
  ]
  ```
- 由 EXTRACT 产出；`constraint-checker` 核对；`validate_artifacts.py` 增加子命令。
- **不进入** `constraints.json`。

### 6.4 `scripts/operator_aux_params.py`
- 扩展 spec 字段：
  - `value_kind`: `attr`（groupListData）| `tensor_values`（本方案）；
  - `value_attr`: 写入 CSV attributes 的键（如 `cu_seqlens_q_values`）；
  - `target_param` / `tensor_index` / `signature_index`；
  - `generator` / `validator` / `generator_options` / `evidence`。
- 新增检测分支：
  - 读 `plugin_requirements.json`；
  - 为每个 `required_value_attrs` 生成 spec（selected=true）；
  - 与 `groupListData` 检测互斥/去重。
- `_build_value` 新增 generator：
  - `indexer_cu_seqlens`：从目标 tensor shape（B+1）、layout、q/k shape 反推 B/T；首0、非递减、末值=T；
  - `indexer_seqused`：TND 用 cu 差分（保证 `<=`），PA 按容量，BSND 用 S2；
  - `indexer_cmp_residual`：长度 B，`i % cmpRatio`；仅 maskMode=3 且 cmpRatio!=1；
  - `indexer_block_table`（可选）：二维、首维 B、容量足够、block id 合法。
- 生成时读 `value_constraints.json` 做自检。

### 6.5 `scripts/generate_cases.py`
- 现有 TTK 分支已经：
  - `ensure_run_aux_specs` → `inject_aux_params_into_file`；
  - 对每个 spec 调 `materialize_csv(..., attr_name, validator, target_param, tensor_index)`。
- 只需保证新 spec 的 `validator/target_param/tensor_index` 被透传；
- 报告文件：`<attr_name>_materialize_report.json`；
- 任一 materialize 失败 → `SystemExit`，不进入 EXECUTE。

### 6.6 `scripts/atk_to_ttk_aclnn.py`
- 现有 `_format_attrs()` 已把 `aux=true` 输入写入 `attributes`；
- 保持 `metadataOptional` 无条件 `(-1,)`（即使 case 为 None）；
- 确认 aux 值以 list 还是 JSON string 写入 CSV（由单 case 探针决定）。

### 6.7 `scripts/materialize_group_list_data.py`
- 扩展 `materialize_csv()` 的 validator：
  - `indexer_cu_seqlens`：长度/首0/非递减/末值，对照 `tensor_view_shapes[target_index]`；
  - `indexer_seqused`：长度 B、`0<=v<=cu diff`、PA 容量；
  - `indexer_cmp_residual`：长度 B、`0<=v<cmpRatio`；
- 失败 `ok=false` + errors，生成阶段阻断。
- 可选：输出 `indexer_values_report.json`。

### 6.8 `scripts/validate_ttk_aclnn_csv.py`
- 增加 `*_values` 校验：
  - 长度与目标 tensor shape 一致；
  - 值域/单调/末值/`<=`/`<` 规则；
  - metadata 哨兵存在；
- 不通过 → invalid，生成阶段 fail-closed。

### 6.9 `scripts/execute_cases.py`
- 无需大改：`--plugin` / `run_state.ttk_plugin` 已有；
- 确认：
  - 手动 plugin 优先级最高；
  - 有 `aux_params_report.json` 时不要用 `golden.py` 覆盖 Metadata 插件。

### 6.10 checker / updater
- `constraint-checker`：同时检查 `constraints.json` 与 `value_constraints.json`；
- `constraint-repairer`：只修报告 open 项，分别修改两个文件；
- `constraint-updater`：执行反馈轮若发现值约束问题，回写 `value_constraints.json`（新路径），不要硬塞 `constraints.json`。

---

## 7. 端到端流程（扩展后）

### 7.1 入口
```text
/iterate-operator operator_docs/aclnnLightningIndexerV2.md \
  --max-iterations 3 --case-count 100 --scenes A2 \
  --test-framework ttk \
  --ttk-plugin /data/.../lightning_indexer_v2/tests/assets
```
- Metadata 类型 + 缺插件 → 立即 `TTK_PLUGIN_REQUIRED`。

### 7.2 PLAN / SCENE
- `init_run` 冻结 run_state、companion、prompt、ttk_plugin；
- `selection.json` → `scene_directive.md`（A2，无场景维度）。

### 7.3 EXTRACT
- `constraints.json`（shape/presence/scalar）；
- `plugin_requirements.json`（插件契约）；
- `value_constraints.json`（值级约束）；
- CLASSIFY。

### 7.4 CHECK / REPAIR
- 两类约束都通过才进 GENERATE。

### 7.5 GENERATE
- Z3 → `cases.json`；
- aux specs → 注入 `*_values`；
- `convert_file` → `cases_ttk.csv`（attributes + metadata `(-1,)`）；
- `materialize_csv` 校验并写回；
- `validate_ttk_aclnn_csv` 通过。

### 7.6 EXECUTE
- `ttk aclnn -i cases_ttk.csv --plugin <assets>`；
- 原插件读 `*_values`、golden 生成、`copy_tensor` 写回；
- PLOG 采集。

### 7.7 GATE / DIAGNOSE
- 结构/证据链/值报告门禁；
- 失败按类回写：
  - shape/presence → `constraints.json`；
  - value → `value_constraints.json`；
  - 插件/执行器 → 止损。

---

## 8. 产物契约

```text
runs/<run-id>/
  run_state.json
  inputs/
    <doc>.md
    <doc>Metadata.md
    prompt_v1.md
    selection.json
    scene_directive.md
    ttk_plugin_snapshot/           # 插件只读快照 + sha256
    plugin_requirements.json       # 插件契约
    value_constraints.json         # 值级约束（或 iter 级）
  iter_001/
    constraints.json
    extraction_provenance.json
    constraint_check.json
    relation_examples.json
    value_constraints.json
    cases.json                     # Z3 + aux 值
    aux_params_report.json
    *_materialize_report.json
    cases_ttk.csv                  # attributes 含 *_values + metadata(-1,)
    generation_summary.json
    execution_result.json
    execution_logs/remote_artifacts/
    analysis.json
    quality_gate.json
```

---

## 9. 实施计划

### P0：早期拦截（已完成）
- `init_run.py` Metadata 识别 + `--ttk-plugin` 缺失即失败；
- `constraints-only` 放行；
- 验收（已通过）：无插件时 init 返回 `TTK_PLUGIN_REQUIRED`，不创建 run/不进 EXTRACT。

### P1：插件分析（已完成）
- 新增 `analyze_ttk_plugin.py`；
- 输出 `plugin_requirements.json`（带 file:line + sha256）；
- 验收（已通过）：对 LLI V2 插件列出 5 个 `*_values` + metadata 槽位，并用 constraints 映射到
  `cuSeqlensQOptional` 等目标参数。

### P1.5：传输探针（已完成，关键）
- `*_values` 以原生 list 写入 CSV attributes（`'cu_seqlens_q_values': [0, 4, 8]`）；
- 用最小 TND case（q=T1=8、B=2、cuSeqlens=[0,4,8]、sequsedK=[4,4]）跑原插件；
- **实测通过**：TTK 把 attributes 解析为 list 并传入插件 kwargs，`TND requires cu_seqlens_q`
  消失，NPU Pass 1/1，精度 100%；
- 结论：不需要修改原插件 `list_value`；CSV attributes 原生 list 通道成立。

### P2：值约束（候选提取 + 校验已完成）
- `scripts/extract_value_constraints.py`：从主/伴随文档 + 插件契约产出候选规则
  （`origin=doc_candidate`、`review_required=true`），带 `src_txt_line`；
- `scripts/validate_artifacts.py value_constraints`：schema + generator 白名单校验；
- `detect_plugin_value_specs` 默认跳过 `review_required` 条目，避免未终审的候选驱动生成；
- **新增 `scripts/finalize_value_constraints.py`**：
  - 按 generator 原语过滤不兼容规则（如 `lt_scalar` 丢弃 `first_zero/non_decreasing`）；
  - 从 `rule_evidence` 重建 `src_txt_line`；
  - `--confirm` 显式确认后写 `origin=doc`、`review_required=false`；
  - 未确认时仍保持候选状态，生成侧自动跳过。
- 实测：LLI V2 候选经 finalize 后，cuSeqlens 保留 `first_zero/non_decreasing/length_b_plus_1/non_negative`，
  cmpResidual 只保留 `length_b/lt_ratio/non_negative`；`validate_artifacts value_constraints` 通过。
- 已补：`check-constraints` 步骤 M 复核候选；`update-constraints` 值级 findings 分流；
  `value_constraints_update_state.py` 提供 prepare/finalize 版本化。

### P3：aux generator + materializer（已完成主体）
- `operator_aux_params.py`：新增 `detect_plugin_value_specs` + 通用原语
  `prefix_sum_prefix0` / `within_prefix` / `lt_scalar`；
- `materialize_group_list_data.py`：新增 `indexer_cu_seqlens` / `indexer_seqused` /
  `indexer_cmp_residual` validator；
- `generate_cases.py`：`tensor_values` spec 不生成 `golden.py`，避免与 Metadata 插件冲突；
- 已用合成 case 验证：生成 `[0,5,10]` → CSV attributes 原生 list → materializer 校验通过；
  非法 `[0,5,9]` 被拒绝。
- 已完成：`prepare_plugin_values.py` 从文档+插件契约产出候选，`--confirm` 落
  `iter_N/value_constraints.json`；TTK attributes→kwargs 已实测（原生 list 被原插件读取）。
- 值化实测（2 case，原插件）：NPU 2/2 PASS、Precision 2/2 PASS。曾出现
  `block_num < sum(ceil(seqused_k/block_size))`，已由 `within_prefix` 的
  `block_budget_from`（PA 容量 cap）修复。

### P4：全量回归（待用户执行）
- 100 条生成/执行；
- 验收：不出现 `TND requires cu_seqlens_q` / `k shape mismatch` / metadata 槽位错误；
- 通过数不低于 095716 基线（51/100）。
- 已做 2 case 冒烟：生成成功、原插件读取 values、2/2 PASS。

---

## 10. 关键约束与校验规则（LLI V2）

| 参数 | 值级规则 | 来源 |
|---|---|---|
| cuSeqlensQ/K | len=B+1；首0；非负；非递减；TND 末值=T1/T2 | 主文档 140/141/427/429；Metadata 371 |
| sequsedQ/K | len=B；`0<=v`；TND `v<=cu[b+1]-cu[b]`；PA 容量 | 主文档 171/426-429；Metadata 387 |
| cmpResidualK | len=B；`0<=v<cmpRatio`；仅 maskMode=3 且 cmpRatio!=1 | 主文档 436；Metadata 138 |
| blockTable | 二维；首维 B；容量覆盖 sequsedK；block id ∈ [-1, block_num) | 主文档 190-191 |
| metadataOptional | TTK 槽位 `(-1,)`；插件填充 | 主文档 211/440；TTK 契约 |

> 注意：TND 下 `sequsedQ <= cu 差分` 是文档要求；原 golden 在 TND 分支**忽略 sequsedQ**，直接用 cu 差分。
> 若未来要测试 Padding 分支，需要额外修改 golden，不仅是提供值。

---

## 11. 风险与待确认

1. ~~CSV attributes → 插件 kwargs 的传输~~：已实测，TTK 把原生 list 作为 kwargs 传给插件。
2. ~~原插件 `list_value` 不解码字符串~~：生成器写原生 list，原插件无需修改。
3. **Metadata 插件与 `golden.py` 冲突**：`execute_cases` 必须保证手动 `--plugin` 优先。
4. **value_constraints 的治理**：新增 artifact 必须同步 validate/checker/contracts/quality gate。
5. **文档冲突**：metadata `[2048]` vs `(1024,)`、TND padding 未实现；记录冲突，不自动选边。
6. **插件只读分析**：不执行插件；插件变更需重新分析（sha256 记录）。
7. **范围控制**：第一版只做 LLI V2，不做通用插件推断框架。

---

## 12. 验收标准（Definition of Done）

1. Metadata 类型算子无 `--ttk-plugin` → 立即 `TTK_PLUGIN_REQUIRED`，不进入 EXTRACT。
2. `plugin_requirements.json` 能准确列出插件需要的 `*_values` 和 metadata 槽位。
3. `value_constraints.json` 覆盖文档值级要求，checker 通过。
4. `cases_ttk.csv` 的 attributes 含合法 `cu_seqlens_q_values` / `seqused_q_values` /
   `cmp_residual_k_values`，且 metadataOptional 为 `(-1,)`。
5. 原插件执行时不再出现 `TND layout requires cu_seqlens_q`、`cmp_residual_k` None、
   metadata 槽位错误。
6. `validate_ttk_aclnn_csv.py` 对非法 values fail-closed。
7. 100 条回归通过数不低于 51/100，且失败根因可归类（约束/生成器/插件）。
