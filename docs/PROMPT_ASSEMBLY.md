# ACLNN 提示词装配与冻结

默认 ACLNN 流程为：算子文档快照 → 文档预分析 → 知识路由 → 适用性判断 →
`base + applicable modules` → 冻结 `prompt_v1.md` 与组装记录。

## 事实源与分层

- 当前算子文档是常规算子事实的最高优先级来源；知识只用于防漏、规范表达和检查。
  例外是显式开启的 `source_analysis` 类模块：它是锁定源码版本的附加约束源，必须保留
  commit、章节、可信度和 `origin=source_analysis`，不得静默覆盖公开文档。
- `prompts/operator_constraints/base.md` 为 **canonical 直接编辑**的 ACLNN 提示词
  （保留流程与未迁移规则，不复制 Pydantic schema）；`v4` 为历史来源（一次性机械拆分已完成，
  原迁移工具 `build_aclnn_prompt_base.py` 已退场归档于 `archive/builders/`，不再 gate）。
- `knowledge/aclnn/manifest.json` 是 ACLNN 知识唯一清单，依次包含 foundation、common、
  feature/reference、exact operator 与默认关闭的 source_analysis。
- `knowledge/torch_npu` 继续由 `select_torch_npu_prompt.py` 独立装配，两套根目录不互扫。

## 冻结产物

ACLNN run 初始化会写：

- `inputs/prompt_preanalysis.json`：文档哈希、算子名、接口模式、平台与结构信号；
- `inputs/prompt_assembly.json`：base/manifest/模块/最终 prompt 哈希、顺序、命中证据和
  适用性结论；
- `inputs/prompt_v1.md`：不可变的完整提取上下文。

校验：

```text
python scripts/validate_prompt_assembly.py --record runs/<id>/inputs/prompt_assembly.json
```

显式 `--prompt` 与 torch_npu 维持各自原有路径；它们不会隐式加载 ACLNN 知识。

### 源码分析约束知识（显式开启）

`source_analysis` 是 ACLNN manifest 中独立的知识类别。它同时受两层门控：

1. 初始化时显式传入 `--source-analysis-knowledge`；
2. 模块的 `operator_name_eq` 与当前算子名精确匹配。

默认不传开关时，即使算子名命中也以 `reason=feature_disabled` 记录为未加载。示例：

```text
python scripts/init_run.py --doc operator_docs/aclnnGroupedMatmulV5.md \
  --source-analysis-knowledge
```

该开关不能与 `--prompt` 同用，也不适用于 torch_npu。是否开启会写入
`run_state.source_analysis_knowledge`、`prompt_assembly.json` 的
`knowledge.feature_flags` 与 `applicability.feature_flags`，首轮冻结后不在迭代中变化。

## 知识预校验（run 初始化前）

`scripts/init_run.py` 的 ACLNN 分支在装配前调用
`scripts/validate_aclnn_knowledge.validate(knowledge/aclnn)`，对 canonical 知识做一次
完整性预校验：manifest 字段、默认加载集、依赖闭包、跨 family 隔离、`operator_name_eq`
唯一性，以及 `reject_on` 触发器合法性与正/负向不矛盾。任一不通过即终止并输出：

```json
{"ok": false, "code": "ACLNN_KNOWLEDGE_INVALID", "errors": ["..."]}
```

torch_npu 分支待对称实现后接入 `validate_torch_npu_knowledge`（见 torch 装配契约节）。

## 重提取轮次与知识路由

约束闭环根因为 `constraint_extraction`、没有已确认补充且能定位 Prompt 规则缺口时，
才进入 `OPTIMIZE → EXTRACT` 循环。该循环的
关键不变式：

- **第 1 轮（EXTRACT）**：读 run 初始化冻结的 `prompt_v1.md`（含 `base + 命中知识`），
  路由只在 run 初始化（PLAN）阶段跑一次。
- **第 2+ 轮（re-EXTRACT）**：读上一轮 `optimize-prompt` 产的 `prompt_vN.md`，**不重走
  知识路由**。知识模块的命中集合（`prompt_assembly.json` 的 `module_ids`）保持首轮冻结
  值，提取器只消费当轮提示词文本。
- **知识库 canonical 变更不在迭代内生效**：`optimize-prompt` 的沉淀提案写到
  `prompt_update_proposals.json` / `prompt_update_decisions.json`，仅由主协调器在任务终态
  展示证据并经用户逐条批准后才提升到 `knowledge/aclnn/`。提升后必须重跑
  `validate_aclnn_knowledge` + 路由 + 组装冻结校验（`init_run` + `validate_prompt_assembly.py --record`），
  下个 run 才看到新知识。

这保证单 run 内的提取结果可复现（同一冻结快照），知识演进只在 run 之间、经人工批准发生。

## manifest 字段

`knowledge/aclnn/manifest.json` 字段含义：

| 字段 | 含义 |
| --- | --- |
| `schema_version` | manifest 自身结构版本，当前 `1.0`。 |
| `family` | `aclnn`；与 torch_npu 严格隔离，禁止跨 family 引用。 |
| `policy` | 装配策略语义。当前 `v4_split_operator_name_eq_source_analysis_opt_in` = v4 拆分（base + 知识模块）+ exact-operator 精确命中 + source_analysis 显式开关门控。 |
| `modules[].id` | 模块唯一标识，必须等于模块 frontmatter 的 `module:` 字段（`_load_modules` 校验）。 |
| `modules[].scope` | `foundation` / `common` / `feature` / `reference` / `operator` / `source_analysis`。 |
| `modules[].path` | 相对 `knowledge/aclnn/` 的模块文件路径。 |
| `modules[].default_load` | `true` 则无条件进装配集（6 个：official_basics / dimensions / allowed_range / implicit_parameters / platform_dtype / expression_language）；`false` 需 trigger 命中。 |
| `modules[].priority` | 装配顺序权重，高优先；同 `default_load` 内用于排序。 |
| `modules[].triggers` | 正向命中触发器，任一命中即加入候选集。kind ∈ `operator_name_eq` / `operator_name_regex` / `name_contains` / `doc_contains` / `format_any`。 |
| `modules[].reject_on` | 负向否决触发器（kind 同 triggers）。命中即从候选集移除，**负向优先**于 `default_load` 与正向 trigger；闭包不再拉取被否决模块的依赖。正/负向同 (kind,value) 视为矛盾，校验阶段拒绝。 |
| `modules[].depends_on` | 依赖模块 id 列表；命中模块会补拉依赖（闭包）。 |

`source_analysis` 模块必须 `default_load=false`，且必须且只能有一个
`operator_name_eq` trigger；开启全局开关不会让它跨算子加载。

foundation 与 feature 的区分：`foundation/*` 是官方原始概念参考（如
`broadcast_relation`、`sparse_mode_foundation`、`type_derivation`、
`type_conversion_foundation`、`quantization_intro`），按文档信号 trigger 命中；`features/*`
是派生约束规则（如何把概念落为 `expr_type` / `constraints_in_parameters`）。两者共存且
区分显示（`render_bundle` 各自带 `<!-- knowledge-module: <id> -->` 标记）。foundation
模块不进 `EXPECTED_DEFAULTS`（仍为 6）。

## torch_npu 装配契约

torch_npu 与 ACLNN 同构（manifest 驱动 + 冻结三产物），仅知识根与 family 隔离：

- `prompts/torch_npu_constraints/base.md` 为 **canonical 直接编辑**的 torch 提示词
  （`v3` 为历史来源，一次性机械拆分已完成；§3 的 JSON schema 列表替换为 `OperatorRule`
  模型契约 + 三条校验命令，§3.1（`ValueWithSrcText` 规则）与 §3.2（平台嵌套契约）保留。
  原迁移工具 `build_torch_npu_prompt_base.py` 已退场归档于 `archive/builders/`，不再 gate）。
- `knowledge/torch_npu/manifest.json` 是 torch 知识唯一清单：1 个 foundation 默认
  （`documentation_conventions`）+ 9 个 feature 家族规则 + 6 个 exact-operator
  （`npu_*`，`operator_name_eq` 门控）。`policy=v3_split_operator_name_eq_gated`。
- `scripts/route_torch_npu_knowledge.py` 镜像 ACLNN 路由：原型解析算子名、`reject_on`
  负向否决、依赖闭包；额外支持 `file_name_regex` 触发器，无 `format_any`。
- `scripts/select_torch_npu_prompt.py` 改为 manifest 路由（不再 glob），`assemble`
  写 `prompt_v1.md` + `prompt_preanalysis.json` + `prompt_assembly.json`（sha256 冻结），
  并保留 `PLATFORM_CONTRACT_MARKERS` 平台契约校验。
- `scripts/validate_torch_npu_knowledge.py` 镜像 ACLNN 校验：family=torch_npu、默认集
  ={`documentation_conventions`}、禁跨 family 引用 aclnn、`reject_on` 合法性。
- run 初始化（`init_run.py` torch 分支）装配前预校验 torch 知识，并在 run_state 一致
  产出 `prompt_preanalysis` / `prompt_assembly_record`（与 ACLNN 同字段）。

```text
# builder 已退场归档于 archive/builders/，base.md 直接编辑后跑下列校验：
python scripts/validate_torch_npu_knowledge.py
python scripts/validate_prompt_assembly.py --record runs/<id>/inputs/prompt_assembly.json
```

torch 不做 ACLNN 那样的 dimensions/allowed_range 概念深拆（torch v3 §6/§8 仍留 base，
torch 知识按 `operator_patterns/` 组织）——本次只统一「manifest + base(§3 抽取) + 冻结记录」
三件；概念深拆列为后续。



