#!/usr/bin/env python3
"""从算子源码快照提取确定性约束与错误串证据(未经 LLM 判读的原始事实)。

子模块(--only):
  binary   解析 op_host/config/<platform>/<op>_binary.json -> platform_matrix
           (从路径取 platform, 从 op_list 取 dtype/format/attrs/input/output 名)
  checks   扫 op_host/**/*.cpp 的 OP_CHECK_IF / CHECK_COND -> raw_checks
           (条件 + 错误串 + 源码位置)
  aclnn    解析 op_host/op_api/aclnn_*.cpp 的 GetWorkspaceSize 签名 -> aclnn_interfaces
           (过滤 aclnnInner 内部接口; 处理一对多: 一个算子目录多个 aclnn 接口)
  all      顺序执行以上全部, 输出合并的 source_raw.json (默认)

输出 source_raw.json, 供 source-analyst agent 做 expr_type 归类与约束差异判读。
本脚本只做确定性正则/JSON 提取, 不做语义判读,
遵循 CLAUDE.md "Python 只负责确定性的校验/留痕, 业务推理通过 Skill 与 Agent"。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def parse_binary(snapshot: Path) -> tuple[dict, str]:
    """解析所有平台的 binary.json, 返回 (platform_matrix, operator_name)。

    platform 形如 op_host/config/<platform>/<op>_binary.json, 从路径取平台名。
    每个 op_list 条目是一个 dtype 派发; 聚合该平台支持的 dtype/format/attrs。
    """
    matrix: dict[str, dict] = {}
    operator_name = ""
    config_dir = snapshot / "op_host" / "config"
    if not config_dir.is_dir():
        return matrix, operator_name
    for bj in config_dir.glob("**/*binary.json"):
        platform = bj.parent.name
        try:
            data = json.loads(bj.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if not operator_name and data.get("op_type"):
            operator_name = data["op_type"]
        dtypes: list[str] = []
        formats: set[str] = set()
        attrs: dict = {}
        input_names: list[str] = []
        output_names: list[str] = []
        for entry in data.get("op_list", []):
            ins = entry.get("inputs", []) or []
            if ins:
                dt = ins[0].get("dtype")
                if dt and dt not in dtypes:
                    dtypes.append(dt)
            for i in ins:
                if i.get("format"):
                    formats.add(i["format"])
                if i.get("name") and i["name"] not in input_names:
                    input_names.append(i["name"])
            for o in entry.get("outputs", []) or []:
                if o.get("name") and o["name"] not in output_names:
                    output_names.append(o["name"])
            for a in entry.get("attrs", []) or []:
                attrs[a.get("name", "")] = a.get("value")
        matrix[platform] = {
            "dtype": dtypes,
            "format": sorted(formats),
            "attrs": attrs,
            "input_names": input_names,
            "output_names": output_names,
        }
    return matrix, operator_name


# OP_CHECK_IF(cond, OP_LOGE(ctx, "err %ld", args), return ge::GRAPH_FAILED)
OP_CHECK_RE = re.compile(
    r'OP_CHECK_IF\s*\(\s*(?P<cond>.+?),\s*OP_LOGE\s*\([^,]+,\s*"(?P<err>[^"]*)"',
    re.DOTALL,
)
# CHECK_COND(cond, ACLNN_ERR_PARAM_INVALID, "err")  (aclnn op_api 入口)
CHECK_COND_RE = re.compile(
    r'CHECK_COND\s*\(\s*(?P<cond>.+?),\s*[\w.]+,\s*"(?P<err>[^"]*)"',
    re.DOTALL,
)


def extract_checks(snapshot: Path) -> list[dict]:
    """扫 op_host 下所有 .cpp, 抽 OP_CHECK_IF / CHECK_COND 的条件与错误串。"""
    checks: list[dict] = []
    op_host = snapshot / "op_host"
    if not op_host.is_dir():
        return checks
    for cpp in op_host.rglob("*.cpp"):
        rel = cpp.relative_to(snapshot).as_posix()
        text = cpp.read_text(encoding="utf-8", errors="replace")
        for rx, kind in ((OP_CHECK_RE, "OP_CHECK_IF"), (CHECK_COND_RE, "CHECK_COND")):
            for m in rx.finditer(text):
                start = m.start()
                line = text[:start].count("\n") + 1
                cond = re.sub(r"\s+", " ", m.group("cond")).strip()
                if len(cond) > 200:
                    cond = cond[:197] + "..."
                err = m.group("err")
                if not err:
                    continue
                checks.append({
                    "kind": kind,
                    "source_location": f"{rel}:{line}",
                    "condition": cond,
                    "error_string": err,
                })
    return checks


# aclnnStatus aclnnXxxGetWorkspaceSize(...)  (过滤 aclnnInner 内部接口)
ACLNN_RE = re.compile(r'aclnnStatus\s+(aclnn(?!Inner)\w*GetWorkspaceSize)\s*\(')


def parse_aclnn(snapshot: Path) -> list[str]:
    """解析 op_api/aclnn_*.cpp 的 GetWorkspaceSize 签名, 返回 aclnn 接口名列表。"""
    interfaces: list[str] = []
    api_dir = snapshot / "op_host" / "op_api"
    if not api_dir.is_dir():
        return interfaces
    for cpp in sorted(api_dir.glob("aclnn_*.cpp")):
        text = cpp.read_text(encoding="utf-8", errors="replace")
        for m in ACLNN_RE.finditer(text):
            name = m.group(1)
            if name not in interfaces:
                interfaces.append(name)
    return sorted(interfaces)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从算子源码快照提取确定性约束与错误串(source_raw.json)。"
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        help="src_snapshot 目录路径(如 runs/<run>/inputs/src_snapshot)。",
    )
    parser.add_argument(
        "--only",
        choices=("all", "binary", "checks", "aclnn"),
        default="all",
        help="只提取某一类(默认 all, 输出合并的 source_raw.json)。",
    )
    parser.add_argument("--out", default=None, help="输出文件路径(默认 stdout)。")
    args = parser.parse_args()

    snapshot = Path(args.snapshot)
    if not snapshot.is_dir():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "SNAPSHOT_NOT_FOUND",
                "message": "src_snapshot 目录不存在。",
                "snapshot": str(snapshot),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 2

    result: dict = {"src_snapshot": str(snapshot)}
    if args.only in ("all", "binary"):
        matrix, opname = parse_binary(snapshot)
        result["platform_matrix"] = matrix
        if opname:
            result["operator_name"] = opname
    if args.only in ("all", "aclnn"):
        result["aclnn_interfaces"] = parse_aclnn(snapshot)
    if args.only in ("all", "checks"):
        result["raw_checks"] = extract_checks(snapshot)

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        summary = {
            "ok": True,
            "out": args.out,
            "operator_name": result.get("operator_name", ""),
            "platforms": list(result.get("platform_matrix", {}).keys()),
            "aclnn_interfaces": result.get("aclnn_interfaces", []),
            "raw_checks_count": len(result.get("raw_checks", [])),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
