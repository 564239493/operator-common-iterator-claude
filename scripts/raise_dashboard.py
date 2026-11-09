#!/usr/bin/env python3
"""Ensure the workbench server is running and raise the visualization page.

由 iterate-operator 主协调器在「首轮 CONSTRAINT CHECK 通过后、进 GENERATE 前」以及
「人工修复检查点」调用，把工作台页面拉到用户眼前。幂等：

- 探测 8420 端口；未监听则后台拉起 ``src/serve.py``（脱离会话的子进程）并等就绪。
  ``src/serve.py`` 托管已构建的 ``src/web/dist``（Vue3 工作台），未构建时仅 API 可用、
  根路径返回「前端尚未构建」占位页——人工修复编辑前必须先构建前端。
- 读 ``run_state.dashboard_raised``：为 false 且带 ``--web`` 时打开浏览器一次并置 true；
  不带 ``--web`` 时只确保 server + 返回 URL，**不自动打开浏览器**（默认）；
  为 true 时不再重复开浏览器，仅确保 server + 返回 URL。
- 人工修复检查点再次调用时（需实际弹出网页），加 ``--web``；页面已拉起则只返回 URL。

用法:
    python scripts/raise_dashboard.py --run-dir <run-dir> --iter <iter_dir> [--page run|constraints] [--web]
stdout: JSON {"ok", "url", "server_started", "browser_opened", "dashboard_raised", "dist_built"}
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
PORT = 8420  # 与 src/serve.py、src/workbench/config.py 保持一致
SERVER_SCRIPT = ROOT / "src" / "serve.py"
RUN_STATE_SCRIPT = ROOT / "scripts" / "run_state.py"
DIST_DIR = ROOT / "src" / "web" / "dist"
SERVER_LOG = ROOT / "logs" / f"dsh_workbench_server_{PORT}.log"


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
    SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
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


def _build_url(page: str, dir_name: str, iter_dir: str) -> str:
    """工作台为 hash 路由：查询参数位于 # 之后，由 vue-router 解析为一次性深链引导。"""
    route = "/constraints" if page == "constraints" else "/run"
    return f"http://localhost:{PORT}/#{route}?run={dir_name}&iter={iter_dir}"


def main() -> int:
    parser = argparse.ArgumentParser(description="拉起工作台可视化页面（幂等）")
    parser.add_argument("--run-dir", required=True, help="当前 run 目录绝对路径")
    parser.add_argument("--iter", required=True, help="iter 目录名（如 iter_001）")
    parser.add_argument(
        "--page", choices=["run", "constraints"], default="run",
        help="拉起的页面：run=工作台监控（默认）；constraints=约束审核（人工修复编辑用）",
    )
    parser.add_argument(
        "--web", action="store_true",
        help="打开浏览器拉起页面；缺省仅确保 server 就绪并返回 URL，不自动打开浏览器",
    )
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
            "error": "workbench server failed to start within timeout",
            "hint": f"手动启动排查: {sys.executable} {SERVER_SCRIPT}  (日志: {SERVER_LOG})",
        }, ensure_ascii=False))
        return 1

    dist_built = DIST_DIR.is_dir() and (DIST_DIR / "index.html").is_file()
    dir_name = run_dir.name
    url = _build_url(args.page, dir_name, args.iter)

    browser_opened = False
    if args.web and not raised:
        browser_opened = _open_browser(url)  # best-effort：无桌面/无浏览器时返回 False
        _set_raised(run_dir)
        raised = True

    result = {
        "ok": True,
        "url": url,
        "server_started": started_by_us,   # 本次是否由本脚本启动（已运行则为 false）
        "browser_opened": browser_opened,  # 本次是否打开了浏览器（已拉起则为 false）
        "dashboard_raised": raised,        # 持久化标记：true=已拉起，后续调用不再开浏览器
        "dist_built": dist_built,          # 前端是否已构建；false 时页面为占位页，人工编辑前需先构建
    }
    if not dist_built:
        result["build_hint"] = (
            "cd src/web && npm install --registry=https://registry.npmmirror.com && npm run build"
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())