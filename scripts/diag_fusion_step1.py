"""诊断 fusion step① cpu_benchmark：复用 SSH 通道，原样重跑 execution_result.json
里记录的 atk 命令，``2>&1`` 合并 + 显式打 shell ``$?``，把远端原始输出抓回来。

背景：runner 的 ``run()`` 用 ``conn.run(check=False)`` 拿 asyncssh completed，当 atk
进程被信号杀时 ``exit_status=None`` (映射 -1) 且 stdout/stderr 全空，根因无法坐实
(见 memory fusion-ct-tool-missing-blocker, 07-23 形态)。本脚本绕过 runner 的 fusion
流程，直接抓 atk 的原始 stderr / shell 退出码。

用法（项目根目录，激活 .venv 后）::

    PYTHONUTF8=1 python scripts/diag_fusion_step1.py \\
        --run-id aclnnAlltoAllMatmul-20260723-162623-857753 \\
        --iter iter_001 --phase cpu_benchmark

不重新 upload、不跑 4 步 fusion、不做 dir_check；不输出 servers.json 秘密。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from executer.ssh import ServerEndpoint, connect  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--iter", default="iter_001")
    ap.add_argument("--phase", default="cpu_benchmark")
    ap.add_argument("--server-config", default="servers.json")
    ap.add_argument("--no-probe", action="store_true", help="跳过 which atk / set_env.sh 探测")
    ap.add_argument("--timeout", type=float, default=1800.0)
    args = ap.parse_args()

    iter_dir = ROOT / "runs" / args.run_id / args.iter
    er_path = iter_dir / "execution_result.json"
    if not er_path.is_file():
        print(f"找不到 {er_path}", file=sys.stderr)
        return 2
    er = json.loads(er_path.read_text(encoding="utf-8"))
    phases = er.get("fusion_phases") or []
    target = next((p for p in phases if p.get("phase") == args.phase), None)
    if not target:
        print(f"未在 execution_result.json 找到 phase={args.phase}", file=sys.stderr)
        return 2
    atk_cmd = target["command"]
    print(f"=== phase: {args.phase} ===")
    print(f"=== 记录值 exit_code={target.get('exit_code')} "
          f"duration={target.get('duration'):.2f}s dir_check_passed={target.get('dir_check_passed')} ===")
    print(f"=== atk command ===\n{atk_cmd}\n")

    cfg = json.loads((ROOT / args.server_config).read_text(encoding="utf-8"))
    servers = cfg.get("servers") or []
    if not servers:
        print("servers.json 无 servers 数组", file=sys.stderr)
        return 2
    server = servers[0]
    endpoint = ServerEndpoint.from_server_row(server)

    conn = await connect(endpoint)
    print("=== SSH 已连接 (host/凭据隐去) ===\n")

    if not args.no_probe:
        probe = (
            "which atk; "
            "echo SETENV:$(test -f /usr/local/Ascend/cann-9.0.0/set_env.sh && echo OK || echo MISSING); "
            "echo CANNLS:$(ls /usr/local/Ascend/ 2>/dev/null | tr '\\n' ' '); "
            "atk --version 2>&1 | head -3; "
            "echo PROBE_DONE:$?"
        )
        try:
            r = await conn.run(probe, check=False, timeout=60)
            print("=== probe (which atk / set_env.sh / cann 目录) ===")
            print(f"exit_status={r.exit_status}")
            print(((r.stdout or "") + (r.stderr or "")).strip())
            print()
        except Exception as exc:
            print(f"=== probe 异常: {exc} ===\n")

    # 用 { group ; } 2>&1 把整条命令 (cd && source && export && atk) 的 stderr 全合并进
    # stdout; 末尾显式打 shell 看到的 $? —— atk 被信号杀时 $? = 128+sig (137=SIGKILL,
    # 139=SIGSEGV, 127=not found)。若 asyncssh channel 整个异常, echo 也跑不到,
    # completed.exit_status 会是 None。
    full = f"{{ {atk_cmd} ; }} 2>&1; printf '\\n__EXIT__:%s\\n' \"$?\""
    print("=== 原样重跑 atk (2>&1 合并, 最长打印尾部 12000 字符) ===")
    try:
        r = await conn.run(full, check=False, timeout=args.timeout)
    except Exception as exc:
        print(f"=== conn.run 异常: {type(exc).__name__}: {exc} ===")
        conn.close()
        return 3
    print(f"exit_status={r.exit_status}")
    out = (r.stdout or "") + (r.stderr or "")
    print(out[-12000:] if len(out) > 12000 else out)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
