"""12 个 agent 的流程角色定义与状态推导入口。

实际判据统一在 progress_rules.py（规则集 v2）——状态推导与交接回放共用
同一份规则，本模块只保留流程顺序（领域知识）并给出 RunView.agents 视图。
所有结论带 basis 与 inferred=True；证据不足一律 unconfirmed（待确认）。
"""
from .. import config
from . import progress_rules

# 工程师带固定顺序（与 scripts/show_registry.py 的 Dispatch 拓扑对齐，属领域知识）
AGENTS_FLOW = [
    {"name": "scene-scanner", "role": "场景扫描", "optional": True, "stage": "SCENE_SCAN"},
    {"name": "constraint-extractor", "role": "约束提取", "optional": False, "stage": "EXTRACT"},
    {"name": "source-analyst", "role": "源码分析", "optional": True, "stage": "SOURCE_ANALYSIS"},
    {"name": "constraint-supplementer", "role": "约束补充", "optional": True, "stage": "SUPPLEMENT"},
    {"name": "constraint-checker", "role": "约束检查", "optional": False, "stage": "CHECK"},
    {"name": "constraint-repairer", "role": "约束修复", "optional": True, "stage": "REPAIR"},
    {"name": "case-generator", "role": "用例生成", "optional": False, "stage": "GENERATE"},
    {"name": "case-executor", "role": "用例执行", "optional": False, "stage": "EXECUTE"},
    {"name": "quality-reviewer", "role": "质量门禁", "optional": False, "stage": "GATE"},
    {"name": "failure-analyst", "role": "失败诊断", "optional": False, "stage": "DIAGNOSE"},
    {"name": "constraint-updater", "role": "约束更新", "optional": True, "stage": "UPDATE_CONSTRAINTS"},
    {"name": "prompt-optimizer", "role": "提示词优化", "optional": True, "stage": "OPTIMIZE"},
]


def infer_agents(run_state, iter_views, inputs_files):
    # type: (dict, list, dict) -> list
    """RunView.agents：当前（或最新）轮的带轮次状态 + run 级角色，按 AGENTS_FLOW 顺序。

    与回放共用 progress_rules.per_round_states 的同一结论。
    """
    if not iter_views:
        return []
    states = progress_rules.per_round_states(run_state, iter_views, inputs_files)
    current = (run_state or {}).get("current_iteration")
    latest_n = iter_views[-1]["n"]
    pick = states.get(current) or states.get(latest_n) or {}
    out = []
    for spec in AGENTS_FLOW:
        entry = dict(pick.get(spec["name"]) or {
            "status": "pending", "basis": "尚无该角色证据",
            "inferred": True, "iteration": None,
        })
        entry["name"] = spec["name"]
        entry.update({
            "role": spec["role"],
            "optional": spec["optional"],
            "stage": spec["stage"],
            "ruleset": config.INFERENCE_RULESET_VERSION,
        })
        out.append(entry)
    return out
