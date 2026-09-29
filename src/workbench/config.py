"""常量配置。"""

DEFAULT_PORT = 8420

# 读 JSON 的硬上限（防止异常大文件撑爆内存；样例最大 execution_result.json 约 2.65MB）
MAX_JSON_READ_BYTES = 64 * 1024 * 1024
# artifact 端点超过该大小时截断返回
ARTIFACT_TRUNCATE_BYTES = 2 * 1024 * 1024
# 日志 tail 默认值与上限
LOG_TAIL_DEFAULT_BYTES = 16 * 1024
LOG_TAIL_MAX_BYTES = 256 * 1024
# 用例分页
RECORDS_PAGE_DEFAULT = 50
RECORDS_PAGE_MAX = 200

# 状态推导规则集版本（随规则调整递增，便于 UI 标注）
INFERENCE_RULESET_VERSION = "v2"

# run_state.state 的终态集合（用于轮询降频与 history 分段）。
# 与生产者 scripts/flow_control.py 的 6 态对齐：MIXED_FAILURE_REVIEW /
# NEEDS_HUMAN_EVIDENCE 是「等待用户拿主意」的挂起态（flow_control 留有后继边），
# 不算终态——等待期间页面不得降频或显示「任务已结束」。
TERMINAL_STATES = frozenset({
    "SUCCESS",
    "BLOCKED",
    "MAX_ITERATIONS",
    "STOP_GENERATOR_BUG",
    "STOP_EXECUTOR_BUG",
    "STOPPED_BY_USER",
})

# 「等待用户决定」的挂起态：非终态（用户答复后流程继续），UI 用等待样式提示
WAITING_STATES = frozenset({
    "MIXED_FAILURE_REVIEW",
    "NEEDS_HUMAN_EVIDENCE",
})

# artifact 端点允许查看的 JSON 文件名白名单（按 basename）
ARTIFACT_BASENAMES = frozenset({
    "run_state.json",
    "constraints.json",
    "constraint_check.json",
    "quality_gate.json",
    "analysis.json",
    "execution_result.json",
    "cases.json",
    "cases_expanded.json",
    "generation_summary.json",
    "generation_progress.json",
    "generate_result.json",
    "regression_check.json",
    "constraint_update.json",
    "constraints_diff.json",
    "extraction_provenance.json",
    "relation_examples.json",
    "scene_scan.json",
    "selection.json",
    "scene_conflicts.json",
    "prompt_assembly.json",
    "prompt_preanalysis.json",
    "prompt_update_decisions.json",
    "source_evidence.json",
    "source_raw.json",
    "conflict_candidates.json",
    "manifest.json",
    "generation_status.json",
    "constraints_patch.json",
    "prompt_update_proposal.json",
    # TTK 框架特有产物
    "ttk_conversion_audit.json",
    "golden_manifest.json",
    "ttk_plugin.py",
    "ttk_golden_fia.py",
    # inputs 层用户裁决文件（与 prompt_update_decisions 同类）
    "conflict_resolution.json",
    "supplement_constraints.md",
    "scene_directive.md",
})

# 额外允许的 basename 前缀（如 cases_Atlas A2 训练系列产品_....json）
ARTIFACT_BASENAME_PREFIXES = ("cases_",)

# logs/tail 端点允许的日志文件名白名单（按 basename）
LOG_BASENAMES = frozenset({
    "generation_console.log",
    "execution.log",
    "atk.log",
    "error_summary.log",
    "archive_stderr.log",
})
