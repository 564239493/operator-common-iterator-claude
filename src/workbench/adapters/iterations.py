"""iter_NNN 目录产物解析 → IterationView（摘要级，不含大文件全文）。"""
from pathlib import Path

from .. import jsonutil
from . import quality_gate as gate_adapter
from . import regression as regression_adapter

# 参与"产物存在性位图"的文件（状态推导的输入；均位于 iter_NNN/ 层）
TRACKED_FILES = (
    "constraints.json",
    "constraint_check.json",
    "cases.json",
    "cases_expanded.json",
    "cases_executor.py",
    "execution_result.json",
    "analysis.json",
    "quality_gate.json",
    "generation_summary.json",
    "generation_progress.json",
    "generation_status.json",
    "generate_result.json",
    "constraint_update.json",
    "regression_check.json",
    "extraction_provenance.json",
    "relation_examples.json",
    "constraints_diff.json",
    "constraints.json.pre_update",
    "constraints.json.pre_repair_local",
    # 源码分析两域 / 补充补丁 / 提示词优化提案
    "source_raw.json",
    "source_evidence.json",
    "constraints_patch.json",
    "prompt_update_proposal.json",
    # TTK 框架特有产物（ATK 轮次不出现，属正常缺失而非异常）
    "ttk_conversion_audit.json",
    "golden_manifest.json",
    "ttk_plugin.py",
    "ttk_golden_fia.py",
)

# generation_status.json 的合法 state 集合（scripts/generate_cases.py 契约）
GENERATION_STATES = frozenset({"in_progress", "complete", "failed"})


def _snippet(value, limit=300):
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def summarize_iteration(iter_dir, n):
    # type: (Path, int) -> dict
    """单轮摘要：产物存在性位图 + 各产物关键字段。"""
    exists = {}
    mtimes = {}
    for name in TRACKED_FILES:
        path = iter_dir / name
        exists[name] = path.exists()
        if exists[name]:
            mtimes[name] = jsonutil.mtime_iso(path)
    # 场景模板子目录形态：iter_N/<场景模板>/ 下也可能有产物；位图只看扁平层，
    # 模板子目录名单独列出供前端提示。
    scene_subdirs = sorted(
        child.name for child in iter_dir.iterdir()
        if child.is_dir() and child.name not in ("execution_logs", "jsonl_checkpoints")
    ) if iter_dir.is_dir() else []

    view = {
        "n": n,
        "dir": iter_dir.name,
        "exists": exists,
        "mtimes": mtimes,
        "scene_subdirs": scene_subdirs,
    }
    view["constraint_check"] = _summarize_constraint_check(iter_dir, exists)
    view["generation"] = _summarize_generation(iter_dir, exists)
    view["generation_status"] = _summarize_generation_status(iter_dir, exists)
    view["execution"] = _summarize_execution(iter_dir, exists)
    view["quality_gate"] = _summarize_gate(iter_dir, exists)
    view["regression"] = _summarize_regression(iter_dir, exists)
    view["analysis"] = _summarize_analysis(iter_dir, exists)
    view["constraint_update"] = _summarize_update(iter_dir, exists)
    view["source_raw"] = _summarize_dict_artifact(iter_dir, exists, "source_raw.json")
    view["source_evidence"] = _summarize_dict_artifact(iter_dir, exists, "source_evidence.json")
    view["constraints_patch"] = _summarize_patch(iter_dir, exists)
    view["prompt_update_proposal"] = _summarize_dict_artifact(iter_dir, exists, "prompt_update_proposal.json")
    view["constraints_origins"] = _summarize_constraints_origins(iter_dir, exists)
    view["cases_count"] = _cases_count(iter_dir, exists)
    return view


def _summarize_constraints_origins(iter_dir, exists):
    # type: (Path, dict) -> dict
    """constraints_in_parameters 中 origin=supplement/conflict_resolution 的条目数。

    仅作辅助信息展示——旧约束可能已带标记，不能证明本轮补丁已应用。
    """
    if not exists.get("constraints.json"):
        return {}
    raw = _read(iter_dir, "constraints.json")
    if jsonutil.is_error(raw) or not isinstance(raw, dict):
        return {}
    cnp = raw.get("constraints_in_parameters")
    groups = cnp.values() if isinstance(cnp, dict) else ([cnp] if isinstance(cnp, list) else [])
    counts = {}
    for group in groups:
        for item in group or []:
            origin = (item or {}).get("origin") if isinstance(item, dict) else None
            if isinstance(origin, str):
                counts[origin] = counts.get(origin, 0) + 1
    return counts


def _read(iter_dir, name):
    return jsonutil.read_json(iter_dir / name)


def _summarize_constraint_check(iter_dir, exists):
    if not exists.get("constraint_check.json"):
        return None
    raw = _read(iter_dir, "constraint_check.json")
    if jsonutil.is_error(raw):
        return {"_error": raw["_error"]}
    issues = raw.get("issues") or []
    counts = {"open": 0, "fixed": 0, "unfixed": 0, "other": 0}
    for issue in issues:
        status = str((issue or {}).get("status", "")).lower()
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1
    summary = raw.get("summary") or {}
    return {
        "status": raw.get("status"),
        "current_round": raw.get("current_round"),
        "max_rounds": raw.get("max_rounds"),
        "issues_total": summary.get("total", len(issues)),
        "issues_open": summary.get("open", counts["open"]),
        "issues_fixed": summary.get("fixed", counts["fixed"]),
        "issues_unfixed": summary.get("unfixed", counts["unfixed"]),
        "issues": [
            {
                "id": (i or {}).get("id"),
                "status": (i or {}).get("status"),
                "found_round": (i or {}).get("found_round"),
                "last_checked_round": (i or {}).get("last_checked_round"),
                "problem": _snippet((i or {}).get("problem"), 200),
                "suggestion": _snippet((i or {}).get("suggestion"), 200),
            }
            for i in issues[:20]
        ],
    }


def _summarize_generation(iter_dir, exists):
    out = {}
    if exists.get("generation_summary.json"):
        raw = _read(iter_dir, "generation_summary.json")
        if not jsonutil.is_error(raw):
            out["summary"] = {
                "total": raw.get("total"),
                "requested_per_platform": raw.get("requested_per_platform"),
                "platforms": raw.get("platforms"),
                "generator_version": raw.get("generator_version"),
            }
    if exists.get("generation_progress.json"):
        raw = _read(iter_dir, "generation_progress.json")
        if not jsonutil.is_error(raw):
            out["progress"] = {
                "state": raw.get("state"),
                "pid_alive": raw.get("pid_alive"),
                "elapsed_seconds": raw.get("elapsed_seconds"),
                "per_platform": raw.get("per_platform"),
                "total": raw.get("total"),
            }
    return out or None


def _summarize_execution(iter_dir, exists):
    if not exists.get("execution_result.json"):
        return None
    raw = _read(iter_dir, "execution_result.json")
    if jsonutil.is_error(raw):
        return {"_error": raw["_error"]}
    engine_error = raw.get("engine_error")
    passed = raw.get("passed", 0) or 0
    failed = raw.get("failed", 0) or 0
    total = raw.get("total", passed + failed) or 0
    # status="success" ≠ 用例全通过：派生 verdict，UI 以 verdict 为准
    if engine_error:
        verdict = "engine_error"
    elif failed > 0:
        verdict = "partial_pass"
    elif total > 0:
        verdict = "full_pass"
    else:
        verdict = "no_cases"
    plog = raw.get("plog") or {}
    return {
        "status_raw": raw.get("status"),
        "verdict": verdict,
        "mode": raw.get("mode"),
        "passed": passed,
        "failed": failed,
        "total": total,
        "duration": raw.get("duration"),
        "engine_error": _snippet(engine_error) if engine_error else None,
        "plog_error_count": plog.get("error_count"),
        "records_count": len(raw.get("records") or []),
        "exit_code": raw.get("exit_code"),
    }


def _summarize_gate(iter_dir, exists):
    if not exists.get("quality_gate.json"):
        return None
    raw = _read(iter_dir, "quality_gate.json")
    if jsonutil.is_error(raw):
        return {"_error": raw["_error"]}
    return gate_adapter.normalize_gate(raw)


def _summarize_regression(iter_dir, exists):
    if not exists.get("regression_check.json"):
        return None
    raw = _read(iter_dir, "regression_check.json")
    if jsonutil.is_error(raw):
        return {"_error": raw["_error"]}
    return regression_adapter.classify(raw)


def _summarize_analysis(iter_dir, exists):
    if not exists.get("analysis.json"):
        return None
    raw = _read(iter_dir, "analysis.json")
    if jsonutil.is_error(raw):
        return {"_error": raw["_error"]}
    clusters = raw.get("failure_clusters") or []
    findings = raw.get("constraint_findings") or []
    return {
        "root_cause": raw.get("root_cause"),
        "overall_action": raw.get("overall_action"),
        "root_cause_summary": raw.get("root_cause_summary"),
        "failure_cluster_count": len(clusters),
        "constraint_finding_count": len(findings),
        "clusters": [
            {
                "id": (c or {}).get("id"),
                "root_cause": (c or {}).get("root_cause"),
                "case_count": len((c or {}).get("case_ids") or []),
                "recommended_action": _snippet((c or {}).get("recommended_action"), 200),
            }
            for c in clusters[:10]
        ],
    }


def _summarize_update(iter_dir, exists):
    if not exists.get("constraint_update.json"):
        return None
    raw = _read(iter_dir, "constraint_update.json")
    if jsonutil.is_error(raw):
        return {"_error": raw["_error"]}
    changes = raw.get("changes") or []
    return {
        "status": raw.get("status"),
        "change_count": len(changes),
        "finding_ids": raw.get("finding_ids"),
        "changes": [
            {
                "id": (c or {}).get("id"),
                "op": (c or {}).get("op"),
                "target": _snippet((c or {}).get("target"), 120),
                "expected_effect": _snippet((c or {}).get("expected_effect"), 200),
            }
            for c in changes[:10]
        ],
    }


def _summarize_generation_status(iter_dir, exists):
    # type: (Path, dict) -> object
    """generation_status.json：正式状态文件（in_progress/complete/failed 为合法态）。"""
    if not exists.get("generation_status.json"):
        return None
    raw = _read(iter_dir, "generation_status.json")
    if jsonutil.is_error(raw):
        return {"status": "broken", "state": None, "error": raw["_error"]}
    state = raw.get("state") if isinstance(raw, dict) else None
    return {
        "status": "ok" if state in GENERATION_STATES else "broken",
        "state": state if isinstance(state, str) else None,
    }


def _summarize_dict_artifact(iter_dir, exists, name):
    # type: (Path, dict, str) -> object
    """通用 dict 产物摘要：missing/ok/broken（各按自身契约，解析为 dict 即 ok）。"""
    if not exists.get(name):
        return None
    raw = _read(iter_dir, name)
    if jsonutil.is_error(raw):
        return {"status": "broken", "error": raw["_error"]}
    return {"status": "ok" if isinstance(raw, dict) else "broken"}


def _summarize_patch(iter_dir, exists):
    # type: (Path, dict) -> object
    """constraints_patch.json：补丁须为 op 条目列表（或含 operations 列表）。
    合法空补丁（空列表）不算 broken——按契约允许。"""
    if not exists.get("constraints_patch.json"):
        return None
    raw = _read(iter_dir, "constraints_patch.json")
    if jsonutil.is_error(raw):
        return {"status": "broken", "empty": None, "count": None, "error": raw["_error"]}
    if isinstance(raw, dict):
        ops = raw.get("operations")
        if not isinstance(ops, list):
            return {"status": "broken", "empty": None, "count": None, "error": "缺少 operations 列表"}
        raw = ops
    if not isinstance(raw, list):
        return {"status": "broken", "empty": None, "count": None, "error": "非补丁结构"}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("op"), str):
            return {"status": "broken", "empty": None, "count": None, "error": "补丁条目缺少 op 字段"}
    return {"status": "ok", "empty": not raw, "count": len(raw)}


def _cases_count(iter_dir, exists):
    if not exists.get("cases.json"):
        return None
    raw = _read(iter_dir, "cases.json")
    if isinstance(raw, list):
        return len(raw)
    return None


def page_records(iter_dir, offset=0, limit=None, result_filter=None):
    # type: (Path, int, int, str) -> dict
    """execution_result.records 分页（精简字段，不含 case_json 全文）。"""
    from .. import config as _cfg

    limit = limit or _cfg.RECORDS_PAGE_DEFAULT
    limit = max(1, min(limit, _cfg.RECORDS_PAGE_MAX))
    offset = max(0, offset)

    raw = _read(iter_dir, "execution_result.json")
    if jsonutil.is_error(raw):
        return {"_error": raw["_error"]}
    records = raw.get("records") or []

    def _match(record):
        if not result_filter:
            return True
        value = str((record or {}).get("run_result", "")).lower()
        return result_filter.lower() in value

    matched = [r for r in records if _match(r)]
    page = matched[offset:offset + limit]
    return {
        "total_records": len(records),
        "matched": len(matched),
        "offset": offset,
        "limit": limit,
        "records": [
            {
                "id": (r or {}).get("id"),
                "run_result": (r or {}).get("run_result"),
                "failure_reason": _snippet((r or {}).get("failure_reason"), 500),
            }
            for r in page
        ],
    }
