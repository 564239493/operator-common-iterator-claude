#!/usr/bin/env python3
"""Recover an existing remote TTK run directory without rerunning cases."""
from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from executer.ssh import ServerEndpoint, connect, download_file, run


def _load_servers(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    servers = payload.get("servers")
    if not isinstance(servers, list):
        raise ValueError("servers.json: servers must be a list")
    return [server for server in servers if isinstance(server, dict)]


def _candidate_remote_dir(server: dict[str, Any], requested: str) -> str | None:
    if requested.startswith("/"):
        return requested.rstrip("/")
    ttk = server.get("ttk")
    if not isinstance(ttk, dict):
        return None
    remote_root = str(ttk.get("remote_root") or "").rstrip("/")
    return f"{remote_root}/{requested}" if remote_root else None


async def _find_files(conn: Any, remote_dir: str) -> list[tuple[str, int]]:
    command = (
        f"if [ -d {shlex.quote(remote_dir)} ]; then "
        f"find {shlex.quote(remote_dir)} -type f -printf '%p\\t%s\\n'; "
        "else echo __MISSING__; fi"
    )
    result = await run(conn, command, timeout=60.0)
    if result.exit_code != 0:
        return []
    files: list[tuple[str, int]] = []
    for line in result.stdout.splitlines():
        if not line.strip() or line.strip() == "__MISSING__":
            continue
        remote_path, separator, size_text = line.rpartition("\t")
        if not separator:
            continue
        try:
            files.append((remote_path, int(size_text)))
        except ValueError:
            continue
    return files


async def recover(
    servers: list[dict[str, Any]],
    requested_remote_dir: str,
    artifact_dir: Path,
) -> dict[str, Any]:
    attempts: list[dict[str, str]] = []
    for server in servers:
        remote_dir = _candidate_remote_dir(server, requested_remote_dir)
        if remote_dir is None:
            continue
        conn = None
        try:
            endpoint = ServerEndpoint.from_server_row(server)
            conn = await connect(endpoint)
            remote_files = await _find_files(conn, remote_dir)
            if not remote_files:
                attempts.append({
                    "server": str(server.get("name") or "unnamed"),
                    "remote_dir": remote_dir,
                    "status": "missing_or_empty",
                })
                continue

            artifact_dir.mkdir(parents=True, exist_ok=True)
            downloaded: list[str] = []
            failed: list[str] = []
            remote_base = PurePosixPath(remote_dir)
            transfer_mode = str(server.get("transfer_mode") or "auto")
            for remote_file, remote_size in remote_files:
                remote_path = PurePosixPath(remote_file)
                try:
                    relative = remote_path.relative_to(remote_base)
                except ValueError:
                    failed.append(remote_file)
                    continue
                if any(part in ("", ".", "..") for part in relative.parts):
                    failed.append(remote_file)
                    continue
                local_path = artifact_dir.joinpath(*relative.parts)
                if remote_size == 0:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(b"")
                    downloaded.append(relative.as_posix())
                    continue
                await download_file(
                    conn,
                    remote_file,
                    local_path,
                    transfer_mode=transfer_mode,
                )
                if local_path.is_file():
                    downloaded.append(relative.as_posix())
                else:
                    failed.append(relative.as_posix())

            manifest = {
                "status": "success" if not failed else "partial",
                "server": str(server.get("name") or "unnamed"),
                "remote_dir": remote_dir,
                "artifact_dir": str(artifact_dir.resolve()),
                "downloaded_count": len(downloaded),
                "failed_count": len(failed),
                "downloaded_files": downloaded,
                "failed_files": failed,
                "previous_attempts": attempts,
            }
            (artifact_dir / "recovery_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return manifest
        except Exception as exc:
            attempts.append({
                "server": str(server.get("name") or "unnamed"),
                "remote_dir": remote_dir,
                "status": f"connection_or_download_error: {type(exc).__name__}: {exc}",
            })
        finally:
            if conn is not None:
                conn.close()
                await conn.wait_closed()

    return {
        "status": "not_found",
        "requested_remote_dir": requested_remote_dir,
        "artifact_dir": str(artifact_dir.resolve()),
        "attempts": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download an existing TTK remote run directory",
    )
    parser.add_argument(
        "remote_dir",
        help="Remote directory name under each ttk.remote_root, or an absolute path",
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--server-config", type=Path, default=Path("servers.json"))
    args = parser.parse_args()

    servers = _load_servers(args.server_config)
    result = asyncio.run(recover(servers, args.remote_dir, args.artifact_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"success", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
