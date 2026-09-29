"""GET /api/runs：扫描 runs/ 一级子目录 → RunSummary[]。"""
from .. import config, jsonutil, paths


def _operator_name(run_id, run_state):
    doc = run_state.get("operator_doc") or run_state.get("operator_doc_source") or ""
    if doc:
        base = doc.replace("\\", "/").rsplit("/", 1)[-1]
        if base.lower().endswith(".md"):
            return base[:-3]
        return base
    # 命名模式 <算子名>-<yyyymmdd>-... 兜底
    parts = run_id.rsplit("-", 3)
    return parts[0] if len(parts) == 4 else run_id


def summarize_run(run_root):
    # type: (...) -> dict
    run_id = run_root.name
    state_file = run_root / "run_state.json"
    iters = paths.iter_dirs(run_root)
    if not state_file.exists():
        return {"run_id": run_id, "parse_error": "缺少 run_state.json", "iteration_dirs": len(iters)}
    raw = jsonutil.read_json(state_file)
    if jsonutil.is_error(raw):
        return {"run_id": run_id, "parse_error": raw["_error"], "iteration_dirs": len(iters)}

    history = raw.get("history") or []
    last_event_at = None
    for event in history:
        if isinstance(event, dict) and event.get("at"):
            last_event_at = event["at"]
    state = raw.get("state")
    scene = raw.get("scene") or {}
    return {
        "run_id": run_id,
        "operator": _operator_name(run_id, raw),
        "state": state,
        "is_terminal": state in config.TERMINAL_STATES,
        "current_iteration": raw.get("current_iteration"),
        "max_iterations": raw.get("max_iterations"),
        "test_framework": raw.get("test_framework"),
        "mode": raw.get("mode"),
        "scene_enabled": bool(scene.get("enabled")),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        # updated_at 可能早于最后事件，取两者最大
        "last_activity": jsonutil.max_iso(raw.get("updated_at"), last_event_at),
        "history_events": len(history),
        "iteration_dirs": len(iters),
    }


def list_runs(root):
    # type: (...) -> list
    base = paths.runs_dir(root)
    runs = []
    if base.is_dir():
        for child in base.iterdir():
            if child.is_dir() and child.name != "batches" and not child.name.startswith("."):
                runs.append(summarize_run(child))
    runs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return runs
