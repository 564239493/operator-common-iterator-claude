#!/usr/bin/env python3
"""Quick atk option probe: connect, run `atk node --help`, disconnect.

Confirms which dist-related options the remote atk version actually
supports (--dist_backend vs --dist-backend vs --backend). Output is
strictly for failure-analyst evidence — not a case run.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from executer.ssh import ServerEndpoint, connect, run, SSHEngineError  # noqa: E402


async def main() -> int:
    config_path = ROOT / "servers.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    servers = payload.get("servers")
    if not isinstance(servers, list) or not servers:
        print("SSH_NO_SERVERS")
        return 2

    endpoint = ServerEndpoint.from_server_row(servers[0])
    try:
        conn = await connect(endpoint, timeout=30.0)
    except SSHEngineError as exc:
        print(f"SSH_CONNECT_FAILED: {exc}")
        return 1

    # Match the env_init chain the runner uses, then ask atk for help.
    cmd = (
        "conda activate op_test && cd /data/atk-space/runs && "
        "source /usr/local/Ascend/cann-9.0.0/set_env.sh && "
        "export TORCH_DEVICE_BACKEND_AUTOLOAD=0 && "
        "echo '===WHICH_ATK===' && which atk && "
        "echo '===TYPE_ATK===' && type atk && "
        "echo '===REPRO_DIST_BACKEND===' && "
        "atk node --backend dist --name cpu --devices 0,1 --dist_backend gloo 2>&1 | head -40"
    )
    try:
        result = await run(conn, cmd, timeout=180.0)
    except SSHEngineError as exc:
        print(f"ATK_HELP_RUN_FAILED: {exc}")
        conn.close()
        return 2

    print(f"EXIT_CODE={result.exit_code}")
    print("---STDOUT---")
    print(result.stdout)
    print("---STDERR---")
    print(result.stderr)
    conn.close()
    try:
        await conn.wait_closed()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
