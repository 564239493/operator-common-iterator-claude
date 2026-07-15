"""Remote TTK E2E execution over the existing SSH/SFTP transport."""
from __future__ import annotations

import asyncio
import base64
import csv
import json
import re
import shlex
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .ssh import (
    SSHEngineError, ServerEndpoint, connect, run, sftp_download_file,
    sftp_download_tree, upload_file,
)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "operator"


def _parse_results(path: Path) -> tuple[int, int, list[dict[str, str]]]:
    if not path.is_file():
        return 0, 0, []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    passed = 0
    for row in rows:
        status = str(row.get("precision_status") or row.get("status") or "").upper()
        passed += status == "PASS"
    return int(passed), len(rows) - int(passed), rows


async def _download_via_shell(conn, remote_path: str, local_path: Path) -> bool:
    """Download through SSH stdout when the server's SFTP/SCP is broken."""
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


async def _run_remote(
    cases_path: Path,
    plugin_path: Path | None,
    operator_name: str,
    server: dict[str, Any],
    artifact_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    ttk = server.get("ttk") or {}
    remote_root = str(ttk.get("remote_root") or "/home/operator_ttk/runs").rstrip("/")
    repo_path = str(ttk.get("repo_path") or "/home/operator_ttk/ops-test-kit").rstrip("/")
    python = str(ttk.get("python") or "python3")
    allow_internal_format = bool(ttk.get("allow_internal_format", True))
    env_init = str(ttk.get("env_init_script") or server.get("env_init_script") or "").strip()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    remote_dir = f"{remote_root}/{_safe_name(operator_name)}_{stamp}"
    remote_cases = f"{remote_dir}/{cases_path.name}"
    remote_plugin = f"{remote_dir}/{plugin_path.name}" if plugin_path else None
    remote_results = f"{remote_dir}/results.csv"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    endpoint = ServerEndpoint.from_server_row(server)
    conn = None
    try:
        conn = await connect(endpoint)
        mkdir = await run(conn, f"mkdir -p {shlex.quote(remote_dir)}")
        if mkdir.exit_code != 0:
            raise SSHEngineError(f"创建远程 TTK 目录失败: {mkdir.stderr}")
        await upload_file(conn, cases_path, remote_cases)
        if plugin_path:
            await upload_file(conn, plugin_path, remote_plugin)

        parts = []
        if env_init:
            parts.append(
                f"source {shlex.quote(env_init)}" if " " not in env_init else env_init
            )
        parts.append(f"cd {shlex.quote(remote_dir)}")
        python_entry = f"{shlex.quote(python)} -m ttk"
        if allow_internal_format:
            bootstrap = (
                "import runpy,torch_npu;"
                "torch_npu.npu.config.allow_internal_format=True;"
                "runpy.run_module('ttk',run_name='__main__')"
            )
            python_entry = f"{shlex.quote(python)} -c {shlex.quote(bootstrap)}"
        ttk_cmd = (
            f"PYTHONPATH={shlex.quote(repo_path)}:${{PYTHONPATH:-}} "
            f"{python_entry} e2e "
            f"-i {shlex.quote(cases_path.name)} --backend npu "
            f"-o {shlex.quote(remote_results)} --single-log"
        )
        if plugin_path:
            ttk_cmd += f" --plugin {shlex.quote(plugin_path.name)}"
        parts.append(ttk_cmd)
        command = " && ".join(parts)
        result = await run(conn, command, timeout=timeout)

        local_results = artifact_dir / "results.csv"
        await sftp_download_file(conn, remote_results, local_results)
        if not local_results.is_file():
            await _download_via_shell(conn, remote_results, local_results)
        remote_log = f"{remote_dir}/log"
        local_log = artifact_dir / "log"
        try:
            await sftp_download_tree(conn, remote_log, local_log)
        except SSHEngineError:
            # Preserve command output even when this TTK version writes logs elsewhere.
            pass
        listing = await run(
            conn,
            f"find {shlex.quote(remote_dir)} -maxdepth 1 -type f -name 'ttk-*.log' -print",
            timeout=60,
        )
        for remote_log_file in listing.stdout.splitlines():
            remote_log_file = remote_log_file.strip()
            if remote_log_file:
                await _download_via_shell(
                    conn, remote_log_file, local_log / Path(remote_log_file).name
                )
        (artifact_dir / "remote_stdout.log").write_text(result.stdout, encoding="utf-8")
        (artifact_dir / "remote_stderr.log").write_text(result.stderr, encoding="utf-8")
        passed, failed, rows = _parse_results(local_results)
        engine_error = ""
        status = "success" if result.exit_code == 0 and failed == 0 and rows else "failed"
        if not local_results.is_file():
            status = "error"
            engine_error = "TTK 未生成 results.csv"
        return {
            "status": status, "mode": "ttk_e2e", "test_framework": "ttk",
            "passed": passed, "failed": failed, "total": passed + failed,
            "records": rows, "engine_error": engine_error,
            "exit_code": result.exit_code, "stdout": result.stdout,
            "stderr": result.stderr, "duration": time.monotonic() - started,
            "remote_output_dir": remote_dir, "remote_results": remote_results,
            "local_artifact_dir": str(artifact_dir), "results_csv": str(local_results),
            "ttk_command": command,
        }
    except SSHEngineError as exc:
        return {
            "status": "error", "mode": "ttk_e2e", "test_framework": "ttk",
            "passed": 0, "failed": 0, "total": 0, "records": [],
            "engine_error": str(exc), "duration": time.monotonic() - started,
            "remote_output_dir": remote_dir, "local_artifact_dir": str(artifact_dir),
        }
    finally:
        if conn is not None:
            conn.close()
            await conn.wait_closed()


def run_ttk_remote(**kwargs) -> dict[str, Any]:
    return asyncio.run(_run_remote(**kwargs))
