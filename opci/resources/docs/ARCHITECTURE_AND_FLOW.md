# 项目架构与业务流程全景

本文档梳理 `operator-common-iterator-claude` 的完整目录结构、确定性分类和业务调用全流程。

## 1. 目录结构与确定性分类

```
operator-common-iterator-claude/
│
├── .claude/                          【Claude Code 专属编排层 · ❌ 非确定性】
│   ├── agents/                       6 个专职 Agent 定义（角色、工具、预加载 Skill）
│   │   ├── constraint-extractor.md   约束提取 Agent · LLM 推理
│   │   ├── case-generator.md         用例生成 Agent · 确定性脚本 wrapper
│   │   ├── case-executor.md          执行 Agent · 确定性脚本 + LLM 推导混合
│   │   ├── failure-analyst.md        诊断 Agent · LLM 推理
│   │   ├── prompt-optimizer.md       优化 Agent · LLM 推理
│   │   └── quality-reviewer.md       门禁 Agent · 确定性校验 + LLM 语义判断
│   │
│   ├── skills/                       10 个流程与阶段 Skill 定义
│   │   ├── iterate-operator/         主闭环编排 · Claude Code 专属（委派 Agent）
│   │   ├── iterate-directory/        目录批次编排 · Claude Code 专属（委派 Skill）
│   │   ├── extract-constraints/      约束提取指引 · LLM 推理（7 步流程含确定性校验）
│   │   ├── generate-cases/           用例生成指引 · 确定性（调 scripts/）
│   │   ├── execute-cases/            执行指引 · 确定性脚本 + LLM 推导
│   │   ├── atc-cpu-golden-derivation/ CPU golden 推导 · LLM 推理（1419 行知识+规则）
│   │   ├── diagnose-failure/         诊断指引 · LLM 推理
│   │   ├── optimize-prompt/          优化指引 · LLM 推理
│   │   ├── validate-run/             门禁指引 · 确定性校验 + LLM 语义判断
│   │   └── show-workforce/           展示注册表 · 确定性（调 show_registry.py）
│   │
│   ├── hooks/                        Claude Code 生命周期观测钩子
│   │   ├── trace_hook.py             调度事件 JSONL 记录 · ✅ 确定性
│   │   └── guard_project_writes.py   Bash 写入边界守卫 · ✅ 确定性
│   │
│   ├── settings.json                 项目权限 + sandbox + Hooks 配置 · 纯配置
│   └── runtime/                      运行时调度事件审计（不入库）· 运行时产物
│       └── schedule.jsonl            每行一个调度事件 JSON
│
├── agent/                            【保留的正式用例生成器 · ✅ 100% 确定性】
│   │                                 来自旧项目 operator-common-iterator
│   │                                 是 Z3 + pairwise 组合生成的核心业务逻辑
│   └── generators/
│       ├── facade.py                 公共入口 TestCaseGenerator → 委托 single_operator_handle
│       ├── operator_handle_main.py   single_operator_handle / batch_operator_handel 原始入口
│       ├── common_model_definition.py Pydantic 模型（OperatorRule 等）· constraints.json schema
│       ├── data_definition/
│       │   ├── common_models.py      公共数据模型
│       │   ├── constants.py          GlobalConfig、dtype 映射、角色体系等常量
│       │   └── param_models_def.py   RunPlatform、参数角色规则定义
│       ├── operator_param_models/
│       │   ├── case_generate.py      单平台用例生成
│       │   ├── batch_case_generate.py 批量生成
│       │   ├── param_dtype_models.py  dtype 建模
│       │   ├── param_shape_models.py  shape 建模
│       │   └── param_range_models.py  range 建模
│       ├── operator_param_combine/
│       │   ├── param_combination_generate.py  参数组合入口
│       │   └── pairwise_combination/
│       │       ├── pairwise_generator.py      pairwise 组合算法
│       │       ├── param_combination_generator.py 组合生成器
│       │       ├── attribute_domain.py        属性域建模
│       │       └── constraint_filter.py       约束过滤
│       ├── param_constraint_solve/
│       │   ├── z3_expression_solver_utils.py   Z3 solver 核心
│       │   ├── expression_preprocess_utils.py  表达式预处理
│       │   ├── param_var_definition.py         参数变量定义
│       │   ├── shape_dim_preprocess_utils.py   shape 维度预处理
│       │   ├── customize_expression_solver_utils.py 自定义求解
│       │   └── param_constraint_utils.py       约束工具
│       ├── coverage_statistics/
│       │   ├── coverage_analyzer.py   覆盖率分析
│       │   ├── modeling_coverage/     建模覆盖率计算
│       │   └── code_coverage/         代码覆盖率
│       ├── common_utils/
│       │   ├── common_dispatcher.py   通用分发器
│       │   ├── data_handle_utils.py   数据处理（JSONL→JSON 转换等）
│       │   └── logger_util.py         日志初始化
│       ├── atk_common_utils/
│       │   ├── case_config.py         CaseConfig 数据模型
│       │   ├── design_config.py       ATK 设计配置
│       │   ├── enums.py               ATK 枚举定义
│       │   └── logger_utils_atk.py    ATK 日志工具
│       └── configs/
│           ├── shape_definitions.json  Tensor shape 模型配置
│           └── global_role_definitions.json 参数语义角色定义
│
├── executer/                         【项目内 ATK 执行引擎 · ✅ 100% 确定性】
│   │                                 替代旧项目对 operator-agent 外部包的依赖
│   │                                 明确声明：no langchain_openai, no .env, no agent.nodes.*
│   ├── __init__.py                   导出 run_cases, ExecutionResult, ReportRecord 等
│   ├── runner.py                     执行编排器：mock / generate / real 三模式
│   │                                 包含 RunRequest, 平台选择, 服务器校验, ATK 命令构建
│   ├── ssh.py                        asyncssh 封装：connect, sftp_upload, scp_upload, run, download
│   ├── models.py                     ExecutionResult, ReportRecord, TaskReportData Pydantic 模型
│   ├── report_parser.py              ATK xlsx 报告解析（模糊列匹配, truthy/fail 判断）
│   └── resources/
│       ├── generator.py              cases_executor.py 代码生成器（Jinja2 模板, NZ 检测, 分布式检测）
│       ├── aclnn_api_template.py.j2  ATK 测试脚本 Jinja2 模板
│       ├── aclnnCalculateMatmulWeightSize.py.tpl    专用模板（跳过 CPU golden）
│       ├── aclnnCalculateMatmulWeightSizeV2.py.tpl  专用模板 V2
│       └── aclnn_extracted.txt       aclnn 签名参考表
│
├── scripts/                          【确定性 CLI 胶水层 · ✅ 100% 确定性】
│   │                                 替代旧项目 LangGraph 节点 + orchestrator.py 的编排
│   │                                 每个脚本对应一个原 LLM 节点的确定性部分
│   ├── init_run.py                   创建 run 目录 + run_state.json；校验文档/servers
│   ├── init_batch.py                 创建目录批次；扫描文档 + 初始化 batch_state.json
│   ├── batch_state.py                批次生命周期：claim / attach-run / complete / show
│   ├── generate_cases.py             调 facade.TestCaseGenerator 生成用例
│   ├── execute_cases.py              调 executer.runner 执行用例（mock/generate/real）
│   ├── validate_artifacts.py         全阶段产物校验（结构 + 语义）· 被所有 Agent 共用
│   ├── normalize_constraints.py      constraints.json 原地规范化（format/dtype/dimensions）
│   ├── runtime_config.py             共享：路径解析、prompt 版本发现、server 校验
│   ├── validate_project.py           项目脚手架校验（settings/agents/skills/docs）
│   └── show_registry.py              打印 Skills/Agents/调度拓扑
│
├── prompts/                          【约束提取提示词版本 · 纯文本（LLM 输入）】
│   ├── operator_constraints_extract_v1.md  版本 1
│   ├── operator_constraints_extract_v2.md  版本 2
│   └── operator_constraints_extract_v3.md  版本 3（当前最新）
│                                     init_run.py 按整数 N 自动选最新版本
│                                     迭代时 prompt-optimizer 生成 v(N+1)
│
├── knowledge/                        【CANN 领域知识 · 纯文本（LLM 参考）】
│   ├── common/
│   │   ├── broadcast.md              广播关系规则（约束提取时参考）
│   │   └── type_promotion.md         互推导/dtype 推导规则
│   └── operator_patterns/
│       └── ffn_v3.md                 FFNV3 算子专用提取规则
│
├── operator_docs/                    【算子文档样例 · 纯文本（LLM 输入）】
│   ├── aclnnAlltoAllMatmul.md
│   ├── aclnnBatchMatMulWeightNz.md
│   ├── aclnnCalculateMatmulWeightSize.md
│   ├── aclnnCalculateMatmulWeightSizeV2.md
│   ├── aclnnFFNV3.md
│   ├── aclnnGroupedMatmulV5.md
│   ├── aclnnNpuFormatCast.md
│   ├── aclnnReflectionPad1dBackward.md
│   ├── aclnnSwinAttentionScoreQuant.md
│   └── aclnnSwinTransformerLnQkvQuant.md
│
├── docs/                             【设计文档 · 纯文本（人读 + LLM 参考）】
│   ├── WORKFLOW.md                   流程设计与状态机
│   ├── ARTIFACT_CONTRACTS.md         产物契约与字段定义
│   ├── OBSERVABILITY.md              可观测性设计
│   ├── PERMISSIONS.md                权限边界设计
│   └── MIGRATION.md                  旧项目迁移说明
│
├── runs/                             【运行产物 · gitignored，运行时动态创建】
│                                     每个算子一个 <operator>-<timestamp>/ 目录
│                                     批次 runs/batches/<batch-id>/ 目录
│
├── requirements.txt                  【Python 依赖 · 纯配置】
│                                     pydantic, numpy, pyyaml, packaging, typing_extensions,
│                                     z3-solver, asyncssh, openpyxl, langgraph, torch
│
├── servers.example.json              【执行机配置模板 · 纯配置】
│
├── README.md                         【项目说明 · 纯文本】
│
├── CLAUDE.md                         【Claude Code 项目指令 · 纯文本】
│
└── .gitignore                        【Git 忽略规则 · 纯配置】
```

### 确定性分类总结

| 目录 | 是否确定性 | 来源 | 说明 |
|---|---|---|---|
| `.claude/agents/` | ❌ | 新写 | Agent 定义含 LLM 角色描述，是 Claude Code 专属编排 |
| `.claude/skills/` | ❌ 混合 | 新写 | 4 个纯 LLM 推理 + 2 个确定性 + 2 个编排 + 2 个混合 |
| `.claude/hooks/` | ✅ | 新写 | 确定性 Python 钩子脚本 |
| `.claude/settings.json` | ✅ | 新写 | 纯配置 |
| `agent/generators/` | ✅ | **旧项目** | 正式 Z3+pairwise 生成器，0 LLM 依赖 |
| `executer/` | ✅ | 新写（替代旧项目外部依赖） | SSH/ATK 执行引擎，0 LLM 依赖 |
| `scripts/` | ✅ | 新写（替代旧项目 LangGraph 节点） | 确定性 CLI 胶水，0 LLM 依赖 |
| `prompts/` | ✅ | 旧项目保留 + 迭代追加 | 纯文本，LLM 的输入而非逻辑 |
| `knowledge/` | ✅ | 新写 | CANN 颟域知识，纯文本参考 |
| `operator_docs/` | ✅ | 旧项目保留 | 纯文本输入 |
| `docs/` | ✅ | 新写 | 纯文本设计文档 |
| `runs/` | ✅ | 运行时产物 | JSON/MD 文件，确定性脚本读写 |

## 2. 业务调用全流程

### 2.1 入口

两种入口方式：

```text
入口 1: 单算子
  用户 → /iterate-operator operator_docs/aclnnFoo.md --max-iterations 3 --case-count 10

入口 2: 目录批次
  用户 → /iterate-directory operator_docs --max-iterations 3 --case-count 10
```

### 2.2 目录批次流程（入口 2）

```text
/iterate-directory
  │
  ├─① python scripts/init_batch.py <directory> ...
  │   ├─ resolve_input_path(directory)
  │   ├─ find_latest_operator_prompt()      ← 从 prompts/ 选最新版本
  │   ├─ validate_server_config()           ← real 模式校验 servers.json
  │   ├─ directory.glob(args.glob)          ← 扫描文档文件
  │   ├─ 创建 runs/batches/<batch-id>/
  │   └─ 写入 batch_state.json              ← 冻结参数 + 文档列表 + PENDING 状态
  │
  ├─② 展示计划：文档数、参数、终止条件
  │
  ├─③ 循环：python scripts/batch_state.py --batch-dir <dir> claim
  │   ├─ action=start → Skill 调用 /iterate-operator <doc_source> --batch-dir <dir>
  │   ├─ action=resume → 读取 run_state.json → 从中断状态继续 /iterate-operator
  │   ├─ action=complete → 停止循环，展示 batch_summary.json
  │   │
  │   └④ 每个 /iterate-operator 完成后
  │   ├─ python scripts/batch_state.py --batch-dir <dir> complete
  │   ├─ 标记该算子为 COMPLETED + terminal_state
  │   └─ 再次 claim → 认领下一个 PENDING 算子
  │   │
  │   └⑤ 所有算子终态 → 报告总数/成功/失败
  │   └⑥ 中断恢复：/iterate-directory --batch-dir runs/batches/<id>
  │      └→ batch_state.py show → 定位当前 RUNNING 项 → 恢复该 run
  │
  └→ 每个算子内部执行 /iterate-operator 流程（见下方）
```

### 2.3 单算子完整流程（入口 1）

#### Phase 0: PLAN — 初始化

```text
/iterate-operator
  │
  ├─① 解析参数
  │   operator_doc, prompt(可选), max-iterations=5, case-count=10, mode=real
  │
  ├─② python scripts/init_run.py --doc <doc> --prompt <prompt> ...
  │   ├─ resolve_input_path(doc)              ← 支持项目外绝对路径
  │   ├─ find_latest_operator_prompt()        ← 未传 --prompt 时自动选最新版
  │   ├─ 校验文档是否存在
  │   ├─ 校验 prompt 是否存在
  │   ├─ validate_server_config()             ← real 模式校验 servers.json
  │   │   └→ 缺配置 → 返回 requires_user_action → 主协调器停止并提示用户
  │   ├─ 创建 runs/<operator>-<timestamp>/ 目录
  │   │   ├─ runs/<id>/inputs/                ← 只读快照目录
  │   │   ├─ runs/<id>/iter_001/              ← 第一轮产物目录
  │   │   ├─ 复制外部文档 → inputs/<doc>.md   ← 只读快照
  │   │   └─ 复制 prompt → inputs/prompt_v1.md ← 只读快照
  │   └─ 写入 run_state.json
  │       { state: "PLAN", current_iteration: 1, history: [...] }
  │
  ├─③ 如果 --batch-dir 存在
  │   python scripts/batch_state.py --batch-dir <dir> attach-run --run-dir <dir>
  │
  ├─④ 主协调器展示完整计划
  │   列出: Agent 链 → 输入/输出 → 终止条件
  │
  └→ 进入 EXTRACT
```

#### Phase 1: EXTRACT — 约束提取

```text
主协调器 → 委派 constraint-extractor Agent
  │
  ├─ Agent 加载 extract-constraints Skill
  │
  ├─① LLM 逐节阅读 inputs/<doc>.md + inputs/prompt_v1.md
  │   ├─ 判断一段式/两段式（看函数原型是否含 GetWorkspaceSize）
  │   ├─ 按提示词规则提取参数、约束、dtype、format、shape、取值范围
  │   ├─ 跨参数约束写入 constraints_in_parameters
  │   └─ 产出 constraints.json 到 iter_001/
  │
  ├─② python scripts/normalize_constraints.py iter_001/constraints.json
  │   └─ 原地修改: Tensor format → list, 非 Tensor dimensions → [],
  │      空 dtype fallback → 按类型注入默认 dtype
  │
  ├─③ python scripts/validate_artifacts.py constraints iter_001/constraints.json
  │   └→ 校验失败 → Agent 依据错误修正 constraints.json
  │   └→ 重新 normalize → 重新 validate
  │   └→ 最多 3 次 → 仍失败 → 返回阻断原因
  │
  └→ 完成 ← constraints.json 路径 + 校验结果 + 关键约束摘要
```

#### Phase 2: GENERATE — 用例生成

```text
主协调器 → 委派 case-generator Agent
  │
  ├─ Agent 加载 generate-cases Skill
  │
  ├─① 确认 constraints.json 已通过校验
  │
  ├─② python scripts/generate_cases.py
  │     --constraints iter_001/constraints.json
  │     --output iter_001/cases_<platform>.json
  │     --count 10
  │     --seed 42
  │     --iter-dir runs/<run-id>/iter_001
  │   │
  │   └─ scripts/generate_cases.py 内部调用链：
  │     ├─ json.loads(constraints.json)
  │     ├─ scripts.normalize_constraints.normalize_constraints(constraints) ← 再次规范化
  │     ├─ from agent.generators.facade import TestCaseGenerator
  │     ├─ TestCaseGenerator(constraints, seed=42)
  │     │   ├─ 提取 operator_name, product_support
  │     │   └─ 为每个 platform:
  │     │       ├─ generator.generate_for_platform(platform, count=10)
  │     │       │   └→ agent.generators.operator_handle_main.single_operator_handle()
  │     │       │       ├─ OperatorRule(**constraints) ← Pydantic 校验
  │     │       │       ├─ 构建 PairwiseParamCombinationGenerator
  │     │       │       │   ├─ attribute_domain.py: 属性域建模
  │     │       │       │   ├─ constraint_filter.py: 约束过滤
  │     │       │       │   ├─ pairwise_generator.py: pairwise 组合
  │     │       │       │   └─ Z3 solver: param_constraint_solve/z3_expression_solver_utils.py
  │     │       │       │       └→ 求解 inter-parameter 约束表达式
  │     │       │       ├─ operator_param_models/: 逐参数生成 dtype/shape/range
  │     │       │       ├─ 覆盖率统计
  │     │       │       └→ 返回 list[CaseConfig]
  │     │       └─ JSONL checkpoint → JSON 转换 → cases_<platform>.json
  │     │
  │     └─ 写入 iter_001/generation_summary.json
  │         { operator_name, platforms, total, per_platform_files, ... }
  │
  ├─③ python scripts/validate_artifacts.py cases iter_001/cases_<platform>.json
  │   └→ 禁止手工补造失败用例；保留 generator_bug 证据
  │
  └→ 完成 ← 数量 + 平台 + 产物路径 + 错误摘要
```

#### Phase 3: EXECUTE — 用例执行

real 模式有三子步骤，mock 模式直接标记：

```text
主协调器 → 委派 case-executor Agent
  │
  ├─ Agent 加载 execute-cases + atc-cpu-golden-derivation Skills
  │
  ════════════════════════════════════════════════
  real 模式流程（三子步骤）
  ════════════════════════════════════════════════
  │
  ├─ Step 3a: generate（生成 executor + expanded）
  │   python scripts/execute_cases.py --generate
  │     --cases iter_001/cases_<platform>.json
  │     --output iter_001/generate_result.json
  │     --doc inputs/<doc>.md
  │     --operator <op_name>
  │     --server-config servers.json
  │     --run-id <run_id>
  │   │
  │   └→ scripts/execute_cases.py 内部：
  │     ├─ validate_server_config() ← 只校验字段存在（不校验 placeholder）
  │     ├─ _load_operator_supported_platforms(iter_dir) ← 从 constraints/summary 读平台
  │     ├─ _select_server_for_execution() ← 按文件顺序匹配
  │     ├─ from executer.runner import RunRequest, run_cases
  │     ├─ RunRequest(cases_path, server_info, operator_name, run_id, ...)
  │     ├─ run_cases("generate", cases, request=request)
  │     │   └→ executer/runner.py:
  │     │       ├─ executer.resources.generator.Generator
  │     │       │   ├─ 读 cases.json + aclnn 签名表
  │     │       │   ├─ Jinja2 渲染 aclnn_api_template.py.j2
  │     │       │   ├─ NZ 格式检测 + 分布式检测
  │     │       │   ├─ 生成 cases_executor.py（含 dummy # TODO: CPU_GOLDEN 占位）
  │     │       │   └→ 生成 cases_expanded.json
  │     │       └→ 返回 generate_result.json
  │     │           { status: "generate", generate_artifacts: [...], ... }
  │     │
  │     └→ 产出:
  │         iter_001/cases_executor.py   ← ATK 测试脚本（含 dummy CPU golden）
  │         iter_001/cases_expanded.json ← 展开后的用例数据
  │
  ├─ Step 3b: CPU golden 推导（LLM 步骤）
  │   │
  │   ├─ Agent 调用 atc-cpu-golden-derivation Skill
  │   │   ├─ 1419 行规则知识：
  │   │   │   ├─ 签名解析规则（C++ type → Python type 映射）
  │   │   │   ├─ PyTorch 函数名推导规则（6 种规则 A-F）
  │   │   │   ├─ 参数映射规则
  │   │   │   ├─ 广播处理规则（broadcast 差异处理）
  │   │   │   ├─ dtype 对齐规则（fp32 计算 + NPU SupportInfo 匹配）
  │   │   │   ├─ 分布式算子规则（torch.distributed）
  │   │   │   ├─ 特殊算子跳过规则（CalculateMatmulWeightSize 等）
  │   │   │   └─ 验证清单（语法、shape、dtype、NPU 特定参数排除）
  │   │   │
  │   │   ├─ LLM 读取 inputs/<doc>.md（参数约束）
  │   │   ├─ LLM 读取 cases_executor.py（签名 + dummy 块）
  │   │   ├─ LLM 替换 # TODO: CPU_GOLDEN ... # END_CPU_GOLDEN 之间的 dummy 块
  │   │   │   └→ 替换 _dummy_output 和 # [FALLBACK] 标记
  │   │   │   └→ 写入真实 torch.* 计算
  │   │   │   └→ 保持所有现有 import 行不变
  │   │   │   └→ 使用 _get_param(name) 提取参数
  │   │   │   └→ 输出 dtype 按 NPU SupportInfo 对齐
  │   │   │
  │   │   └→ 自检（必须全过才进 real-run）：
  │   │       ├─ grep -E "_dummy_output|FALLBACK|TODO: CPU_GOLDEN" cases_executor.py
  │   │       │   └→ 必须 0 命中
  │   │       ├─ python -c "import ast; ast.parse(...)"
  │   │       │   └→ 必须 exit 0
  │   │       ├─ python scripts/validate_artifacts.py executor iter_001/cases_executor.py
  │   │       │   └→ 必须 valid:true
  │   │       │
  │   │       └→ 失败 → 重试推导最多 3 次
  │   │       └→ 仍失败 → 写 execution_result.json
  │   │           { status: "error", engine_error: "CPU golden 推导未完成" }
  │   │           → 交给 failure-analyst 诊断
  │
  ├─ Step 3c: real-run（上传 + 远程执行 + 结果下载）
  │   python scripts/execute_cases.py --mode real
  │     --cases iter_001/cases_<platform>.json
  │     --output iter_001/execution_result.json
  │     --doc inputs/<doc>.md
  │     --operator <op_name>
  │     --server-config servers.json
  │     --run-id <run_id>
  │   │
  │   └→ scripts/execute_cases.py 内部：
  │     ├─ validate_server_config() ← real 模式完整校验（含 placeholder 检测）
  │     │   └→ 缺配置 → 返回 requires_user_action → 停止提示用户
  │     ├─ validate_server_info(selected_server) ← 校验 IP/username/password 非占位符
  │     ├─ _select_server_for_execution() ← 按文件顺序匹配平台
  │     ├─ RunRequest(cases_path, server_info, operator_name, ...)
  │     ├─ run_cases("real", cases, request=request)
  │     │   └→ executer/runner.py:
  │     │       ├─ 检查 iter_dir 中是否有 generate 产物
  │     │       ├─ 检查 cases_executor.py 是否有 dummy 标记 ← 如有则报错停止
  │     │       ├─ executer.ssh.connect(server_info) ← asyncssh 连接
  │     │       ├─ executer.ssh.sftp_upload(
  │     │       │     cases_executor.py, cases_expanded.json, cases.json)
  │     │       ├─ executer.ssh.run(atk_command) ← 远程执行 ATK
  │     │       ├─ executer.ssh.find_latest_output_dir() ← 找最新产出目录
  │     │       ├─ executer.ssh.sftp_download(xlsx + log) ← 下载结果
  │     │       ├─ executer.report_parser.parse_xlsx_report(xlsx_path)
  │     │       │   ├─ 模糊列匹配（task_id, expect, actual, result）
  │     │       │   ├─ truthy/fail 判断
  │     │       │   └→ list[ReportRecord]
  │     │       ├─ 构建 ExecutionResult
  │     │       │   { status, mode, passed, failed, total, records, engine_error }
  │     │       └→ 返回 execution_result.json shaped dict
  │     │
  │     └→ 写入 iter_001/execution_result.json
  │
  ════════════════════════════════════════════════
  mock 模式流程（跳过 generate/推导/SSH）
  ════════════════════════════════════════════════
  │
  │   python scripts/execute_cases.py --mode mock
  │     --cases iter_001/cases_<platform>.json
  │     --output iter_001/execution_result.json
  │   │
  │   └→ executer.runner.run_cases("mock", cases)
  │       └→ 直接标记结果（每隔 N 条标记 1 条失败）
  │       └→ 不涉及 SSH/ATK/推导
  │
  ├─ 校验
  │   python scripts/validate_artifacts.py execution iter_001/execution_result.json
  │   └→ passed + failed = total; engine_error 非空不能宣称成功
  │
  └→ 完成 ← passed/failed/total + 执行模式 + 产物路径 + engine_error
```

#### Phase 4: GATE — 质量门禁

```text
主协调器 → 委派 quality-reviewer Agent
  │
  ├─ Agent 加载 validate-run Skill
  │
  ├─① 确定性校验（并行，只读）
  │   ├─ python scripts/validate_artifacts.py constraints iter_001/constraints.json
  │   ├─ python scripts/validate_artifacts.py cases iter_001/cases_<platform>.json
  │   ├─ python scripts/validate_artifacts.py execution iter_001/execution_result.json
  │   ├─ python scripts/validate_artifacts.py executor iter_001/cases_executor.py (real 模式)
  │   │
  │   ├─② LLM 语义检查
  │   │   ├─ constraints 中所有非空 expr 通过 Python AST 校验？
  │   │   ├─ range 边界不含 null？
  │   │   ├─ 数值范围用不等式而非嵌套列表？
  │   │   ├─ cases 数量与 generation_summary 一致？
  │   │   ├─ passed + failed = total？
  │   │   ├─ execution records 中 case id 可回溯到 cases？
  │   │   ├─ analysis root_cause 属于固定枚举？
  │   │   ├─ 一段式算子合法？
  │   │   └→ is_single_function_mode 字段已废弃，命中即阻断
  │   │
  │   ├─③ 跨文件一致性
  │   │   ├─ next_state 与根因/通过统计一致？
  │   │   ├─ blocking_issues 与 status 一致？
  │   │
  │   └─④ real 模式额外: CPU golden 推导门禁
  │       ├─ validate_artifacts.py executor → 检查 dummy 标记残留
  │       └→ 有残留 → blocking_issues 非空 → status=blocked
  │
  └─⑤ 写入 quality_gate.json
      { status, checks, blocking_issues, next_state }
      │
      ├─ 全部通过 → next_state = SUCCESS
      ├─ 有 blocking → next_state = BLOCKED
      └─ 有用例失败 → next_state → DIAGNOSE
  │
  └→ 完成 ← quality_gate.json 路径 + next_state
```

#### Phase 5: 判断分流

```text
主协调器读取 quality_gate.json 的 next_state
  │
  ├─ SUCCESS → 更新 run_state → 结束 ✅
  │
  ├─ BLOCKED → 停止，提示用户 ⛔
  │
  └─ 有用例失败 → 进入 DIAGNOSE ↓
```

#### Phase 6: DIAGNOSE — 根因诊断

```text
主协调器 → 委派 failure-analyst Agent
  │
  ├─ Agent 加载 diagnose-failure Skill
  │
  ├─① 按顺序读取（只用落盘事实，不接收提取 Agent 隐藏推理）：
  │   ├─ inputs/prompt_v1.md          ← 当前提示词
  │   ├─ inputs/<doc>.md              ← 原始算子文档
  │   ├─ iter_001/constraints.json    ← 提取出的约束
  │   ├─ iter_001/cases.json          ← 生成的用例
  │   ├─ iter_001/cases_expanded.json ← 展开后的用例（如果存在）
  │   ├─ iter_001/execution_result.json ← 执行结果
  │   │
  │   ├─② 先检查 engine_error
  │   │   └→ engine_error 非空 → 优先归 executor_bug
  │   │
  │   ├─③ 检查生成用例是否违反已提取约束
  │   │   └→ cases 中的参数值超出 constraints 定义的 range/enum？
  │   │   └→ 违反 inter-parameter 约束？
  │   │   └→ 但先检查约束本身是否遗漏了文档语义
  │   │
  │   ├─④ 检查约束是否遗漏或误解文档
  │   │   ├─ allowed_range_value.type=range 含 null 边界？
  │   │   ├─ expr 用嵌套列表而非不等式？
  │   │   ├─ 约束遗漏了可可靠推导的语义？
  │   │   │   └→ epsilon 被描述为"除0保护值"但未提取严格正值？
  │   │   └→ 只要约束遗漏足以解释失败 → 主根因 constraint_extraction
  │   │   └→ 生成器健壮性问题只作次要记录 generator_issue
  │   │
  │   ├─⑤ 对比 cases.json 与 cases_expanded.json
  │   │   ├─ 标量 range_values + length → 合法（表示每个元素共用）
  │   │   ├─ 不能仅凭 range_values 是标量就判 generator_bug
  │   │   ├─ 展开逻辑本身有错 → executor_bug
  │   │   └→ 展开正确但仍有失败 → 继续查找真实根因
  │   │
  │   ├─⑥ 证据不足 → 保守归入 executor_bug
  │   │
  │   └─⑦ 写入 iter_001/analysis.json
  │       {
  │         root_cause: "constraint_extraction | generator_bug | executor_bug",
  │         analysis: "根因摘要",
  │         specific_issues: ["带 case id 或文档证据的问题"],
  │         modified_sections: [],
  │         generator_issue: "",
  │         executor_issue: ""
  │       }
  │
  ├─⑧ python scripts/validate_artifacts.py analysis iter_001/analysis.json
  │   └→ root_cause 必须属于 {constraint_extraction, generator_bug, executor_bug}
  │
  └→ 完成 ← root_cause + specific_issues + 产物路径
```

#### Phase 7: 根因分流

```text
主协调器读取 analysis.json 的 root_cause
  │
  ├─ constraint_extraction → 进入 OPTIMIZE ↓
  │
  ├─ generator_bug → STOP_GENERATOR_BUG ⛔
  │   └→ 更新 run_state → 结束并报告
  │
  └─ executor_bug → STOP_EXECUTOR_BUG ⛔
      └→ 更新 run_state → 结束并报告
```

#### Phase 8: OPTIMIZE — 提示词优化（仅 constraint_extraction 根因）

```text
主协调器 → 委派 prompt-optimizer Agent
  │
  ├─ Agent 加载 optimize-prompt Skill
  │
  ├─① 前置检查: analysis.json.root_cause == "constraint_extraction"
  │   └→ 不是 → 拒绝工作
  │
  ├─② LLM 读取:
  │   ├─ inputs/prompt_v1.md (或当前版本的 prompt)
  │   ├─ iter_001/analysis.json
  │   │
  │   ├─③ LLM 只修改 specific_issues 支持的章节
  │   │   ├─ 保留原提示词整体结构和无关规则
  │   │   ├─ 不硬编码当前算子名称的特例
  │   │   └→ 产出:
  │   │       iter_001/prompt_v2.md          ← 新版完整提示词
  │   │       iter_001/prompt_changes_v2.md  ← 变更说明
  │   │
  │   ├─④ 同时写入全局 prompts/ 目录
  │   │   ├─ prompts/operator_constraints_extract_v(N+1).md ← 新版本对后续算子也可用
  │   │
  │   └─⑤ 更新 run_state.json
  │       ├─ current_iteration: 2
  │       ├─ current_prompt: iter_002/inputs/prompt_v2.md ← 新版本
  │       ├─ state: EXTRACT
  │       ├─ history: [..., { state: "OPTIMIZE" → "EXTRACT" }]
  │
  └→ 完成 ← 新 prompt 路径 + 变更说明
```

#### Phase 9: 第二轮 EXTRACT（使用新 prompt）

```text
├─ 创建 iter_002/ 目录
│   ├─ 复制 prompt_v2.md 到 iter_002/inputs/ (或更新 run_state 的 current_prompt)
│   │
│   └→ 重复 Phase 1~8
│       ├─ EXTRACT: 使用 prompt_v2 提取约束
│       ├─ GENERATE: 用新约束生成用例
│       ├─ EXECUTE: 执行新用例
│       ├─ GATE: 校验
│       └→ SUCCESS → 结束
│       └→ 失败 → DIAGNOSE → ...
│
├─ 循环直到:
│   ├─ SUCCESS ✅
│   ├─ max_iterations 达上限 → MAX_ITERATIONS ⛔
│   ├─ generator_bug → STOP_GENERATOR_BUG ⛔
│   ├─ executor_bug → STOP_EXECUTOR_BUG ⛔
│   └→ blocking → BLOCKED ⛔
```

### 2.4 全流程总览图

```text
用户 ── /iterate-operator ──→ 主协调器 (Claude Code 主会话)
  │
  ├─ PLAN ──→ scripts/init_run.py ──→ run_state.json (state=PLAN)
  │             ├─ 校验文档/servers.json
  │             └→ 创建 runs/<id>/ + 快照
  │
  ├─ EXTRACT ──→ constraint-extractor Agent ──→ constraints.json
  │               ├─ LLM 读文档+prompt
  │               ├─ normalize_constraints.py
  │               └→ validate_artifacts.py (最多3次自修正)
  │
  ├─ GENERATE ──→ case-generator Agent ──→ cases_<platform>.json + generation_summary.json
  │               ├─ generate_cases.py
  │               │   └→ facade.TestCaseGenerator
  │               │       └→ single_operator_handle
  │               │           └→ OperatorRule + Pairwise + Z3 solver
  │               └→ validate_artifacts.py
  │
  ├─ EXECUTE ──→ case-executor Agent ──→ execution_result.json
  │               │  real 模式:
  │               ├─ Step a: execute_cases.py --generate
  │               │   └→ executer.resources.generator ──→ cases_executor.py + cases_expanded.json
  │               ├─ Step b: atc-cpu-golden-derivation Skill (LLM)
  │               │   └→ 替换 dummy 块 → 自检 → 通过才继续
  │               ├─ Step c: execute_cases.py --mode real
  │               │   └→ executer.ssh.* (connect/upload/run/download)
  │               │   └→ executer.report_parser ──→ ExecutionResult
  │               │  mock 模式:
  │               └─ execute_cases.py --mode mock ──→ 直接标记
  │
  ├─ GATE ──→ quality-reviewer Agent ──→ quality_gate.json
  │            ├─ validate_artifacts.py × N
  │            ├─ LLM 语义检查
  │            └→ next_state 决定走向
  │
  ├─ SUCCESS ──────────────────────────────────────→ 结束 ✅
  │
  ├─ DIAGNOSE ──→ failure-analyst Agent ──→ analysis.json
  │                ├─ LLM 读 全部落盘证据
  │                ├─ 根因分类 (三选一)
  │                └→ validate_artifacts.py
  │
  ├─ 根因分流:
  │   ├─ constraint_extraction ──→ OPTIMIZE ──→ prompt-optimizer Agent
  │   │                                ├─ LLM 优化提示词
  │   │                                └→ prompt_v(N+1).md
  │   │                                └→ 回到 EXTRACT (下一轮)
  │   ├─ generator_bug ──→ STOP_GENERATOR_BUG ⛔
  │   └─ executor_bug ──→ STOP_EXECUTOR_BUG ⛔
  │
  └─ MAX_ITERATIONS (达到上限) ⛔
```

### 2.5 关键数据流

```text
inputs/<doc>.md + prompt_v1.md
          │
          ↓ (LLM 读取)
   constraints.json
          │
          ↓ (Z3+pairwise 生成)
   cases_<platform>.json + generation_summary.json
          │
          ↓ (generate → 代码生成)
   cases_executor.py (含 dummy) + cases_expanded.json
          │
          ↓ (LLM CPU golden 推导)
   cases_executor.py (dummy 已替换为真实 torch 计算)
          │
          ↓ (SSH 上传 + ATK 远程执行 + 结果下载)
   execution_result.json
          │
          ↓ (校验 + 语义检查)
   quality_gate.json → next_state
          │
          ↓ (如果失败)
   analysis.json → root_cause
          │
          ↓ (如果是 constraint_extraction)
   prompt_v(N+1).md → 回到 constraints 提取
```

每个箭头处都有 `scripts/validate_artifacts.py` 校验，不合格则阻断。

### 2.6 状态机终态

| 终态 | 含义 | 触发条件 |
|---|---|---|
| `SUCCESS` | 全部用例通过 | GATE 全部通过 |
| `MAX_ITERATIONS` | 达到轮次上限 | 超过 max-iterations 仍未成功 |
| `STOP_GENERATOR_BUG` | 生成器代码 bug | DIAGNOSE 根因 = generator_bug |
| `STOP_EXECUTOR_BUG` | 执行器/环境 bug | DIAGNOSE 根因 = executor_bug |
| `BLOCKED` | 产物不合法 | GATE 发现 blocking_issues |

只有 `constraint_extraction` 根因才会循环（OPTIMIZE → EXTRACT），其余根因立即止损。
