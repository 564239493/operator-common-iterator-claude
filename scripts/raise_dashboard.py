#!/usr/bin/env python3
"""Ensure the dashboard server is running and raise the operator-result page.

由 iterate-operator 主协调器在「首轮 CONSTRAINT CHECK 通过后、进 GENERATE 前」以及
「人工修复检查点」调用，把 operator-result.html 拉到用户眼前。幂等：

- 探测 8899 端口；未监听则后台拉起 ``dashboard_server.py``（脱离会话的子进程）并等就绪。
- 读 ``run_state.dashboard_raised``：为 false 时打开浏览器一次并置 true；
  为 true 时不再重复开浏览器，仅确保 server + 返回 URL。
- 人工修复检查点再次调用时，页面已拉起 → 只返回 URL 供主协调器提示用户去网页修改。

用法:
    python scripts/raise_dashboard.py --run-dir <run-dir> --iter <iter_dir>
stdout: JSON {"ok", "url", "server_started", "browser_opened", "dashboard_raised"}
失败（server 起不来）exit 1，stdout 给出手动启动命令提示。
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8899  # 与 scripts/dashboard_server.py 保持一致
SERVER_SCRIPT = ROOT / "scripts" / "dashboard_server.py"
RUN_STATE_SCRIPT = ROOT / "scripts" / "run_state.py"
SERVER_LOG = Path("/tmp") / f"dsh_dashboard_server_{PORT}.log"


def _port_open(host: str = "127.0.0.1", port: int = PORT, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_server() -> tuple[bool, bool]:
    """返回 (started_by_us, server_up)。已监听则不重复启动。"""
    if _port_open():
        return False, True
    log = SERVER_LOG.open("ab")
    subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # 脱离会话 job/session，不被调用方 shell 生命周期杀死
        cwd=str(ROOT),
    )
    # 等就绪（最多 ~6 秒）
    for _ in range(30):
        if _port_open():
            return True, True
        time.sleep(0.2)
    return True, False


def _open_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:
        return False


def _set_raised(run_dir: Path) -> None:
    """经 run_state.py set-fields 持久化 dashboard_raised=true（不经手 run_state.json 直写）。"""
    subprocess.run(
        [sys.executable, str(RUN_STATE_SCRIPT), "set-fields",
         "--run-dir", str(run_dir), "--set", "dashboard_raised=true"],
        check=False,
        capture_output=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="拉起 dashboard 监控页面（幂等）")
    parser.add_argument("--run-dir", required=True, help="当前 run 目录绝对路径")
    parser.add_argument("--iter", required=True, help="iter 目录名（如 iter_001）")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    state_path = run_dir / "run_state.json"
    if not state_path.is_file():
        print(json.dumps({"ok": False, "error": f"run_state.json not found: {state_path}"}, ensure_ascii=False))
        return 1
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(json.dumps({"ok": False, "error": f"run_state.json parse error: {e}"}, ensure_ascii=False))
        return 1
    raised = bool(state.get("dashboard_raised", False))

    started_by_us, server_up = _ensure_server()
    if not server_up:
        print(json.dumps({
            "ok": False,
            "error": "dashboard_server failed to start within timeout",
            "hint": f"手动启动排查: {sys.executable} {SERVER_SCRIPT}  (日志: {SERVER_LOG})",
        }, ensure_ascii=False))
        return 1

    dir_name = run_dir.name
    url = f"http://localhost:{PORT}/?run={dir_name}&iter={args.iter}"

    browser_opened = False
    if not raised:
        browser_opened = _open_browser(url)  # best-effort：无桌面/无浏览器时返回 False
        _set_raised(run_dir)
        raised = True

    print(json.dumps({
        "ok": True,
        "url": url,
        "server_started": started_by_us,   # 本次是否由本脚本启动（已运行则为 false）
        "browser_opened": browser_opened,  # 本次是否打开了浏览器（已拉起则为 false）
        "dashboard_raised": raised,        # 持久化标记：true=已拉起，后续调用不再开浏览器
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
