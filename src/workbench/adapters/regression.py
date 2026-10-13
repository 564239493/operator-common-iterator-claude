"""regression_check.json 分类：区分"求值器不支持表达式"与"真实回归"。

实证边界：样例 run 的全部 regression_check.json 均为 ok:false，但失败原因是
求值器不支持 Slice 表达式（"could not be evaluated ... unsupported expression node: Slice"），
属于"无法判定"而非真实回归证据。UI 必须按 kind 区分渲染。
"""

_EVALUATOR_MARKERS = (
    "could not be evaluated",
    "unsupported expression node",
    "无法求值",
)


def classify(raw):
    # type: (dict) -> dict
    """返回 {ok, kind, checked_cases, regressions_count, samples[]}。

    kind ∈ none / evaluator_unsupported / real_regression / mixed
    """
    if not isinstance(raw, dict):
        return {"ok": None, "kind": "none", "checked_cases": 0, "regressions_count": 0, "samples": []}
    ok = raw.get("ok")
    regressions = raw.get("regressions") or []
    checked = raw.get("checked_cases", raw.get("total_passed_cases", 0)) or 0

    if ok or not regressions:
        return {
            "ok": ok,
            "kind": "none",
            "checked_cases": checked,
            "regressions_count": len(regressions),
            "attempt": raw.get("attempt"),
            "max_attempts": raw.get("max_attempts"),
            "limit_reached": raw.get("limit_reached"),
            "samples": [],
        }

    texts = []
    for item in regressions:
        if isinstance(item, dict):
            for issue in item.get("issues") or []:
                texts.append(str(issue))
        else:
            texts.append(str(item))
    evaluator_hits = [t for t in texts if any(m in t for m in _EVALUATOR_MARKERS)]

    if evaluator_hits and len(evaluator_hits) == len(texts):
        kind = "evaluator_unsupported"
    elif evaluator_hits:
        kind = "mixed"
    else:
        kind = "real_regression"

    return {
        "ok": ok,
        "kind": kind,
        "checked_cases": checked,
        "regressions_count": len(regressions),
        "attempt": raw.get("attempt"),
        "max_attempts": raw.get("max_attempts"),
        "limit_reached": raw.get("limit_reached"),
        "samples": texts[:3],
    }
