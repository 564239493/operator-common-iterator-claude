# MCP + SKILL + SUBAGENT 改造计划（最终版）

## Context

当前项目是 CANN 算子迭代测试的 Claude Code 原生编排器。所有确定性业务逻辑通过 `scripts/*.py` CLI 脚本实现，由 Skills 通过 Bash 命令调用。目标：改为 MCP + SKILL + SUBAGENT 架构，打包成 wheel，支持任意位置安装，业务流程不变。

## 关键决策（已确认）

- **包结构**：opci 子包（agent → opci.agent, executer → opci.executer, 新增 opci.mcp）
- **工具粒度**：细粒度（约 22 个 MCP 工具）
- **scripts/ 处理**：删除（所有功能只通过 MCP 工具）
- **setup 策略**：复制到用户目录（.claude/、prompts/、docs/、knowledge/ 全部复制）

---

## 1. 包结构

```
项目根目录/
  pyproject.toml
  uv.lock
  .gitignore
  README.md
  CLAUDE.md                       ← 项目指令（本地开发用）
  .claude/                        ← 本地开发用（git 管理，不打包进 wheel）
  
  opci/                           ← 唯一源码目录（打包进 wheel）
    __init__.py
    config.py                     ← 路径解析（PROJECT_ROOT / PACKAGE_ROOT / resolve_input_path）
    server.py                     ← fastmcp MCP Server 入口
    cli.py                        ← CLI：setup / mcp-server
    
    agent/                        ← 原 agent/ 的所有内容（import 改为 opci.agent.*）
      __init__.py
      generators/
        facade.py
        operator_handle_main.py
        common_model_definition.py
        data_definition/
          constants.py            ← 修复 cwd-relative 路径
          param_models_def.py
          common_models.py
        operator_param_models/
        operator_param_combine/
        param_constraint_solve/
        coverage_statistics/
        common_utils/
          data_handle_utils.py
          logger_util.py          ← 修复 cwd-relative 日志路径
          common_dispatcher.py
        atk_common_utils/
        configs/
          shape_definitions.json
          global_role_definitions.json
    
    executer/                     ← 原 executer/ 的所有内容（import 改为 opci.executer.*）
      __init__.py
      runner.py                   ← project_root 参数由 MCP 工具传入
      ssh.py
      report_parser.py
      models.py
      resources/
        generator.py
        aclnn_api_template.py.j2
        aclnnCalculateMatmulWeightSize.py.tpl
        aclnnCalculateMatmulWeightSizeV2.py.tpl
        aclnn_extracted.txt
    
    mcp/                          ← 新增：MCP Server 工具层
      __init__.py
      _shared.py                  ← 跨工具共享逻辑
      tools/
        __init__.py
        run_management.py         ← init_run, find_latest_prompt, validate_server_config, update_run_state
        batch_management.py       ← init_batch, batch_claim, batch_attach_run, batch_complete, batch_show
        constraints.py            ← normalize_constraints, validate_constraints
        cases.py                  ← generate_cases, validate_cases
        execution.py              ← execute_generate, execute_real, execute_mock, validate_execution, validate_executor
        analysis.py               ← validate_analysis
        registry.py               ← show_workforce
    
    resources/                    ← 所有捆绑数据（直接在包内，无 force-include）
      agents/                     ← 6 个修改后的 agent .md
      skills/                     ← 10 个修改后的 skill SKILL.md
      hooks/                      ← 2 个 hook .py
      settings_template.json      ← 含 MCP server 注册的 settings 模板
      knowledge/                  ← 知识库（直接放在包内）
        common/broadcast.md, type_promotion.md
        operator_patterns/ffn_v3.md
      docs/                       ← 6 个设计文档（直接放在包内）
      prompts/                    ← v1~v3 完整版（直接放在包内）
      operator_docs/              ← 示例算子文档（直接放在包内）
      servers.example.json
```

**关键变化**：
- prompts/、knowledge/、docs/、operator_docs/ 全部移入 opci/resources/ 内部
- 不再保留项目根下的这些目录副本
- 不再需要 force-include（所有资源在包内）
- hatchling `packages = ["opci"]` 自动包含 opci/ 下所有子目录
- 非Python文件(.json/.md/.j2/.tpl/.txt)通过 force-include 配置确保打包

---

## 2. MCP 工具定义（22 个）

### 设计原则：Agent 通过 MCP 巃具访问资源

所有文件读写操作通过 MCP 巃具完成，Agent 不直接用 Read/Write 巃具操作项目目录内的资源文件。
Agent 可以用 Read 巃具读取 run 目录内的产物（constraints.json、cases.json 等），因为这些是 Agent 自己产出的中间文件。

### 2.1 run_management（6）

| 工具 | 参数 | 逻辑 |
|---|---|---|
| `init_run` | doc, prompt?, max_iterations=5, case_count=10, mode="real", server_config="servers.json" | 创建 run + state + inputs 快照（含 prompt 复制） |
| `update_run_state` | run_dir, state, iteration? | 更新 run_state.json |
| `find_latest_operator_prompt` | directory? | 找最高版本 prompt，返回路径 |
| `validate_server_config` | path | 校验 servers.json |
| `read_operator_prompt` | run_dir | 读取 run/inputs/ 下的当前 prompt 全文内容 |
| `write_operator_prompt` | run_dir, iter_dir, content, version | 写入优化后的 prompt（写到 iter/ 快照 + 项目 prompts/ 目录） |

### 2.2 batch_management（5）

| 工具 | 参数 | 逻辑 |
|---|---|---|
| `init_batch` | directory, glob="*.md", recursive=false, prompt?, max_iterations=5, case_count=10, mode="real", server_config="servers.json", continue_on_error=true | 扫描 + 创建批次 |
| `batch_claim` | batch_dir | 认领下一个算子 |
| `batch_attach_run` | batch_dir, run_dir | 关联 run |
| `batch_complete` | batch_dir, terminal_state?, message="" | 完成算子 |
| `batch_show` | batch_dir | 展示批次状态 |

### 2.3 constraints（2）

| 工具 | 参数 | 逻辑 |
|---|---|---|
| `normalize_constraints` | path | 原地规范化 |
| `validate_constraints` | path | 校验 |

### 2.4 cases（2）

| 工具 | 参数 | 逻辑 |
|---|---|---|
| `generate_cases` | constraints, output, count=10, seed=42, jsonl_save_path?, iter_dir? | 生成用例 |
| `validate_cases` | path | 校验 |

### 2.5 execution（5）

| 工具 | 参数 | 逻辑 |
|---|---|---|
| `execute_cases_generate` | cases, output, doc, operator, server_config, run_id, env_init?, artifact_dir? | 生成 executor |
| `execute_cases_real` | cases, output, doc, operator, server_config, run_id, platform?, env_init?, artifact_dir? | 真实执行 |
| `execute_cases_mock` | cases, output, fail_every=3 | mock 执行 |
| `validate_execution` | path | 校验 execution_result |
| `validate_executor` | path | 校验 cases_executor.py |

### 2.6 analysis（1）

| 工具 | 参数 | 逻辑 |
|---|---|---|
| `validate_analysis` | path | 校验 analysis |

### 2.7 registry（1）

| 工具 | 参数 | 逻辑 |
|---|---|---|
| `show_workforce` | 无 | 返回 skills/agents/dispatch |

---

## 3. CLI 命令

### `opci setup`
复制 .claude/、prompts/、docs/、knowledge/、servers.example.json 到用户目录。生成 settings.json 含 MCP 注册。

### `opci mcp-server`
启动 fastmcp stdio MCP server。

---

## 4. 路径解析与资源归属（核心原则）

### 原则：MCP 依赖放 pip 安装目录，Agent 依赖放项目目录，无重叠

| 资源 | 归属 | 位置 | 说明 |
|---|---|---|---|
| prompts/（原始版本 v1~v3） | MCP 侧 | pip 安装目录 `opci/resources/prompts/` | MCP 工具 `find_latest_operator_prompt` 从包内找 |
| prompts/（优化版本 v4+） | MCP 侧 | 项目目录 `prompts/`（setup 创建空目录，optimizer 写入） | MCP `init_run` 从项目/prompts/ 或包内找最新版，复制到 run/inputs/ |
| prompt 快照 | MCP 侧管理 → Agent 通过 MCP 读取 | run/inputs/prompt_vN.md | Agent 通过 MCP 巃具 `read_operator_prompt` 读取，不直接 Read 文件 |
| agent/generators/configs/ | MCP 侧 | pip 安装目录 `opci/agent/generators/configs/` | TestCaseGenerator 内部 __file__ 定位 |
| executer/resources/ | MCP 侧 | pip 安装目录 `opci/executer/resources/` | runner.py 内部 __file__ 定位 |
| .claude/（agents/skills/hooks） | Agent 侧 | 项目目录（setup 复制） | Claude Code 框架加载 |
| docs/ | Agent 侧 | 项目目录（setup 复制） | LLM Agent 读取 |
| knowledge/ | Agent 侧 | 项目目录（setup 复制） | LLM Agent 读取 |
| servers.json | 项目数据 | 项目目录（用户填写） | MCP 工具校验 + SSH |
| runs/ | 项目数据 | 项目目录 | MCP 工具创建 + Agent 读取 |
| operator_docs/ | 项目数据 | 项目目录（用户提供） | MCP 工具复制快照 → Agent 读快照 |

### 路径解析实现

```python
# opci/config.py
import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent  # pip 安装位置

def get_project_root() -> Path:
    """项目目录 = CLAUDE_PROJECT_DIR 或 cwd"""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env).resolve() if env else Path.cwd().resolve()

def resolve_input_path(value: str | Path) -> Path:
    """用户输入路径 → 绝对路径（非绝对前加 PROJECT_ROOT）"""
    root = get_project_root()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()

def prompt_directory() -> Path:
    """prompt 查找：项目目录优先 → 包内兜底"""
    local = get_project_root() / "prompts"
    if local.is_dir() and any(local.glob("operator_constraints_extract_v*.md")):
        return local  # 项目有优化版本时优先
    bundled = PACKAGE_ROOT / "resources" / "prompts"
    if bundled.is_dir():
        return bundled
    return local  # 兜底（可能不存在）

def find_latest_operator_prompt(directory: Path | None = None) -> Path | None:
    """在指定或默认 prompt 目录找最高版本"""
    prompt_dir = directory or prompt_directory()
    # ... 同原 runtime_config.py 逻辑
```

---

## 5. pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "opci"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "fastmcp>=0.7",
    "pydantic>=2.0",
    "numpy>=1.26",
    "pyyaml>=6.0",
    "packaging>=23.0",
    "typing_extensions>=4.0",
    "z3-solver>=4.12",
    "asyncssh>=2.14",
    "openpyxl>=3.1",
    "jinja2>=3.1",
]

[project.optional-dependencies]
torch = ["torch>=2.0"]
dev = ["pytest>=7.0"]

[project.scripts]
opci = "opci.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["opci"]

# 确保 hatchling 打包非 Python 文件
[tool.hatch.build.targets.wheel.force-include]
"opci/resources" = "opci/resources"    # Agent Pack + 捆绑数据
"opci/agent/generators/configs" = "opci/agent/generators/configs"  # shape_definitions.json 等
"opci/executer/resources" = "opci/executer/resources"  # generator.py + 模板
```

---

## 6. Import 改造（关键工作量）

### 需要修改的 import 语句

所有 `from agent.xxx import` → `from opci.agent.xxx import`
所有 `from executer.xxx import` → `from opci.executer.xxx import`

**受影响的文件**（约 50+ 处）：

**agent/ 内部相互引用**（约 30 处）：
- agent/__init__.py
- agent/generators/facade.py
- agent/generators/operator_handle_main.py
- agent/generators/common_model_definition.py
- agent/generators/data_definition/*.py
- agent/generators/operator_param_models/*.py
- agent/generators/operator_param_combine/*.py
- agent/generators/param_constraint_solve/*.py
- agent/generators/coverage_statistics/*.py
- agent/generators/common_utils/*.py
- agent/generators/atk_common_utils/*.py

**executer/ 内部引用**（约 5 处）：
- executer/__init__.py
- executer/runner.py
- executer/ssh.py

**新增 MCP 工具层**（约 10 处）：
- opci/mcp/tools/*.py — 都引用 opci.agent.* 和 opci.executer.*

---

## 7. Skill 修改

所有 SKILL.md 中 `python scripts/xxx.py` → MCP 工具调用说明。

### 具体映射

| Skill | Bash 命令 | → MCP 工具 |
|---|---|---|
| iterate-operator | `python scripts/init_run.py` | `mcp__opci__init_run` |
| | `python scripts/batch_state.py ... attach-run` | `mcp__opci__batch_attach_run` |
| | `python scripts/batch_state.py ... complete` | `mcp__opci__batch_complete` |
| iterate-directory | `python scripts/init_batch.py` | `mcp__opci__init_batch` |
| | `python scripts/batch_state.py claim/show/complete` | `mcp__opci__batch_claim/show/complete` |
| extract-constraints | `python scripts/normalize_constraints.py` | `mcp__opci__normalize_constraints` |
| | `python scripts/validate_artifacts.py constraints` | `mcp__opci__validate_constraints` |
| generate-cases | `python scripts/generate_cases.py` | `mcp__opci__generate_cases` |
| | `python scripts/validate_artifacts.py cases` | `mcp__opci__validate_cases` |
| execute-cases | `python scripts/execute_cases.py --generate` | `mcp__opci__execute_cases_generate` |
| | `python scripts/execute_cases.py --mode real` | `mcp__opci__execute_cases_real` |
| | `python scripts/execute_cases.py --mode mock` | `mcp__opci__execute_cases_mock` |
| | `python scripts/validate_artifacts.py execution` | `mcp__opci__validate_execution` |
| | `python scripts/validate_artifacts.py executor` | `mcp__opci__validate_executor` |
| validate-run | `python scripts/validate_artifacts.py ...` | 对应 validate_* 工具 |
| show-workforce | `python scripts/show_registry.py` | `mcp__opci__show_workforce` |

**不修改的 Skills**（纯 LLM 推理）：atc-cpu-golden-derivation, diagnose-failure, optimize-prompt

---

## 8. Agent 修改

6 个 Agent .md：
- 去掉 Bash 引用，改为 MCP 巃具
- 纯 LLM Agent（failure-analyst、prompt-optimizer）去掉 Bash tools 权限
- case-executor 保留 Bash 用于 grep/ast.parse 自检命令

---

## 9. settings.json

新增 MCP server 注册：
```json
{
  "mcpServers": {
    "opci": {
      "command": "opci",
      "args": ["mcp-server"]
    }
  }
}
```
permissions allow 中添加 MCP 巃具权限。

---

## 10. 路径修复

- agent/generators/data_definition/constants.py：CASE_RESULT_SAVE_PATH 等 cwd-relative 路径 → 改为参数传入或 PACKAGE_ROOT 内
- agent/generators/common_utils/logger_util.py：log_dir="./logs" → 改为参数传入
- executer/runner.py：project_root 参数由 MCP 工具正确传入
- 所有 MCP 工具通过 PROJECT_ROOT 变量解析用户路径

---

## 11. 实施步骤

### Phase 1: 包骨架 + 迁移
1. 创建 opci/ 目录结构（__init__.py, config.py 等）
2. 创建 pyproject.toml
3. 初始化 uv 项目
4. 移动 agent/ → opci/agent/（原目录删除）
5. 移动 executer/ → opci/executer/（原目录删除）
6. 移动 prompts/ → opci/resources/prompts/（原目录删除）
7. 移动 knowledge/ → opci/resources/knowledge/（原目录删除）
8. 移动 docs/ → opci/resources/docs/（原目录删除）
9. 移动 operator_docs/ → opci/resources/operator_docs/（原目录删除）
10. 修改所有 import 为 opci.agent.* / opci.executer.*
11. 实现 config.py（路径解析）
12. 验证 uv pip install -e . 成功

### Phase 2: MCP 工具实现
8. 实现 _shared.py
9. 实现每个 tools/ 模块（22 个工具）
10. 实现 server.py
11. 实现 cli.py mcp-server 命令
12. 测试 MCP server

### Phase 3: CLI setup
13. 实现 cli.py setup 命令
14. 准备 resources/ 模板文件
15. 测试 setup 命令

### Phase 4: Skill/Agent 更新
16. 修改 10 个 SKILL.md（Bash → MCP）
17. 修改 6 个 agent .md
18. 生成 settings_template.json
19. 更新 CLAUDE.md

### Phase 5: 清理
20. 删除 scripts/ 目录
21. 删除旧的 __pycache__
22. 删除 requirements.txt
23. 更新 .gitignore

### Phase 6: 测试验证
24. uv run opci setup
25. uv run opci mcp-server
26. 完整 /iterate-operator 流程
27. pip install . 构建 wheel
28. 全新环境安装 + 运行

---

## 12. 验证策略

- 单元：每个 MCP 巃具独立调用，确认返回值与脚本输出一致
- 集成：完整 /iterate-operator 流程
- 打包：wheel 包含所有资源
- 安装：全新环境 setup + MCP 巃具可用
- 路径：任意安装位置 prompts/、configs/、resources/ 可找到

---

## 13. 回退策略

- Git tag 标记每个 Phase 边界
- Phase 1-2 可回退（scripts 还存在时）
- Phase 5 删除 scripts/ 后可通过 git revert 回退

## 14. 关键风险

| 风险 | 缓解 |
|---|---|
| import 改造遗漏 | grep 全量扫描 from agent / from executer |
| hatchling 不打包 .json/.j2/.tpl/.txt | force-include 双保险 |
| fastmcp Windows stdio | 官方支持，实测 |
| agent/executer cwd-relative 路径 | MCP 层传入绝对路径 |
| torch 依赖大 | 可选依赖 |
