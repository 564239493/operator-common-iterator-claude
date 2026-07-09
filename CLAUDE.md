# Claude Code 项目指令

本项目由 Claude Code CLI 原生编排。你是主协调器，不是隐藏在 Python
脚本里的第二个 LLM 调用层。业务推理必须通过项目 Skill 与专职 Agent 完成；
Python 只负责确定性的校验、用例生成、执行适配和调度留痕。

## 每次会话的第一原则

1. 先读取 `docs/WORKFLOW.md` 和 `docs/ARTIFACT_CONTRACTS.md`。
2. 用户要求查看能力时，执行 `/show-workforce`。
3. 用户要求迭代算子时，使用 `/iterate-operator`，不要临时发明另一套流程。
   用户要求处理目录中的全部算子时，使用 `/iterate-directory`，由它串行复用
   `/iterate-operator`。
4. 每次委派前在主会话输出：
   `调度 -> <agent> | 输入: ... | 预期产物: ...`
5. 每次委派完成后输出：
   `完成 <- <agent> | 结论: ... | 产物: ...`
6. 所有阶段只通过 `runs/<run-id>/` 下的文件交接，避免跨 Agent 的隐式上下文污染。

## Agent 调度表

| 阶段 | Agent | 预加载 Skill | 主要产物 |
|---|---|---|---|
| 约束提取 | `constraint-extractor` | `extract-constraints` | `constraints.json` |
| 源码校验（可选，EXTRACT 后每轮一次） | `source-analyst` | `analyze-source` | `source_evidence.json`、`constraints_patch.json` |
| 用例生成 | `case-generator` | `generate-cases` | `cases.json` |
| 用例执行 | `case-executor` | `execute-cases`、`atc-cpu-golden-derivation` | `execution_result.json` |
| 根因诊断 | `failure-analyst` | `diagnose-failure` | `analysis.json` |
| 提示词优化 | `prompt-optimizer` | `optimize-prompt` | `prompt_vN.md` |
| 质量门禁 | `quality-reviewer` | `validate-run` | `quality_gate.json` |

## 状态机

`PLAN -> EXTRACT -> GENERATE -> EXECUTE -> GATE`

- 全部通过：`SUCCESS`
- 有失败：`DIAGNOSE`
- `constraint_extraction`：`OPTIMIZE -> EXTRACT`，进入下一轮
- `generator_bug`：`STOP_GENERATOR_BUG`
- `executor_bug`：`STOP_EXECUTOR_BUG`
- 达到最大轮数：`MAX_ITERATIONS`

主协调器必须显式维护当前轮次和状态，不能跳过质量门禁。并行只用于同一阶段内
彼此无写冲突的只读检查；主流水线阶段有数据依赖，默认串行。

## 安全边界

- 不读取或输出 `.env`、`servers.json` 中的秘密。
- 默认使用 real 执行。缺少或无法解析 `servers.json` 时必须停止并提示用户配置，
  禁止静默回退 Mock；只有用户显式指定 `--mode mock` 才能使用 Mock。
- 算子文档可来自项目外路径；先只读复制到 `runs/<run-id>/inputs/`，所有 Agent
  使用项目内快照，禁止修改外部原文档。
- 项目权限采用 `dontAsk`：已批准操作直接执行，不弹出确认。
- 可读取项目外文件；Edit/Write/删除/移动/重定向写入只能作用于本项目目录。
- Agent 业务产物只能写当前 `runs/<run-id>/` 和提示词版本文件。
- 不自动提交、推送或删除文件。
- 约束、用例、执行结果和分析结果必须先过 `scripts/validate_artifacts.py`。
- 源码分析是可选增强项：仅当 `--source-root`（直传算子目录）或 `--src-tree`
  （operators-src 根，由 init_run 调 `locate_operator_source.locate_in_tree` 跨 ops-*
  子树按 aclnn 名自动定位算子目录）提供且非空时，`init_run.py` 把算子源码只读复制到
  `runs/<run-id>/inputs/src_snapshot/`，由 `source-analyst` 在每轮 EXTRACT 后校验约束
  类型/范围/表达式。两者均未给或定位未命中绝不阻断闭环，退回纯文档驱动。快照由
  `scripts/snapshot_source.py` 生成：种子覆盖 `op_host/`+`op_api/` 的
  `.cpp/.cc/.h/.hpp/.json` 与 `docs/aclnn*.md`（**不含** `op_kernel`/`op_graph`，
  设备执行/图定义代码不携带输入输出约束）；`--src-tree` 模式额外经 `#include` 闭包把算子
  目录外共享头（`common/inc/error_util.h`、`op_host/tiling_base.h` 等）拉到
  `src_snapshot/_closure/`（搜索范围收紧到算子所属 `ops-*` 子树，跳过 tests/stub 路径），
  写 `_closure/MANIFEST.json`（`unresolved_includes` 多为 SDK 头）。`--src-tree` 模式还在 `#include`
  闭包后做**声明头驱动 l0op impl 拉取**：闭包复制的 `aclnn_kernels/*.h`（含 `namespace l0op`）里声明的
  symbol，按定义在所属 `ops-*` 子树内定位 impl `.cpp`（`l0op::TransData`/`ViewCopy`/`Contiguous`/`Reshape`
  等 impl 实际在 conversion 族 `.cpp`，同子树，经链接期符号关联非 `#include`），整文件复制到
  `_closure/delegated/`（不追逐 impl 自身 `#include`；其内部 l0op 调用如 `l0op::Cast` 标
  `missing_evidence`），MANIFEST 增 `delegated_impl_count`/`delegated_symbols_resolved`/
  `delegated_symbols_unresolved`；`CommonOpExecutorRun` 在 opbase 跨仓库框架执行器不在 l0op 声明头，不拉。
  `--src-tree` 模式还在 l0op 拉取后做 **R1/S1/S2 legacy tiling 拉取**（`--backend-tree` 给定或默认
  `<src-tree>/canndev` 时）：算子 L0 impl（如 `transdata.cpp`）经 `OP_TYPE_REGISTER`/`INFER_SHAPE`/
  `ADD_TO_LAUNCHER_LIST_AICORE`/`l0op::<Symbol>` 发射 op-type 名，对端在 canndev `op_tiling/` 用
  `IMPL_OP_OPTILING_LEGACY(<Op>,..)` 登记 legacy 图模式 tiling（含 `OP_TILING_CHECK`）。R1 按 op-type 名
  在 backend_trees 建 `IMPL_OP*` 注册点索引，命中 `.cc` 整文件复制到 `_closure/legacy_optiling/` + 追其
  op-stem `#include`（`trans_data*.h`）；S1 对 legacy 头收声明函数名、在同目录 op-stem 前缀过滤的 `.cc` 找
  impl（高精度，跳通用名 `DoTiling`）；S2 按 op-stem 在 `op_tiling/` 找同前缀 co-member 兜底（补 S1 漏的
  static helper `.cc`），复制到 `_closure/comake/`。均一跳+上限+truncation 留痕。相关性交 source-analyst
  裁决（图模式 legacy，对 aclnn L0 路径不一定成立，命中调用点才提升并标 `path_relevance=graph_mode_inferred`）。
  MANIFEST 增 `legacy_optiling_opnames`/`s1_files`/`comake_files`/`*_truncated`/`backend_trees` 等。
  `--source-root` 单目录模式不闭包、不拉 impl，算子目录外共享头不可达 → 常触发 `source_evidence.missing_evidence`。`.cc` 主要见于
  asc-devkit 自定义算子，真实 CANN 算子目录用 `.cpp`。源码定位用
  `scripts/locate_operator_source.py`，确定性抽取用 `scripts/extract_source_constraints.py`
  （扫 `op_host`+`_closure` 的 `.cpp/.cc/.h/.hpp`，含 `OP_TILING_CHECK`/`OP_CHECK`/`OP_CHECK_IF`/
  `CHECK_COND`/`OP_CHECK_DTYPE_NOT_SUPPORT`/`OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE`/`OP_LOGE`（仅
  `ACLNN_ERR_PARAM` 族，`condition` 留空、抓 L0 impl `Check*` 参数校验消息）；`.h` 先剥 `#define` 续行块防匹配宏定义；每条带
  `owning_function`/`callees`/`unresolved_calls`/`soc_scope`），另产 `soc_branches`（平台维度：
  aclrtGetSocName 字面量 + SocVersion 枚举两类 SoC 分支 + if 块范围；raw_check 行号落入则 soc_scope
  收该 token，空=通用），patch 机械应用用 `scripts/apply_constraints_patch.py`
  （单轮内仅一次，失败回滚不重试）。source-analyst 按 `raw_check.soc_scope` 设 patch 的
  `target_platform`：空→`common`（生成器 fan-out 全 `product_support`）；非空→经
  `agent/generators/data_definition/soc_product_matrix.py`（用户产品-芯片对应表）映射命中的产品名；
  未映射 SoC 落 `source_evidence.unknown_socnames` 供用户补表。
- 约束条目的 `origin` 字段标记来源：`doc`（文档提取，默认）或 `source_analysis`
  （源码校验 patch 写入）。源码与文档冲突时以源码为准并在 `source_evidence.json`
  标注 `doc_error`，但 `constraints.json` 的最终写入仍只由
  `apply_constraints_patch.py` 机械完成，source-analyst 不直接写约束。
