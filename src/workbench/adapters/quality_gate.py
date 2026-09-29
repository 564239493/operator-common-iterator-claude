"""quality_gate.json 多形态归一化。

实测样例中 checks 元素存在多种键形态：
  {name,result,evidence} / {name,passed,detail} / {name,status,detail} / {name,result,detail}
顶层汇总键 checks_summary 或 summary 二选一，也可能两者都缺。

归一原则：任何不认识的取值一律归 "unknown"，绝不默认通过。
"""

_PASS_WORDS = {"pass", "passed", "ok", "success", "succeeded", "true", "yes"}
_FAIL_WORDS = {"fail", "failed", "error", "blocked", "false", "no"}
_WARN_WORDS = {"warn", "warning"}


def normalize_value(value):
    """把任意形态的检查结论归一到 pass/fail/warn/unknown。"""
    if isinstance(value, bool):
        return "pass" if value else "fail"
    if value is None:
        return "unknown"
    text = str(value).strip().lower()
    if text in _PASS_WORDS:
        return "pass"
    if text in _FAIL_WORDS:
        return "fail"
    if text in _WARN_WORDS:
        return "warn"
    return "unknown"


def normalize_check(element):
    # type: (dict) -> dict
    """归一单个 checks 元素，兼容全部已知键形态。"""
    if not isinstance(element, dict):
        return {"name": str(element), "result": "unknown", "detail": "", "raw_key": None}
    raw_key = None
    raw_value = None
    for candidate in ("result", "passed", "status"):
        if candidate in element:
            raw_key = candidate
            raw_value = element[candidate]
            break
    detail = element.get("evidence", element.get("detail", ""))
    if not isinstance(detail, str):
        detail = str(detail)
    return {
        "name": element.get("name", "(unnamed)"),
        "result": normalize_value(raw_value),
        "detail": detail,
        "raw_key": raw_key,
    }


def normalize_gate(raw):
    # type: (dict) -> dict
    """归一整个 quality_gate.json。"""
    if not isinstance(raw, dict):
        return {"status": "unknown", "checks": [], "summary": None, "_error": "非 dict 内容"}
    checks_raw = raw.get("checks") or []
    checks = [normalize_check(el) for el in checks_raw if el is not None]

    summary_key = None
    summary_raw = None
    for candidate in ("checks_summary", "summary"):
        if isinstance(raw.get(candidate), dict):
            summary_key = candidate
            summary_raw = raw[candidate]
            break
    summary_derived = False
    if summary_raw:
        summary = {
            "total": summary_raw.get("total", len(checks)),
            "passed": summary_raw.get("passed", summary_raw.get("pass")),
            "failed": summary_raw.get("failed", summary_raw.get("fail")),
            "raw_key": summary_key,
            "summary_derived": False,
        }
    else:
        # 顶层无汇总键 → 从 checks 现算
        summary_derived = True
        summary = {
            "total": len(checks),
            "passed": sum(1 for c in checks if c["result"] == "pass"),
            "failed": sum(1 for c in checks if c["result"] == "fail"),
            "warned": sum(1 for c in checks if c["result"] == "warn"),
            "unknown": sum(1 for c in checks if c["result"] == "unknown"),
            "raw_key": None,
            "summary_derived": True,
        }

    blocking = raw.get("blocking_issues") or []
    warnings = raw.get("warnings") or []
    return {
        "status": normalize_value(raw.get("status")),
        "status_raw": raw.get("status"),
        "next_state": raw.get("next_state"),
        "next_state_reasoning": raw.get("next_state_reasoning"),
        "checks": checks,
        "summary": summary,
        "summary_derived": summary_derived,
        "blocking_issues": blocking,
        "warnings": warnings,
        "iteration": raw.get("iteration", raw.get("gate_for_iteration")),
    }
