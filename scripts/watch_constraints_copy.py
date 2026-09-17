#!/usr/bin/env python3
"""监听用户上传的 constraints_copy.json 落盘（单次触发后退出，唤醒空闲会话）。

人工约束上传通道的「挂起」侧：主会话在 DIAGNOSE 后（检查点用户选「人工修复」）
**不自己复制**约束文件，而是以 Monitor 工具（``persistent: true``）挂起本脚本，
等待用户通过 web 页面（或手动）把修改后的约束上传到当前失败轮目录
``<run>/iter_<N>/constraints_copy.json``。

检测语义 = **文件从无到有 + 稳定性校验**：
- 启动时文件应不存在（agent 不创建它）——这是正常的「等待用户上传」初始态。
  此态下文件持续缺席**继续等待，不退出**（退出等价于 Monitor 收尾、会话永不被
  唤醒，违背「没有的就继续等」的设计）；
- 文件出现后进入稳定阶段；连续两次轮询 mtime/size 不变（≈ interval × 2，默认
  10 秒）才判定「上传完成」，向 stdout 输出一行结构化 JSON 后**立即退出**
  （单次触发后退出：只报告一个事件即结束，不重复报告），由 Monitor 把事件送回空闲会话，会话据此提示「已检测到用户
  修改的约束文件，开启下一轮迭代」并运行 ``apply_human_constraints.py``；
- **曾出现后消失**（apply 消费 ``mv`` 走，或用户删除）→ 静默退出（exit 0，无
  事件）；**始终未出现**则永不退出、持续轮询。
- 稳定性校验防部分写入竞态：边写边传的文件在写完前 mtime/size 仍在变，不会
  误触发；写完稳定后才触发。监听器自兜底，不依赖上传侧原子写。

设计纪律（WORKFLOW.md Monitor 用法）：
- 命令单条，无变量/管道/shell 循环；
- 极简纯 poll，无崩溃路径——监听器死亡等价于「不唤醒」，故无 try/except 吞错
  的余地，任何异常都应让 Monitor 看到非零退出；
- 单次触发 + 消费由 ``apply_human_constraints.py`` 的 ``mv`` 完成（移走后文件
  消失，下一轮监听器重新等出现），避免误重触发。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

WATCHED_NAME = "constraints_copy.json"


def _snapshot(path: Path) -> tuple[int, int] | None:
    """返回 (mtime_ns, size)；文件不存在返回 None。"""
    try:
        stat = path.stat()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _iter_dir(run_dir: Path) -> Path | None:
    """从 run_state 读 current_iteration，返回 iter_<NNN> 目录路径。

    run_state 不可读时返回 None（监听器仍可跑，但无法定位 iter 目录 → 报错退出）。
    """
    run_state_path = run_dir / "run_state.json"
    try:
        state = json.loads(run_state_path.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            n = int(state.get("current_iteration", 0) or 0)
            if n >= 1:
                return run_dir / f"iter_{n:03d}"
    except (OSError, ValueError, TypeError):
        pass
    return None


def _read_meta(run_dir: Path) -> tuple[str, int]:
    run_state_path = run_dir / "run_state.json"
    try:
        state = json.loads(run_state_path.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            return (
                str(state.get("run_id", "")),
                int(state.get("current_iteration", 0) or 0),
            )
    except (OSError, ValueError, TypeError):
        pass
    return ("", 0)


def watch(run_dir: Path, interval: float) -> int:
    run_id, iteration = _read_meta(run_dir)
    if iteration < 1:
        # 无法定位当前轮次 → 静默退出交会话处理。
        return 0
    iter_dir = run_dir / f"iter_{iteration:03d}"
    if not iter_dir.is_dir():
        # iter 目录不存在 → 静默退出。
        return 0
    watched = iter_dir / WATCHED_NAME

    # 启动时文件应不存在（agent 不创建）——这是正常的「等待上传」初始态。
    # prev=None 表示「尚未出现过」：此态下文件持续缺席应继续等待，不得退出
    prev = _snapshot(watched)
    while True:
        time.sleep(interval)
        cur = _snapshot(watched)
        if cur is None:
            if prev is None:
                # 始终缺席（尚未上传）→ 继续等待。
                continue
            # 曾出现过，但是当前消失→ 静默退出。
            return 0
        if prev is None:
            # 文件刚出现：进入"等稳定"阶段，记录首次快照，下一轮再比。
            prev = cur
            continue
        if cur == prev:
            # 连续两次（含本次）不变 → 稳定
            event = {
                "run_id": run_id,
                "iteration": iteration,
                "file": WATCHED_NAME,
                "appeared_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            }
            print(json.dumps(event, ensure_ascii=False), flush=True)
            return 0
        # 仍在变化（部分写入中）→ 更新快照，继续等稳定。
        prev = cur


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "监听 <run>/iter_<N>/constraints_copy.json 落盘（单次触发后退出 + 稳定性校验）。"
            "供 Monitor 工具挂起：检测到用户上传完成即输出一行 JSON 并退出，唤醒空闲会话。"
        )
    )
    parser.add_argument("--run-dir", required=True, help="run 目录绝对路径")
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="轮询间隔秒（默认 5；稳定性需约 interval×2）",
    )
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        print(json.dumps(
            {"ok": False, "code": "RUN_DIR_NOT_FOUND", "run_dir": str(run_dir)},
            ensure_ascii=False,
        ))
        return 2
    if args.interval <= 0:
        print(json.dumps(
            {"ok": False, "code": "INVALID_INTERVAL", "interval": args.interval},
            ensure_ascii=False,
        ))
        return 2
    return watch(run_dir, args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
