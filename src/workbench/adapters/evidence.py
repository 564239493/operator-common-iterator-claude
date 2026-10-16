"""统一证据装配：run_state + 各轮摘要 + inputs 层摘要。

判据模块（progress_rules）只消费本模块装配好的数据，不自行读文件。
inputs 与 iter 两层产物统一使用 missing / ok / broken 三态；
各文件按自身契约判断有效性——合法空列表与允许为空的文档不属于 broken；
"解析成功 ≠ 结构有效"，空对象、类型错误归入 broken。
"""
import json
from pathlib import Path

from .. import jsonutil, paths
from . import iterations as iter_adapter


def _json_entry(path, contract):
    # type: (Path, str) -> dict
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"status": "broken", "error": "stat 失败: %s" % exc, "size": 0}
    if not path.exists():
        return {"status": "missing", "size": 0}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return {"status": "broken", "error": "读取/解析失败: %s" % exc, "size": size}
    entry = {"status": "ok", "size": size}
    if contract == "scene_scan":
        # 契约：dict 且 has_scenarios 为 bool、device_types 为列表（空列表合法）
        if not isinstance(raw, dict) or not isinstance(raw.get("has_scenarios"), bool) \
                or not isinstance(raw.get("device_types"), list):
            return {"status": "broken", "error": "scene_scan 缺少 has_scenarios/device_types 或类型不符",
                    "size": size}
        entry["has_scenarios"] = raw["has_scenarios"]
        entry["device_types"] = raw["device_types"]
    elif contract == "decisions":
        # 契约：dict 且 decisions 为列表（空列表合法，如实计 0 条）
        decisions = raw.get("decisions") if isinstance(raw, dict) else raw
        if not isinstance(decisions, list):
            return {"status": "broken", "error": "decisions 必须是数组", "size": size}
        entry["count"] = len(decisions)
    elif contract == "json":
        if not isinstance(raw, (dict, list)):
            return {"status": "broken", "error": "应为 JSON 对象或数组", "size": size}
    return entry


def _text_entry(path):
    # type: (Path) -> dict
    """文本类输入：允许为空（空文档不属于 broken）。"""
    try:
        data = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"status": "broken", "error": "读取失败: %s" % exc, "size": 0}
    return {"status": "ok", "size": len(data.encode("utf-8"))}


def summarize_inputs(run_root):
    # type: (Path) -> dict
    """inputs/ 层摘要：{文件名: {status/...}}。"""
    inputs_dir = run_root / "inputs"
    result = {}
    if not inputs_dir.is_dir():
        return result
    json_files = {
        "scene_scan.json": "scene_scan",
        "selection.json": "json",
        "scene_conflicts.json": "json",
        "prompt_update_decisions.json": "decisions",
        "conflict_candidates.json": "json",
        # 与 prompt_update_decisions 同类的「用户裁决」文件，口径保持一致
        "conflict_resolution.json": "json",
    }
    text_files = (
        "supplementary-doc.md",
        "uncertain-doc.md",
        "conflict-doc.md",
        "supplement_constraints.md",
        "scene_directive.md",
    )
    for name, contract in json_files.items():
        path = inputs_dir / name
        if path.exists():
            result[name] = _json_entry(path, contract)
    for name in text_files:
        path = inputs_dir / name
        if path.exists():
            result[name] = _text_entry(path)
    return result


def build_evidence(root, run_id):
    # type: (...) -> dict
    """装配三层证据：run_state + 各轮摘要 + inputs 摘要。

    供 build_run_view / infer_agents / build_replay 统一消费；
    禁止把含 agents 的 RunView 回灌给状态推导。
    """
    run_root = paths.resolve_run(root, run_id)
    raw = jsonutil.read_json(run_root / "run_state.json")
    if jsonutil.is_error(raw):
        raise ValueError("run_state.json 解析失败：%s" % raw["_error"])
    iter_views = [
        iter_adapter.summarize_iteration(path, n)
        for n, path in paths.iter_dirs(run_root)
    ]
    return {
        "run_id": run_id,
        "run_root": run_root,
        "run_state": raw,
        "iterations": iter_views,
        "inputs_files": summarize_inputs(run_root),
    }
