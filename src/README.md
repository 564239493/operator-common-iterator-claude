# 算子测试工作台 · 可视化与人工约束审核（src/）

独立于业务代码的只读可视化：Python 标准库小服务实时解析 `runs/`、`.opencode/` 产物，
向 Vue3 + Element Plus 前端提供读取接口。新增的人工约束提交只写所选轮次的 `constraints_copy.json`，不改原约束、任务状态、知识库，也不启动执行。

## 结构

```
src/
├── serve.py            # 唯一入口（纯标准库，零 pip 依赖）
├── workbench/          # 后端：路由 + 只读适配层（adapters/）+ 解析器（parsers/）
├── web/                # 前端 Vite 工程（Vue3 + Element Plus）
│   └── dist/           # 构建产物（git 忽略，由 Python 服务托管）
└── tests/              # 后端单测（unittest，含真实样例 run 冒烟）
```

## 启动

```bash
# 1. 构建前端（首次）
cd src/web
npm install --registry=https://registry.npmmirror.com
npm run build

# 2. 启动服务（项目根目录，系统 python3 即可，无需 .venv）
cd ../..
python3 src/serve.py --port 8420
# 打开 http://127.0.0.1:8420/
```

前端未构建时服务仍可启动，仅 API 可用（`/api/runs` 等）。
开发前端可用 `cd src/web && npm run dev`（已配置 /api 代理到 8420）。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s src/tests -v
```

## 关键设计

- **页面形态**：浅色商务风（与 demo.html 统一设计令牌，顶部 ☾/☀ 可切换暗色并记忆）；
  主视图为泳道式「轮次与交接」图（角色列 × 时间流）——**事件生成节点，只有事件中明确的
  交接关系才画箭头，相邻排列不构成交接证据**（核心逻辑抽为 `board/graph.ts` 纯函数，vitest 覆盖）。
- **智能体定义来源**：优先读 `.opencode/agent/*.md`（opencode 格式，`permission` 为嵌套映射）；
  该目录**存在但为空时不回退**；目录不存在才回退 `.claude/agents/*.md`（Claude 格式 name/skills/tools/color）。
  单文件读取/解析失败记入 `load_error`，不伪装成功、不静默改用另一格式。
  `definition_found=false` 表示"该角色是流程固定角色，未找到定义文件"——页面上有该智能体
  不代表定义已加载，前端以「无定义 / 加载失败」标记明示。权限区展示**配置原值**，
  非运行时最终授权。
- **写入边界**：除了人工副本提交，服务仅提供读取；artifact/log 端点走白名单 + realpath 防逃逸；大 JSON 截断、大日志只读尾部。
- **状态推导（共享规则集 v2）**：横条状态与交接回放共用同一份规则
  （`workbench/adapters/progress_rules.py`），状态条目带轮次、逐角色输出，前端不再另写判据。
  证据由 `evidence.build_evidence` 统一装配（run_state + 各轮摘要 + inputs 摘要），
  判据模块只消费不读文件。核心原则：**"待确认"≠跳过≠完成**——
  - 生成：`generation_status.json` 为权威状态（failed/complete/in_progress 三态；
    in_progress 阻止旧摘要判完成但不屏蔽同次失败证据；进程存活不推翻完成/失败）；
  - 修复：只有"结果通过 + 复检/修复证据"才算通过，检查未通过不能断言修复者已运行；
  - 源码分析分 extract 域（初始分析：source_raw + inputs 判读文档）与 diagnose 域（source_evidence）；
  - 补充约束：智能体只负责产出补丁（含合法空补丁），合并归主协调器，应用情况单独展示；
  - 提示词优化按触发条件判定，提案与裁决互不推翻；
  - 历史轮次不被任务终局状态覆盖；无记录不解释成"未参与"。
- **已知数据边界**（适配层显式处理）：
  - quality_gate.json 的 checks 元素键逐轮漂移（result/passed/status × evidence/detail），不认识的键归 unknown、绝不默认通过；
  - regression_check 的 `ok:false` 可能是求值器不支持表达式（非真实回归），按 `kind` 区分；
  - execution_result `status="success"` ≠ 用例全通过，以派生 `verdict` 为准；
  - history 无 iteration/ended_at，轮次归属为推导；
  - 终态后可授权续跑、STOPPED_BY_USER 后可恢复（history 分 normal/restart/continuation 段）；
  - inputs/ 文件可能被后续诊断追加修改，历史归属不明的证据标注"待确认"。

## 读取 API 一览

| 端点 | 说明 |
|---|---|
| `/api/health` | 存活探测 |
| `/api/runs` | run 列表摘要 |
| `/api/runs/{id}` | RunView：run_state + history 分段 + 逐轮摘要 + 12 agent 状态 |
| `/api/runs/{id}/replay` | 迭代回放事件流（谁 pass → 交给谁 → 谁打回） |
| `/api/runs/{id}/iterations/{n}` | 单轮产物摘要 |
| `/api/runs/{id}/iterations/{n}/cases?offset&limit&result=failed` | 用例记录分页 |
| `/api/runs/{id}/iterations/{n}/logs/tail?name=&bytes=` | 大日志尾部（白名单） |
| `/api/runs/{id}/artifact?path=` | 原始 JSON 产物（白名单 + 截断 + sha256） |
| `/api/agents` | 12 agent 定义（opencode 优先 / Claude 兜底 + definition_found/load_error/permission） |
| `/api/assets` | 资产目录：手写/知识技能、agent 能力、知识库 manifest、扩展与用户技能组（资产页消费） |

## 约束审核与覆盖率

- 工作台顶部及节点详情提供入口；约束页面地址为 `/constraints?run=<任务目录名>&iter=iter_002`。
- 支持产品分组、输入输出参数、原文行定位、相邻轮次参数间约束差异、修改/新增/删除参数间约束。输入输出参数卡保持只读。
- `/coverage` 覆盖率报告按任务内嵌存放：`runs/<run-id>/ops_cov_report/<报告目录>/`（含 `*_coverage.json` 与可选 `analysis.md`）。
  带 `?run=<任务目录名>` 进入时只列该任务内嵌报告，**按路径关联、不做名称匹配**；无任务上下文时浏览项目根 `ops_cov_report/`（兼容旧数据）。
  保留文件/函数粒度、未覆盖函数原因及已覆盖函数的行详情。报告不自动绑定任务轮次，函数/行/分支指标分别展示。
- 数据接口位于 `/api/review/`，兼容旧页面的原始响应结构。旧 `static/` 文件不变；迁入页面通过本地前端构建加载依赖，无外部 CDN。
- 唯一写入接口：`POST /api/review/runs/<id>/iter_<n>/constraints_update`。必须带同源页面读取的 `X-Review-Token`，以及原约束和现有副本的内容哈希。版本变化返回 409，避免覆盖其他编辑。请求体上限 8 MB。
- 保存时进行结构校验、唯一编号检查和原子替换。**语义校验仍由业务侧负责。保存成功不代表已接入、已创建新轮次或已执行。**
- 原任务若有正在等待的文件监听器，提交副本可能被现有业务流程接入；本服务本身不调用生成器或执行器。
- 本地使用建议绑定回环地址；不要把无登录控制的审核服务作为共享服务暴露。

测试：`PYTHONPATH=src <虚拟环境解释器> -m unittest discover -s src/tests -v`。浏览器写入验证必须使用运行目录的临时副本。
