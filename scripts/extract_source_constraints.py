#!/usr/bin/env python3
"""从算子源码快照确定性提取约束事实，产 source_raw.json。

快照范围由快照生产者决定：``init_run._snapshot_operator_source`` 在树根+算子名
场景走 ``collect_operator_source.collect``（include 不动点闭包 + canndev 多层
+ 后缀变体 + L0 反查，覆盖跨目录 L0 实现）；单算子目录或 collect 失败时回退
SEED 固定位置 glob + L1 stem 闭包。``collect_operator_source.py`` 亦可单独
调用（需补 ``--extra-stem`` 时）。本脚本 rglob 扫快照内所有
``.cpp/.cc/.h/.c``，不跨快照边界。

产三段（供 source-analyst LLM 判读，本脚本不判读）：

- ``aclnn_interfaces``: aclnn 接口签名（op_api/aclnn_*.cpp），过滤 aclnnInner。
- ``platform_matrix``: 源码出现的平台宏（ASCEND910B/ASCEND950/IsRegBase）+
  dtype 枚举（ge::DT_*）集合，供平台交叉校验。
- ``raw_checks``: 约束宏扫描，每项
  ``{condition, error_string, source_location, macro}``。正则集：
  OP_CHECK / OP_LOGE(仅 ACLNN_ERR_PARAM 族) /
  OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE / OP_TILING_CHECK +
  canndev 的 VECTOR_INNER_ERR_REPORT_* 族（两参数 op_name+msg；canndev
  不用 ACLNN_ERR_PARAM_INVALID 那套错误码，全树 0 命中）。

不引入 LLM，只做确定性正则提取。语义判读（raw_checks -> hard_constraints ->
3 文件）由 source-analyst agent 完成。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SOURCE_EXTS = (".cpp", ".cc", ".h", ".hpp", ".c")

# --- raw_checks 正则集 ------------------------------------------------------

# OP_CHECK(condition, OP_LOGE/OP_LOGW(ACLNN_ERR_PARAM_INVALID, "msg"), ...)
# condition 跨多行，非贪婪到 ", OP_LOG[EW]"。捕获 condition + msg。
OP_CHECK_WITH_LOG_RE = re.compile(
    r"OP_CHECK\s*\(\s*([\s\S]*?),\s*OP_LOG[EW]\s*\(\s*ACLNN_ERR_PARAM_INVALID\s*,\s*"
    r'"([^"]*)"'
)

# OP_TILING_CHECK(condition, OP_LOG[EW](ACLNN_ERR_PARAM_INVALID, "msg"), ...)
OP_TILING_CHECK_RE = re.compile(
    r"OP_TILING_CHECK\s*\(\s*([\s\S]*?),\s*OP_LOG[EW]\s*\(\s*ACLNN_ERR_PARAM_INVALID\s*,\s*"
    r'"([^"]*)"'
)

# 独立 OP_LOGE(ACLNN_ERR_PARAM_INVALID, "msg") —— condition 留空，
# 抓 L0 impl 的 Check* 消息（靠上下文 if 判断，condition 由 LLM 回溯）。
STANDALONE_OP_LOGE_RE = re.compile(
    r'OP_LOGE\s*\(\s*ACLNN_ERR_PARAM_INVALID\s*,\s*"([^"]*)"'
)

# OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE(arg) —— 无消息，condition 抓参数。
ADD_TO_LAUNCHER_RE = re.compile(
    r"OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE\s*\(([\s\S]*?)\)"
)

# canndev 写法：OP_CHECK/OP_TILING_CHECK(cond, VECTOR_INNER_ERR_REPORT_*(op, msg), action)
# 日志宏两参数 (op_name, msg)：op_name 可能是字符串字面量("TransData") 或函数调用
# (context->GetNodeName())，用 [^,]* 兼容；msg 抓第二参数。canndev 不用
# ACLNN_ERR_PARAM_INVALID 那套错误码（全树 0 命中），改用 VECTOR_INNER_ERR_REPORT
# 族（含拼写既定的 _TILIING 后缀）。(?:_[A-Z]+)? 覆盖 _TILIING/_INNER 等变体。
VECTOR_ERR_HEAD = r'VECTOR_INNER_ERR_REPORT(?:_[A-Z]+)?\s*\(\s*[^,]*,\s*'
OP_CHECK_VECTOR_RE = re.compile(
    r'OP_CHECK\s*\(\s*([\s\S]*?),\s*' + VECTOR_ERR_HEAD + r'"([^"]*)"'
)
OP_TILING_CHECK_VECTOR_RE = re.compile(
    r'OP_TILING_CHECK\s*\(\s*([\s\S]*?),\s*' + VECTOR_ERR_HEAD + r'"([^"]*)"'
)
# 独立 VECTOR_INNER_ERR_REPORT_*(op, msg) —— condition 留空，靠上下文 if
# 判断（canndev L0 impl 的 Check* 消息，condition 由 LLM 回溯）。op_name
# 同样可能是字符串或函数调用，用 [^,]* 兼容。
STANDALONE_VECTOR_ERR_RE = re.compile(
    r'VECTOR_INNER_ERR_REPORT(?:_[A-Z]+)?\s*\(\s*[^,]*,\s*"([^"]*)"'
)

# --- aclnn_interfaces -------------------------------------------------------

ACLNN_IFACE_RE = re.compile(
    r"\b(aclnn[A-Z]\w*?GetWorkspaceSize)\s*\("
)

# --- platform_matrix --------------------------------------------------------

DTYPE_ENUM_RE = re.compile(r"\bge::DT_([A-Z0-9_]+)\b")
# 平台分支值（源码常见 socVersion == SocVersion::ASCEND910B，前缀可选）。
SOC_VERSION_VALUE_RE = re.compile(r"socVersion\s*==\s*(?:SocVersion::)?(ASCEND[A-Z0-9_]+)")
# IsRegBase() 是 950（RegBase）平台判定，单列。
IS_REGBASE_RE = re.compile(r"\bIsRegBase\s*\(\s*\)")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _clean_condition(cond: str) -> str:
    """合并多行空白，去首尾空白与尾随逗号。"""
    cond = cond.strip().rstrip(",").strip()
    return re.sub(r"\s+", " ", cond)


def _scan_file(path: Path, snapshot_root: Path) -> tuple[list[dict], list[str], dict]:
    """扫单文件，返回 (raw_checks, aclnn_interfaces, platform_hits)。

    platform_hits = {"soc_versions": [...], "dtypes": [...]}（本文件命中的）。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], [], {"soc_versions": [], "dtypes": []}

    rel = path.relative_to(snapshot_root).as_posix()
    checks: list[dict] = []
    # 记录已被组合宏（OP_CHECK/OP_TILING_CHECK + 日志宏）匹配占用的日志宏
    # 区间，避免独立 OP_LOGE / VECTOR_INNER_ERR_REPORT 重复抓同一条。
    consumed_spans: list[tuple[int, int]] = []

    def _mark_consumed(marker: str, m_start: int, m_end: int) -> None:
        pos = text.find(marker, m_start)
        if pos != -1:
            consumed_spans.append((pos, m_end))

    for m in OP_CHECK_WITH_LOG_RE.finditer(text):
        checks.append({
            "macro": "OP_CHECK",
            "condition": _clean_condition(m.group(1)),
            "error_string": m.group(2),
            "source_location": f"{rel}:{_line_of(text, m.start())}",
        })
        _mark_consumed("OP_LOG", m.start(), m.end())

    for m in OP_TILING_CHECK_RE.finditer(text):
        checks.append({
            "macro": "OP_TILING_CHECK",
            "condition": _clean_condition(m.group(1)),
            "error_string": m.group(2),
            "source_location": f"{rel}:{_line_of(text, m.start())}",
        })
        _mark_consumed("OP_LOG", m.start(), m.end())

    # canndev 写法：OP_CHECK/OP_TILING_CHECK(cond, VECTOR_INNER_ERR_REPORT_*(op, msg), action)
    for m in OP_CHECK_VECTOR_RE.finditer(text):
        checks.append({
            "macro": "OP_CHECK",
            "condition": _clean_condition(m.group(1)),
            "error_string": m.group(2),
            "source_location": f"{rel}:{_line_of(text, m.start())}",
        })
        _mark_consumed("VECTOR_INNER_ERR_REPORT", m.start(), m.end())

    for m in OP_TILING_CHECK_VECTOR_RE.finditer(text):
        checks.append({
            "macro": "OP_TILING_CHECK",
            "condition": _clean_condition(m.group(1)),
            "error_string": m.group(2),
            "source_location": f"{rel}:{_line_of(text, m.start())}",
        })
        _mark_consumed("VECTOR_INNER_ERR_REPORT", m.start(), m.end())

    for m in ADD_TO_LAUNCHER_RE.finditer(text):
        checks.append({
            "macro": "OP_CHECK_ADD_TO_LAUNCHER_LIST_AICORE",
            "condition": _clean_condition(m.group(1)),
            "error_string": "",
            "source_location": f"{rel}:{_line_of(text, m.start())}",
        })

    # 独立 OP_LOGE，跳过已被组合匹配占用的。
    for m in STANDALONE_OP_LOGE_RE.finditer(text):
        if any(start <= m.start() < end for start, end in consumed_spans):
            continue
        checks.append({
            "macro": "OP_LOGE",
            "condition": "",
            "error_string": m.group(1),
            "source_location": f"{rel}:{_line_of(text, m.start())}",
        })

    # 独立 VECTOR_INNER_ERR_REPORT_*(op, msg)，跳过已被组合匹配占用的。
    for m in STANDALONE_VECTOR_ERR_RE.finditer(text):
        if any(start <= m.start() < end for start, end in consumed_spans):
            continue
        checks.append({
            "macro": "VECTOR_INNER_ERR_REPORT",
            "condition": "",
            "error_string": m.group(1),
            "source_location": f"{rel}:{_line_of(text, m.start())}",
        })

    # aclnn 接口签名（去重保序）。
    ifaces: list[str] = []
    for m in ACLNN_IFACE_RE.finditer(text):
        name = m.group(1)
        if "Inner" in name:  # 过滤 aclnnInner*
            continue
        if name not in ifaces:
            ifaces.append(name)

    soc_versions = list(dict.fromkeys(SOC_VERSION_VALUE_RE.findall(text)))
    dtypes = list(dict.fromkeys(DTYPE_ENUM_RE.findall(text)))
    is_regbase = bool(IS_REGBASE_RE.search(text))
    return checks, ifaces, {"soc_versions": soc_versions, "dtypes": dtypes, "is_regbase": is_regbase}


def extract(snapshot_root: Path) -> dict:
    """扫整个快照目录，汇总 source_raw 结构。"""
    all_checks: list[dict] = []
    all_ifaces: list[str] = []
    platform_by_file: list[dict] = []
    all_soc: list[str] = []
    all_dtypes: list[str] = []
    any_regbase = False

    files = sorted(
        p for p in snapshot_root.rglob("*")
        if p.is_file() and p.suffix in SOURCE_EXTS
    )
    for path in files:
        checks, ifaces, plat = _scan_file(path, snapshot_root)
        all_checks.extend(checks)
        for name in ifaces:
            if name not in all_ifaces:
                all_ifaces.append(name)
        if plat["soc_versions"] or plat["dtypes"] or plat["is_regbase"]:
            platform_by_file.append({
                "file": path.relative_to(snapshot_root).as_posix(),
                "soc_versions": plat["soc_versions"],
                "dtypes": [f"ge::DT_{d}" for d in plat["dtypes"]],
                "is_regbase": plat["is_regbase"],
            })
        for s in plat["soc_versions"]:
            if s not in all_soc:
                all_soc.append(s)
        for d in plat["dtypes"]:
            if d not in all_dtypes:
                all_dtypes.append(d)
        if plat["is_regbase"]:
            any_regbase = True

    return {
        "operator_src_snapshot": str(snapshot_root),
        "aclnn_interfaces": all_ifaces,
        "platform_matrix": {
            "soc_versions": all_soc,
            "is_reg_base_used": any_regbase,
            "dtypes": [f"ge::DT_{d}" for d in all_dtypes],
            "by_file": platform_by_file,
        },
        "raw_checks": all_checks,
        "stats": {
            "files_scanned": len(files),
            "raw_checks_count": len(all_checks),
            "aclnn_interfaces_count": len(all_ifaces),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从算子源码快照确定性提取约束事实，产 source_raw.json。"
    )
    parser.add_argument(
        "--snapshot", required=True,
        help="算子源码快照目录（init_run --src 复制到 inputs/src_snapshot/）。",
    )
    parser.add_argument(
        "--out", required=True,
        help="source_raw.json 输出路径。",
    )
    parser.add_argument(
        "--only", default="all",
        help="兼容占位参数（兄弟项目接口），当前只实现 all。",
    )
    args = parser.parse_args()

    snapshot_root = Path(args.snapshot).resolve()
    if not snapshot_root.is_dir():
        print(json.dumps(
            {"ok": False, "code": "SNAPSHOT_NOT_FOUND",
             "snapshot": str(snapshot_root)},
            ensure_ascii=False,
        ))
        return 2

    result = extract(snapshot_root)
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(
        {"ok": True, "out": str(out_path),
         "stats": result["stats"]},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
