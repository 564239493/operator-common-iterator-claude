"""GET /api/runs/{run_id}：RunView（run_state + history 分段 + 逐轮摘要 + 带轮次 agent 状态）。"""
from .. import config, jsonutil, paths
from . import evidence as evidence_adapter
from . import progress_rules
from . import stage_inference


def build_segments(history):
    # type: (list) -> list
    """把 history 事件切成段：

    - restart：STOPPED_BY_USER（如 SESSION_RESTART）之后的恢复段；
    - continuation：终态（如 MAX_ITERATIONS）之后再出现非终态事件的授权续跑段；
    - normal：常规段。

    restart 段内同 state 重入不算打回。
    """
    segments = []
    current = None
    pending_kind = "normal"
    prev_state = None

    def _open(kind, at):
        return {"kind": kind, "from_at": at, "to_at": at, "events": []}

    for event in history:
        if not isinstance(event, dict):
            continue
        state = event.get("state")
        at = event.get("at")
        if state == "STOPPED_BY_USER" and prev_state != "STOPPED_BY_USER":
            # 当前段在用户停止处收束，本事件记入当前段，下一段标记 restart
            if current is None:
                current = _open("normal", at)
            current["events"].append(event)
            current["to_at"] = at
            segments.append(current)
            current = None
            pending_kind = "restart"
            prev_state = state
            continue
        if current is None:
            current = _open(pending_kind, at)
            pending_kind = "normal"
        elif (prev_state in config.TERMINAL_STATES
              and state not in config.TERMINAL_STATES):
            # 终态后授权续跑
            segments.append(current)
            current = _open("continuation", at)
        current["events"].append(event)
        current["to_at"] = at
        prev_state = state
    if current:
        segments.append(current)
    return segments


def build_run_view(root, run_id):
    # type: (...) -> dict
    ev = evidence_adapter.build_evidence(root, run_id)
    raw = ev["run_state"]
    iter_views = ev["iterations"]
    inputs_files = ev["inputs_files"]
    run_root = ev["run_root"]

    history = raw.get("history") or []
    segments = build_segments(history)

    agents = stage_inference.infer_agents(raw, iter_views, inputs_files)
    round_states = progress_rules.per_round_states(raw, iter_views, inputs_files)

    last_event_at = None
    for event in history:
        if isinstance(event, dict) and event.get("at"):
            last_event_at = event["at"]

    scene = raw.get("scene") or {}
    return {
        "run_id": run_id,
        "run_root_rel": paths.rel_to(run_root, root),
        "state": raw.get("state"),
        "is_terminal": raw.get("state") in config.TERMINAL_STATES,
        "current_iteration": raw.get("current_iteration"),
        "max_iterations": raw.get("max_iterations"),
        "test_framework": raw.get("test_framework"),
        "mode": raw.get("mode"),
        "operator_family": raw.get("operator_family"),
        "operator_doc": jsonutil.remap_paths(raw.get("operator_doc"), root),
        "case_count": raw.get("case_count"),
        "scene": {
            "enabled": bool(scene.get("enabled")),
            "scope": scene.get("scope"),
            "device_types": scene.get("device_types"),
            "selection": scene.get("selection"),
            "known_conflicts": scene.get("known_conflicts"),
        },
        "current_prompt": jsonutil.remap_paths(raw.get("current_prompt"), root),
        "current_prompt_modules": raw.get("current_prompt_modules") or [],
        "constraint_check_config": raw.get("constraint_check"),
        "human_checkpoint_round": raw.get("human_checkpoint_round"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "last_activity": jsonutil.max_iso(raw.get("updated_at"), last_event_at),
        "history": history,
        "segments": segments,
        "iterations": iter_views,
        "inputs_files": inputs_files,
        "round_states": round_states,
        "agents": agents,
    }
