#!/usr/bin/env python3
"""Detached launcher + status poller for ``scripts/generate_cases.py``.

Why this exists
---------------
Long generations (500 cases x multi-platform Z3, ~hours) used to be launched by
the case-generator sub-Agent as a single background bash running this wrapper's
*old* design: the wrapper spawned ``generate_cases.py`` and then sat in a
``proc.poll()`` loop for hours writing ``generation_progress.json``. That made
the **wrapper itself** a long-lived harness background bash task — and a
background bash task is bound to its owning Claude Code session's lifetime. When
the session is interrupted / restarted / context-compacted (NOT a fixed 60-min
cap — there is no such cap), the harness kills the background task and its whole
process tree, taking ``generate_cases.py`` with it. The frozen
``generation_progress.json`` (``state=running, pid_alive=true``, never
finalized) and the console log stopping mid-case are the signature. A process
launched from *outside* the session (a plain terminal) survives and completes.

This module fixes it by **never hosting a long-lived background task**. It has
two subcommands:

``launch`` (run once by the case-generator sub-Agent as a *foreground* Bash —
it returns in ~1 second):
  1. Spawn ``generate_cases.py`` as a subprocess that is **detached** from the
     launcher's job/session:
       - Windows: ``creationflags = CREATE_BREAKAWAY_FROM_JOB |
         CREATE_NEW_PROCESS_GROUP`` — the child leaves the launcher's Job
         Object so a session/job kill cannot reach it.
       - POSIX: ``start_new_session=True`` (setsid) — the child becomes a
         session leader in its own session/process-group.
  2. Redirect the child's stdout+stderr to ``<output-dir>/generation_console.log``
     (binary) so verbose per-case logs land on disk, never in any captured
     stream.
  3. Write a tiny ``<output-dir>/generation_progress.json`` marker carrying
     ``pid``, ``started_epoch``, ``requested``, ``console_log``, state=running.
  4. Print **one** compact JSON line (the marker) to stdout and **exit 0
     immediately**. There is now no long-lived process for the harness to kill
     — the only long-lived process is the detached ``generate_cases.py`` child,
     which the probe (``scripts/probe_breakaway.py``) proved survives launcher
     exit.

``status`` (run by the orchestrator every ~60s as a *foreground* Bash — also
returns in ~1 second):
  1. Read the launch marker from ``generation_progress.json`` (pid, started).
  2. Compute per-platform live progress from the per-case-flushed JSONL
     checkpoints (line counts) + finished ``cases_<platform>.json`` files.
  3. Check child pid liveness (advisory; ``os.kill(pid, 0)``).
  4. Treat ``generation_summary.json`` existence as the authoritative completion
     signal (generate_cases.py writes it on success right before exit) and
     ``generation_status.json`` ``state=failed`` as the authoritative failure
     signal.
  5. Rewrite ``generation_progress.json`` with current state and print one JSON
     line. The orchestrator decides done/keep-polling/failed from this line;
     it never reads the verbose console log or the multi-MB cases files.

Interface
---------

::

    # case-generator sub-Agent (foreground, ~1s):
    python scripts/generation_progress.py launch --output-dir <dir> \\
        [--console-log <path>] -- \\
        --constraints <constraints.json> --output <cases.json> \\
        --count <N> --test-framework atk

    # orchestrator (foreground, ~1s, repeat every 60s):
    python scripts/generation_progress.py status --output-dir <dir>

``--output-dir`` is the artifact root (parent of ``generate_cases.py``'s
``--output``). Everything after ``--`` (launch mode only) is forwarded verbatim
to ``generate_cases.py``; this module supplies the interpreter (its own
``sys.executable``, the same venv python) and the absolute ``generate_cases.py``
path, so the pass-through must be the **generate_cases.py arguments only** (not
``python scripts/generate_cases.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GENERATE_CASES = ROOT / "scripts" / "generate_cases.py"

_IS_WINDOWS = sys.platform == "win32"
# Windows CreateProcess flags: break the child out of the launcher's Job Object
# (so a session/job kill cannot reach it) and put it in a new process group
# (so console Ctrl-C / signal events aimed at the launcher's group do not hit it).
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200

# Markers that announce a generation failure in the console log. The status
# poller extracts a bounded excerpt around the first match so the orchestrator
# gets the cause without reading the verbose file itself.
_ERROR_MARKERS = (
    "Traceback (most recent call last)",
    "ZERO_CASES_GENERATED",
    "HS_SEMANTIC_GATE_FAILED",
    "HS_SCENARIO_GENERATION_INCOMPLETE",
    "RuntimeError:",
    "ValueError:",
    "SystemExit:",
    "OSError:",
)
_MAX_ERROR_LINES = 20


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _emit(payload: dict[str, Any]) -> None:
    """Print exactly one compact JSON line -- the sole stdout content.

    Writes raw UTF-8 bytes to fd 1 so it never trips the Windows console
    codec (cp936/GBK) when the payload carries non-ASCII platform names or
    traceback excerpts.
    """
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except AttributeError:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _extract_arg(argv: list[str], name: str) -> str | None:
    """Return the value of ``name`` in a pass-through argv (``--name X`` / ``--name=X``)."""
    for i, tok in enumerate(argv):
        if tok == name and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith(name + "="):
            return tok[len(name) + 1:]
    return None


def _resolve_output_dir(argv: list[str], explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_absolute() else (ROOT / p).resolve()
    output = _extract_arg(argv, "--output")
    if not output:
        raise SystemExit(
            "generation_progress: --output-dir is required (or pass "
            "--output <path> to generate_cases.py after --)"
        )
    p = Path(output).expanduser()
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p.parent


def _progress_path(output_dir: Path) -> Path:
    return output_dir / "generation_progress.json"


def _read_progress(output_dir: Path) -> dict[str, Any]:
    path = _progress_path(output_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _count_jsonl_cases(path: Path) -> int:
    """Count non-empty JSONL lines (proxy for completed cases; +/-1 on a truncated tail)."""
    if not path.is_file():
        return 0
    n = 0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
    except OSError:
        pass
    return n


def _per_platform_progress(output_dir: Path, requested: int | None) -> dict[str, dict[str, Any]]:
    """Per-platform live progress (advisory, READ-ONLY -- never intrudes on generation).

    A platform that has finished has its JSONL checkpoint deleted by
    ``convert_jsonl_to_json``. Without surfacing finished platforms, a completed
    platform would *disappear* from progress, so the inter-platform gap
    (A done, B not yet started) would look like "nothing left / finalized" --
    tempting the supervisor to kill a still-running multi-platform job. So a
    finished platform is reported from its persisted ``cases_<platform>.json``
    (file-existence only -- no multi-MB re-parse each tick) with
    ``status="complete"``; an in-progress platform from its JSONL line count
    with ``status="running"``. These counts are advisory; the authoritative
    final counts come from the status poller's summary check.
    """
    root = output_dir / "jsonl_checkpoints"
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    if root.is_dir():
        for platform_dir in sorted(root.iterdir()):
            if not platform_dir.is_dir():
                continue
            plat = platform_dir.name
            seen.add(plat)
            jsonl_files = list(platform_dir.glob("*.jsonl"))
            cases_file = output_dir / f"cases_{plat}.json"
            if jsonl_files:
                entry: dict[str, Any] = {
                    "done": _count_jsonl_cases(jsonl_files[0]), "status": "running",
                }
            elif cases_file.is_file():
                entry = {"done": requested, "status": "complete"}
            else:
                entry = {"done": 0, "status": "running"}
            if requested is not None:
                entry["requested"] = requested
            result[plat] = entry
    for cases_file in output_dir.glob("cases_*.json"):
        plat = cases_file.stem[len("cases_"):]
        if plat in seen or plat in result:
            continue
        entry = {"done": requested, "status": "complete"}
        if requested is not None:
            entry["requested"] = requested
        result[plat] = entry
    return result


def _read_status_file(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "generation_status.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _pid_alive_windows(pid: int) -> bool:
    """Correct Windows pid liveness via OpenProcess + GetExitCodeProcess.

    ``os.kill(pid, 0)`` is NOT an existence probe on Windows: signal 0 is
    ``CTRL_C_EVENT`` there, so it tries ``GenerateConsoleCtrlEvent`` against
    the target's process group -- which fails (OSError) for a detached,
    console-less breakaway child (``stdin=DEVNULL``, ``stdout=file``,
    ``CREATE_NEW_PROCESS_GROUP``), producing a false "dead" verdict. Use the
    Win32 API directly instead.
    """
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        STILL_ACTIVE = 259
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False  # ERROR_INVALID_PARAMETER (87) -> no such process
        try:
            code = wintypes.DWORD()
            k32.GetExitCodeProcess.argtypes = (
                wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
            )
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return False


def _pid_alive(pid: int | None) -> bool:
    """Advisory pid liveness across platforms.

    Authoritative completion is ``generation_summary.json`` existence, not this
    check (pids can be recycled). POSIX ``os.kill(pid, 0)`` is a genuine
    existence probe; Windows is dispatched to ``_pid_alive_windows`` because
    ``os.kill(pid, 0)`` == ``CTRL_C_EVENT`` there (see that function's docstring).
    """
    if not pid or pid < 0:
        return False
    if _IS_WINDOWS:
        return _pid_alive_windows(int(pid))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # exists but not owned by us -- treat as alive (advisory only)
        return True
    except OSError:
        return False
    return True


def _console_recently_written(
    console_log: Path | None, now_epoch: float, threshold: float = 90.0,
) -> bool:
    """True if the console log was modified within ``threshold`` seconds.

    The generator flushes per case (``ThreadSafeLogger`` + per-case JSONL), so
    an active generation keeps the console log's mtime fresh. This is the most
    robust "still running" signal -- it does not depend on pid-liveness (which
    can misfire on Windows breakaway children) nor on the launcher recording
    the exact worker pid. A stalled/crashed generation stops writing, mtime
    goes stale, and ``status`` can then declare failure without a summary.
    """
    if console_log is None or not console_log.is_file():
        return False
    try:
        return (now_epoch - console_log.stat().st_mtime) <= threshold
    except OSError:
        return False


def _bounded_error(console_log: Path) -> str | None:
    """Extract a bounded (<=_MAX_ERROR_LINES) excerpt carrying the failure cause."""
    if not console_log.is_file():
        return None
    try:
        text = console_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines:
        return None
    first_idx = None
    for i, line in enumerate(lines):
        if any(marker in line for marker in _ERROR_MARKERS):
            first_idx = i
            break
    excerpt: list[str] = []
    if first_idx is not None:
        excerpt = lines[first_idx:first_idx + _MAX_ERROR_LINES]
    else:
        excerpt = lines[-_MAX_ERROR_LINES:]
    excerpt = excerpt or lines[-_MAX_ERROR_LINES:]
    return "\n".join(excerpt).strip() or None


def _final_counts(output_dir: Path) -> dict[str, int]:
    """Per-platform case counts, sourced from persisted cases files (+JSONL fallback)."""
    counts: dict[str, int] = {}
    for p in output_dir.glob("cases_*.json"):
        plat = p.stem[len("cases_"):]
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            counts[plat] = len(data) if isinstance(data, list) else 0
        except (OSError, json.JSONDecodeError):
            counts[plat] = 0
    jc = output_dir / "jsonl_checkpoints"
    if jc.is_dir():
        for platform_dir in jc.iterdir():
            if not platform_dir.is_dir() or platform_dir.name in counts:
                continue
            for jf in platform_dir.glob("*.jsonl"):
                counts[platform_dir.name] = _count_jsonl_cases(jf)
                break
    return counts


def _write_progress(output_dir: Path, payload: dict[str, Any]) -> None:
    try:
        _progress_path(output_dir).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------

def cmd_launch(
    output_dir: Path, console_log: Path, passthrough: list[str],
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    console_log.parent.mkdir(parents=True, exist_ok=True)

    requested_raw = _extract_arg(passthrough, "--count")
    requested = int(requested_raw) if requested_raw and requested_raw.isdigit() else None

    cmd = [sys.executable, str(GENERATE_CASES), *passthrough]
    # Detach the child from this launcher's job/session so a harness session/job
    # kill cannot reach it. stdin=DEVNULL so the child never blocks on a tty
    # owned by the (soon-dead) launcher. close_fds keeps no inherited handles.
    creationflags = 0
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": None,  # set below
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if _IS_WINDOWS:
        creationflags = _CREATE_BREAKAWAY_FROM_JOB | _CREATE_NEW_PROCESS_GROUP
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    console_fp = console_log.open("wb")
    popen_kwargs["stdout"] = console_fp
    breakaway_note = "ok"
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except OSError as exc:
        # CREATE_BREAKAWAY_FROM_JOB can fail with "Access is denied" (error 5)
        # if the launcher's job forbids breakaway. Fall back to a plain spawn
        # (still immediate-exit; the child just is not out of the job) and
        # record the fallback so status/the orchestrator knows breakaway did
        # not engage.
        console_fp.close()
        popen_kwargs.pop("creationflags", None)
        popen_kwargs.pop("start_new_session", None)
        creationflags = 0
        breakaway_note = f"fallback (breakaway failed: {exc})"
        console_fp = console_log.open("wb")
        popen_kwargs["stdout"] = console_fp
        proc = subprocess.Popen(cmd, **popen_kwargs)
    # The child is detached; closing our handle does NOT kill it. We must not
    # hold the console file handle open in a long-lived launcher -- but we are
    # about to exit immediately, so close it now. The detached child holds its
    # own dup'd stdout/stderr fd.
    console_fp.close()

    started_epoch = time.time()
    marker = {
        "state": "running",
        "pid": proc.pid,
        "started_epoch": started_epoch,
        "requested": requested,
        "console_log": str(console_log),
        "breakaway": breakaway_note,
        "platform": "windows" if _IS_WINDOWS else "posix",
        "creationflags": creationflags,
        "elapsed_seconds": 0.0,
        "pid_alive": True,
        "per_platform": {},
    }
    _write_progress(output_dir, marker)
    # One line out, then exit 0 immediately. No long-lived process remains.
    _emit(marker)
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(output_dir: Path) -> int:
    prev = _read_progress(output_dir)
    pid = prev.get("pid")
    started = prev.get("started_epoch")
    requested = prev.get("requested")
    console_log = (
        Path(prev["console_log"]) if prev.get("console_log")
        else output_dir / "generation_console.log"
    )

    alive = _pid_alive(pid) if isinstance(pid, int) else False
    now = time.time()
    elapsed = round(now - float(started), 1) if isinstance(started, (int, float)) else 0.0
    per_platform = _per_platform_progress(output_dir, requested if isinstance(requested, int) else None)
    summary_exists = (output_dir / "generation_summary.json").is_file()
    status_file = _read_status_file(output_dir)
    # console.log mtime is the most robust "still running" signal; pid_alive
    # is secondary (it can misfire on Windows breakaway children).
    console_recent = _console_recently_written(console_log, now, 90.0)

    if status_file and status_file.get("state") == "failed":
        state = "failed"
        error = (
            status_file.get("message")
            or (_bounded_error(console_log) if console_log else None)
            or "generation failed"
        )
    elif summary_exists:
        state = "complete"
        error = None
    elif console_recent or alive:
        state = "running"
        error = None
    else:
        # no summary, no explicit failure, console stale AND pid dead -> stalled/crashed
        state = "failed"
        error = (
            (_bounded_error(console_log) if console_log else None)
            or "generate_cases.py stalled: console log stale >90s, pid dead, "
               "no generation_summary.json"
        )

    final_counts = _final_counts(output_dir) if state in ("complete", "failed") else {}
    total = sum(int(v) for v in final_counts.values()) if final_counts else sum(
        int(v.get("done", 0)) for v in per_platform.values()
    )

    payload: dict[str, Any] = {
        "state": state,
        "pid": pid,
        "started_epoch": started,
        "requested": requested,
        "console_log": str(console_log) if console_log else None,
        "elapsed_seconds": elapsed,
        "pid_alive": alive,
        "per_platform": per_platform if state == "running" else {
            k: {"done": v, "status": state} for k, v in final_counts.items()
        },
        "total": total,
    }
    if error:
        payload["error"] = error
    _write_progress(output_dir, payload)
    _emit(payload)
    return 0 if state == "running" else 0  # status always exits 0; state carries verdict


# ---------------------------------------------------------------------------
# arg parsing
# ---------------------------------------------------------------------------

def _parse_launch(argv: list[str]) -> tuple[Path, Path, list[str]]:
    if "--" not in argv:
        raise SystemExit(
            "generation_progress launch: pass '--' followed by the "
            "generate_cases.py argv, e.g. --output-dir <dir> -- "
            "--constraints ... --output ... --count N"
        )
    sep = argv.index("--")
    own = argv[:sep]
    passthrough = argv[sep + 1:]
    parser = argparse.ArgumentParser(prog="generation_progress launch", add_help=False)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--console-log", default=None)
    flags, _ = parser.parse_known_args(own)
    if not passthrough:
        raise SystemExit("generation_progress launch: nothing after '--' to run")
    output_dir = _resolve_output_dir(passthrough, flags.output_dir)
    if not output_dir.is_absolute():
        output_dir = (ROOT / output_dir).resolve()
    console_log = (
        Path(flags.console_log).expanduser() if flags.console_log
        else output_dir / "generation_console.log"
    )
    if not console_log.is_absolute():
        console_log = (ROOT / console_log).resolve()
    return output_dir, console_log, passthrough


def _parse_status(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(prog="generation_progress status", add_help=False)
    parser.add_argument("--output-dir", required=True)
    flags, _ = parser.parse_known_args(argv)
    p = Path(flags.output_dir).expanduser()
    return p if p.is_absolute() else (ROOT / p).resolve()


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit(
            "usage: generation_progress launch|status ...\n"
            "  launch --output-dir <dir> [--console-log <p>] -- <gen_cases_args>\n"
            "  status --output-dir <dir>"
        )
    sub = argv[0]
    rest = argv[1:]
    try:
        if sub == "launch":
            output_dir, console_log, passthrough = _parse_launch(rest)
            return cmd_launch(output_dir, console_log, passthrough)
        if sub == "status":
            output_dir = _parse_status(rest)
            return cmd_status(output_dir)
    except BaseException as exc:  # noqa: BLE001 -- must still emit one line
        _emit({
            "state": "failed",
            "error": f"generation_progress {sub}: {type(exc).__name__}: {exc}",
            "per_platform": {},
            "total": 0,
        })
        return 1
    raise SystemExit(
        f"unknown subcommand '{sub}' (expected 'launch' or 'status')"
    )


if __name__ == "__main__":
    raise SystemExit(main())
