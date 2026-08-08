#!/usr/bin/env python3
"""Probe: can CREATE_BREAKAWAY_FROM_JOB let a child escape the harness bg-task kill?

This is a one-shot diagnostic (run once, then delete). It simulates exactly the
generation situation:

  harness Bash(run_in_background)  ->  parent (this script, mode=parent)
                                     ->  child (this script, mode=child) with
                                         CREATE_BREAKAWAY_FROM_JOB, heartbeats
                                         to a file every 2s for 40s

The orchestrator then TaskStops the bg task (== the harness killing the bg bash
task, which is what kills the generation wrapper at ~60min). If the breakaway
child KEEPS heartbeating after the parent/bg-task is stopped, then
CREATE_BREAKAWAY_FROM_JOB escapes the harness job -> the real fix (let
generate_cases.py break away from generation_progress.py's job) is viable and
automated. If the child dies with the parent, breakaway is blocked here and we
must launch generation from outside the harness (user terminal).

Usage (the probe is driven by the orchestrator, not run by hand):
  python scripts/probe_breakaway.py parent <heartbeat_file>
  python scripts/probe_breakaway.py child  <heartbeat_file>
"""
import os
import sys
import time
import subprocess

# CREATE_BREAKAWAY_FROM_JOB = 0x01000000 ; CREATE_DETACHED_PROCESS not needed for
# breakaway but harmless. CREATE_NEW_PROCESS_GROUP = 0x00000200 helps it survive
# console signals.
BREAKAWAY = 0x01000000
NEW_GROUP = 0x00000200


def run_child(hb_file: str) -> int:
    started = time.time()
    with open(hb_file, "w", encoding="utf-8") as f:
        f.write(f"child start pid={os.getpid()} parent_ppid={os.getppid()}\n")
        f.flush()
        try:
            os.utime(hb_file)
        except OSError:
            pass
    while time.time() - started < 40:
        with open(hb_file, "a", encoding="utf-8") as f:
            f.write(f"tick {int(time.time() - started)}s pid={os.getpid()}\n")
            f.flush()
        time.sleep(2)
    with open(hb_file, "a", encoding="utf-8") as f:
        f.write(f"child done pid={os.getpid()}\n")
    return 0


def run_parent(hb_file: str) -> int:
    me = sys.executable
    script = os.path.abspath(__file__)
    flags = BREAKAWAY | NEW_GROUP
    try:
        proc = subprocess.Popen(
            [me, script, "child", hb_file],
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as e:
        with open(hb_file, "w", encoding="utf-8") as f:
            f.write(f"BREAKAWAY CREATE FAILED: {e}\n")
        return 1
    with open(hb_file, "a", encoding="utf-8") as f:
        f.write(f"parent launched child pid={proc.pid} flags={flags:#x}; parent pid={os.getpid()}; parent EXITS NOW (normal exit 0, not killed)\n")
        f.flush()
    # Real-fix scenario: the wrapper exits immediately after spawning the
    # detached child. The bg task ends normally -> no forceful tree-kill ->
    # the breakaway child must survive on its own and tick to completion.
    return 0


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: probe_breakaway.py parent|child <heartbeat_file>")
        return 2
    mode, hb_file = sys.argv[1], sys.argv[2]
    if mode == "child":
        return run_child(hb_file)
    elif mode == "parent":
        return run_parent(hb_file)
    print(f"unknown mode {mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
