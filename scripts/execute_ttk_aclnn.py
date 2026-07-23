#!/usr/bin/env python3
"""Remote TTK ACLNN execution over SSH.

Modes:
    --validate   python3 -m ttk aclnn -i <csv> --validate --plat=<plat>
    --npu        python3 -m ttk aclnn -i <csv> --plat=<plat>  (default NPU execution)

Downloads results.csv and log files back to local.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv as csv_mod
import json
import re
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from executer.ssh import (
    ServerEndpoint, SSHEngineError, connect, run, sftp_download_file, upload_file,
)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "operator"


async def _download_via_shell(conn, remote_path: str, local_path: Path) -> bool:
    encoded = await run(conn, f"base64 {shlex.quote(remote_path)}", timeout=120)
    if encoded.exit_code != 0 or not encoded.stdout.strip():
        return False
    try:
        payload = base64.b64decode("".join(encoded.stdout.splitlines()), validate=True)
    except (ValueError, base64.binascii.Error):
        return False
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(payload)
    return True


def _parse_results(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"passed": 0, "failed": 0, "total": 0, "rows": []}
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv_mod.DictReader(handle))
    passed = sum(
        1 for r in rows
        if str(r.get("precision_status") or r.get("status") or "").upper() == "PASS"
    )
    return {"passed": passed, "failed": len(rows) - passed, "total": len(rows), "rows": rows}


async def _run_aclnn(
    csv_path: Path,
    server: dict[str, Any],
    artifact_dir: Path,
    mode: str = "validate",
    test_indexes: str = "",
    timeout: float = 600,
) -> dict[str, Any]:
    started = time.monotonic()
    ttk = server.get("ttk") or {}
    remote_root = str(ttk.get("remote_root") or "/data/ops-test-kit/ttk_cases").rstrip("/")
    repo_path = str(ttk.get("repo_path") or "/data/ops-test-kit").rstrip("/")
    python = str(ttk.get("python") or "python3")
    env_init = str(ttk.get("env_init_script") or server.get("env_init_script", "")).strip()
    plat = str(ttk.get("plat") or "Ascend910B1")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    op_name = csv_path.stem
    remote_dir = f"{remote_root}/{_safe_name(op_name)}_{stamp}"
    remote_csv = f"{remote_dir}/{csv_path.name}"
    remote_results = f"{remote_dir}/results.csv"

    artifact_dir.mkdir(parents=True, exist_ok=True)
    endpoint = ServerEndpoint.from_server_row(server)
    conn = None

    try:
        conn = await connect(endpoint)
        mkdir = await run(conn, f"mkdir -p {shlex.quote(remote_dir)}")
        if mkdir.exit_code != 0:
            raise SSHEngineError(f"mkdir failed: {mkdir.stderr}")
        await upload_file(conn, csv_path, remote_csv)

        ttk_args = (
            f"-i {shlex.quote(csv_path.name)} "
            f"--plat={shlex.quote(plat)}"
        )
        if mode == "validate":
            ttk_args += " --validate"
        if test_indexes:
            ttk_args += f" --ti={shlex.quote(test_indexes)}"
        if mode == "npu":
            ttk_args += " --warmup False"

        parts = []
        if env_init:
            parts.append(env_init)
        parts.append(f"cd {shlex.quote(remote_dir)}")
        parts.append(
            f"PYTHONPATH={shlex.quote(repo_path)}:${{PYTHONPATH:-}} "
            f"{shlex.quote(python)} -m ttk aclnn {ttk_args}"
        )
        command = " && ".join(parts)

        result = await run(conn, command, timeout=timeout)
        duration = time.monotonic() - started

        (artifact_dir / "remote_stdout.log").write_text(result.stdout, encoding="utf-8")
        (artifact_dir / "remote_stderr.log").write_text(result.stderr, encoding="utf-8")
        (artifact_dir / "remote_command.txt").write_text(command, encoding="utf-8")

        # Download result CSV (TTK outputs <input>_result.csv or results.csv)
        local_results = artifact_dir / "results.csv"
        results_info = {}
        if mode == "npu":
            for candidate in (remote_results, f"{remote_dir}/{csv_path.stem}_result.csv"):
                if not local_results.is_file():
                    await _download_via_shell(conn, candidate, local_results)
            if local_results.is_file():
                results_info = _parse_results(local_results)

        # Download all log/csv/json files via base64 (SFTP is unreliable on this server)
        local_log_dir = artifact_dir / "log"
        local_log_dir.mkdir(parents=True, exist_ok=True)
        listing = await run(
            conn,
            f"find {shlex.quote(remote_dir)} -maxdepth 1 -type f "
            f"\\( -name '*.log' -o -name '*.csv' -o -name '*.json' \\) -print",
            timeout=30,
        )
        for remote_file in listing.stdout.splitlines():
            remote_file = remote_file.strip()
            if not remote_file:
                continue
            fname = Path(remote_file).name
            local_path = local_log_dir / fname
            if local_path == local_results:
                continue
            await _download_via_shell(conn, remote_file, local_path)

        passed = result.exit_code == 0
        npu_pass = results_info.get("passed", 0)
        npu_fail = results_info.get("failed", 0)
        npu_total = results_info.get("total", 0)

        status = "success"
        if not passed:
            status = "failed"
        elif mode == "npu" and npu_fail > 0:
            status = "partial"
        elif mode == "npu" and npu_total == 0:
            status = "failed"  # no results produced

        return {
            "status": status, "mode": f"ttk_aclnn_{mode}",
            "test_framework": "ttk", "ttk_mode": "aclnn",
            "exit_code": result.exit_code,
            "stdout": result.stdout, "stderr": result.stderr,
            "duration": duration,
            "remote_dir": remote_dir, "remote_command": command,
            "local_artifact_dir": str(artifact_dir),
            "results_csv": str(local_results) if local_results.is_file() else None,
            "npu_passed": npu_pass, "npu_failed": npu_fail, "npu_total": npu_total,
            "passed": npu_pass, "failed": npu_fail, "total": npu_total,
            "records": results_info.get("rows", []),
            "engine_error": "" if status == "success" else (
                "TTK ACLNN command failed" if not passed else
                "TTK ACLNN produced no passing result set"
            ),
        }
    except SSHEngineError as exc:
        return {
            "status": "error", "mode": f"ttk_aclnn_{mode}",
            "test_framework": "ttk", "ttk_mode": "aclnn",
            "passed": 0, "failed": 0, "total": 0, "records": [],
            "engine_error": str(exc),
            "duration": time.monotonic() - started,
            "remote_dir": remote_dir,
            "local_artifact_dir": str(artifact_dir),
        }
    finally:
        if conn is not None:
            conn.close()
            await conn.wait_closed()


def run_aclnn(**kwargs) -> dict[str, Any]:
    return asyncio.run(_run_aclnn(**kwargs))


def main() -> None:
    parser = argparse.ArgumentParser(description="Remote TTK ACLNN execution")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--npu", action="store_true", help="NPU execution (default: --validate)")
    parser.add_argument("--ti", default="", help="Test indexes, e.g. 0-2 or 0")
    parser.add_argument("--server-config", type=Path, default="servers.json")
    parser.add_argument("--artifact-dir", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    config = json.loads(args.server_config.read_text(encoding="utf-8"))
    servers = config.get("servers", [])
    if not servers:
        raise SystemExit("servers.json: no servers configured")

    server = next((s for s in servers if s.get("supports_npu")), servers[0])
    if not server.get("ttk"):
        raise SystemExit(f"Server {server.get('name')} has no 'ttk' block")

    mode = "npu" if args.npu else "validate"
    artifact_dir = args.artifact_dir or (args.csv.parent / "ttk_aclnn_artifacts")

    print(f"Server:  {server.get('name')} ({server.get('ip')})")
    print(f"CSV:     {args.csv}")
    print(f"Mode:    {mode}")
    print()

    result = run_aclnn(
        csv_path=args.csv, server=server, artifact_dir=artifact_dir,
        mode=mode, test_indexes=args.ti, timeout=args.timeout,
    )

    print(f"Status:   {result['status']}")
    print(f"Exit:     {result.get('exit_code', 'N/A')}")
    print(f"Duration: {result.get('duration', 0):.1f}s")

    if mode == "npu":
        print(f"NPU Pass: {result.get('npu_passed', '?')}/{result.get('npu_total', '?')}")
        if result.get("npu_failed", 0) > 0:
            print(f"NPU Fail: {result['npu_failed']}")
            for row in _parse_results(Path(result.get("results_csv", ""))).get("rows", []):
                st = str(row.get("precision_status", "")).upper()
                if st != "PASS":
                    print(f"  FAIL {row.get('testcase_name', '?')}: {st}")

    if result.get("engine_error"):
        print(f"Error:    {result['engine_error']}")
    stdout = result.get("stdout", "")
    if stdout:
        # Print last 15 lines of stdout (summary)
        lines = stdout.splitlines()
        for line in lines[-20:]:
            print(f"  {line}")
    stderr = (result.get("stderr") or "").strip()
    if stderr:
        print(f"\n--- STDERR ---\n{stderr[:2000]}")
    print(f"\nArtifacts: {result.get('local_artifact_dir')}")

    ok = result["status"] in ("success", "partial")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
