#!/usr/bin/env python3
"""Minimal remote file/command helper over the project's SSH channel.

Usage (project root, .venv interpreter):
    python scripts/remote_file.py run --command "ls -l /path"
    python scripts/remote_file.py get --remote /path/file --local /tmp/file
    python scripts/remote_file.py put --local /tmp/file --remote /path/file

Credentials come from ``servers.json``; secrets are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from executer.ssh import (  # noqa: E402
    ServerEndpoint,
    connect,
    download_file,
    upload_file,
)


def _first_server(config_path: Path, index: int) -> dict:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    servers = payload.get("servers") if isinstance(payload, dict) else None
    if not isinstance(servers, list) or not servers:
        raise SystemExit("servers.json has no servers array")
    return servers[index]


async def _run(args: argparse.Namespace) -> int:
    server = _first_server(Path(args.server_config), args.server_index)
    conn = await connect(ServerEndpoint.from_server_row(server))
    try:
        result = await conn.run(args.command, check=False, timeout=args.timeout)
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        if not str(result.stdout).endswith("\n") and result.stdout:
            sys.stdout.write("\n")
        return int(result.exit_status or 0)
    finally:
        conn.close()


async def _get(args: argparse.Namespace) -> int:
    server = _first_server(Path(args.server_config), args.server_index)
    conn = await connect(ServerEndpoint.from_server_row(server))
    try:
        local = Path(args.local).expanduser()
        local.parent.mkdir(parents=True, exist_ok=True)
        await download_file(conn, args.remote, local, transfer_mode=args.transfer_mode)
        print(json.dumps({"ok": True, "remote": args.remote, "local": str(local)}))
        return 0
    finally:
        conn.close()


async def _put(args: argparse.Namespace) -> int:
    server = _first_server(Path(args.server_config), args.server_index)
    conn = await connect(ServerEndpoint.from_server_row(server))
    try:
        local = Path(args.local).expanduser()
        if not local.is_file():
            raise SystemExit(f"local file not found: {local}")
        await upload_file(conn, local, args.remote, transfer_mode=args.transfer_mode)
        print(json.dumps({"ok": True, "local": str(local), "remote": args.remote}))
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Remote file/command helper")
    parser.add_argument("--server-config", default="servers.json")
    parser.add_argument("--server-index", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--transfer-mode", default="auto",
        choices=("auto", "sftp", "scp", "base64", "shell"),
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--command", required=True)
    p_run.set_defaults(func=_run)

    p_get = sub.add_parser("get")
    p_get.add_argument("--remote", required=True)
    p_get.add_argument("--local", required=True)
    p_get.set_defaults(func=_get)

    p_put = sub.add_parser("put")
    p_put.add_argument("--local", required=True)
    p_put.add_argument("--remote", required=True)
    p_put.set_defaults(func=_put)

    args = parser.parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
