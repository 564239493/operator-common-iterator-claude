#!/usr/bin/env python3
"""Execute generated cases in deterministic mock mode or operator-agent real mode."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from runtime_config import (
    ROOT,
    config_error_payload,
    resolve_input_path,
    validate_server_config,
)


def case_id(case: dict[str, Any], index: int) -> str:
    return str(case.get("id") or case.get("case_name") or f"case_{index:04d}")


def mock_execute(cases: list[dict[str, Any]], fail_every: int) -> dict[str, Any]:
    records = []
    for index, case in enumerate(cases, start=1):
        failed = fail_every > 0 and index % fail_every == 0
        records.append(
            {
                "id": case_id(case, index),
                "run_result": "fail" if failed else "pass",
                "failure_reason": (
                    "MOCK_CONSTRAINT_MISMATCH: deterministic diagnostic failure" if failed else ""
                ),
                "case_json": case,
            }
        )
    passed = sum(item["run_result"] == "pass" for item in records)
    failed = len(records) - passed
    return {
        "status": "success" if failed == 0 else "failed",
        "mode": "mock",
        "passed": passed,
        "failed": failed,
        "total": len(records),
        "records": records,
        "engine_error": "",
    }


def load_server(path: Path, platform: str) -> dict[str, Any]:
    servers = json.loads(path.read_text(encoding="utf-8")).get("servers", [])
    for server in servers:
        if platform in server.get("platforms", []):
            return server
    if servers:
        return servers[0]
    raise RuntimeError("no server configured")


async def real_execute(args: argparse.Namespace) -> dict[str, Any]:
    agent_root = ROOT.parent / "operator-agent"
    agent_src = agent_root / "packages" / "agent" / "src"
    shared_src = agent_root / "packages" / "shared" / "src"
    if not agent_src.is_dir():
        raise RuntimeError(f"operator-agent dependency not found: {agent_root}")
    sys.path[:0] = [str(agent_src), str(shared_src)]

    from agent.nodes.executer_subgraph import create_executer_subgraph

    server = load_server(args.server_config_path, args.platform)
    state = {
        "operator_name": args.operator,
        "cases_path": str(Path(args.cases).resolve()),
        "content": Path(args.doc).read_text(encoding="utf-8"),
        "server_info": server,
        "task_type": "accuracy",
        "execution_count": 1,
        "run_id": args.run_id,
    }
    result = await create_executer_subgraph().ainvoke(state)
    if result.get("error"):
        return {
            "status": "error",
            "mode": "real",
            "passed": 0,
            "failed": 0,
            "total": 0,
            "records": [],
            "engine_error": str(result["error"]),
        }
    execution = dict(result.get("exec_result") or {})
    report = execution.get("task_report_data") or {}
    records = report.get("report_records") or execution.get("records") or []
    passed = int(report.get("passed", execution.get("passed", 0)) or 0)
    failed = int(report.get("failed", execution.get("failed", 0)) or 0)
    execution.update(
        {
            "status": "success" if failed == 0 and passed > 0 else "failed",
            "mode": "real",
            "passed": passed,
            "failed": failed,
            "total": passed + failed,
            "records": records,
            "engine_error": "",
        }
    )
    return execution


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("mock", "real"), default="real")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fail-every", type=int, default=3)
    parser.add_argument("--doc")
    parser.add_argument("--operator")
    parser.add_argument("--platform", default="Atlas A3 训练系列产品/Atlas A3 推理系列产品")
    parser.add_argument("--server-config", default="servers.json")
    parser.add_argument("--run-id", default="manual")
    args = parser.parse_args()

    cases_path = resolve_input_path(args.cases)
    output = resolve_input_path(args.output)
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise SystemExit("cases must be a non-empty JSON array")
    if args.mode == "mock":
        result = mock_execute(cases, args.fail_every)
    else:
        if not args.doc:
            print(json.dumps(
                {
                    "ok": False,
                    "requires_user_action": True,
                    "code": "OPERATOR_DOC_REQUIRED",
                    "message": "真实执行需要 --doc；请传入 run 目录中的算子文档快照。",
                },
                ensure_ascii=False,
            ))
            return 2
        args.doc = str(resolve_input_path(args.doc))
        args.operator = args.operator or Path(args.doc).stem
        args.cases = str(cases_path)
        args.server_config_path, config_errors = validate_server_config(args.server_config)
        if config_errors:
            print(json.dumps(
                config_error_payload(args.server_config_path, config_errors),
                ensure_ascii=False,
            ))
            return 2
        try:
            result = asyncio.run(real_execute(args))
        except Exception as exc:
            result = {
                "status": "error",
                "mode": "real",
                "passed": 0,
                "failed": 0,
                "total": 0,
                "records": [],
                "engine_error": str(exc),
            }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result.get(key) for key in ("status", "mode", "passed", "failed", "total")}))
    return 0 if not result.get("engine_error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
