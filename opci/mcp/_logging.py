"""MCP tool debug logging.

Writes timestamped step-level logs to project_root/logs/mcp/mcp_calls_<date>.log.
Each log entry records: tool name, step, parameters, timing.

Two log categories share the unified ``logs/`` directory:
  - ``logs/mcp/``   — MCP call logs (which tool was called, when, params)
  - ``logs/tools/`` — MCP tool business logs (generator, execution, etc.)

``setup_tool_logging()`` is called during MCP server warmup to attach
FileHandlers to the deterministic Python loggers (executer, runner, ssh)
so their output lands in ``logs/tools/execution.log`` rather than only
stderr or per-run iter directories.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from opci.config import get_project_root


# ---------------------------------------------------------------------------
# MCP call log (tool invocation tracking)
# ---------------------------------------------------------------------------

def _mcp_log_dir() -> Path:
    """Return the MCP call log directory under the unified logs tree.

    Returns ``<project_root>/logs/mcp/``, creating it if necessary.
    """
    root = get_project_root()
    log_dir = root / "logs" / "mcp"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _mcp_log_file() -> Path:
    """Return the current MCP call log file path (date-based)."""
    return _mcp_log_dir() / f"mcp_calls_{time.strftime('%Y%m%d')}.log"


def log(tool: str, step: str, **details: Any) -> None:
    """Write a timestamped log entry.

    Args:
        tool: MCP tool name (e.g. "validate_constraints")
        step: Current step label (e.g. "start", "read_file", "validate", "done")
        **details: Key-value pairs for parameters, paths, sizes, etc.
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    # Truncate large values (e.g. full JSON content) to keep log readable
    safe_details: dict[str, Any] = {}
    for k, v in details.items():
        if isinstance(v, str) and len(v) > 200:
            safe_details[k] = v[:200] + f"...({len(v)} chars total)"
        elif isinstance(v, (list, dict)):
            serialized = json.dumps(v, ensure_ascii=False)
            if len(serialized) > 200:
                safe_details[k] = serialized[:200] + f"...({len(serialized)} chars)"
            else:
                safe_details[k] = v
        else:
            safe_details[k] = v

    detail_str = " | ".join(f"{k}={v}" for k, v in safe_details.items())
    line = f"[{ts}] {tool} :: {step} | {detail_str}\n"

    # Use append mode instead of read+write for efficiency and thread safety
    try:
        with _mcp_log_file().open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        # If the file can't be opened (e.g. disk full), silently skip
        # MCP call logging is diagnostic — never block the tool execution
        pass


def log_elapsed(tool: str, step: str, start_time: float, **details: Any) -> None:
    """Log a step with elapsed time from start_time (monotonic)."""
    elapsed = time.monotonic() - start_time
    log(tool, step, elapsed_s=f"{elapsed:.3f}", **details)


# ---------------------------------------------------------------------------
# MCP tool business log setup (deterministic Python modules)
# ---------------------------------------------------------------------------

# Deterministic Python loggers whose output should be captured to files.
# These are the modules that do real work (SSH, ATK execution, report parsing)
# and use stdlib ``logging.getLogger(__name__)`` to emit diagnostic output.
_TOOL_LOGGERS = [
    "opci.executer.runner",
    "opci.executer.ssh",
    "opci.executer.report_parser",
    "opci.agent.generators.facade",
]


def _tools_log_dir() -> Path:
    """Return the MCP tool business log directory.

    Returns ``<project_root>/logs/tools/``, creating it if necessary.
    """
    root = get_project_root()
    log_dir = root / "logs" / "tools"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_tool_logging() -> None:
    """Attach FileHandlers to deterministic Python loggers.

    Called once during MCP server warmup (``cli._warmup``).  Each logger
    in ``_TOOL_LOGGERS`` gets a date-based FileHandler writing to
    ``logs/tools/execution_<date>.log``.  The handler uses append mode
    and UTF-8 encoding; the per-run ``_setup_execution_log`` handler in
    ``runner.py`` is unaffected (it adds a second handler for the iter dir).
    """
    log_dir = _tools_log_dir()
    date_stamp = time.strftime("%Y%m%d")
    log_path = log_dir / f"execution_{date_stamp}.log"

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(threadName)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    for logger_name in _TOOL_LOGGERS:
        lg = logging.getLogger(logger_name)
        # Avoid adding duplicate handlers if setup_tool_logging is called twice
        if any(
            isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path)
            for h in lg.handlers
        ):
            continue
        try:
            handler = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
            handler.setFormatter(formatter)
            handler.setLevel(logging.DEBUG)
            lg.addHandler(handler)
            lg.setLevel(logging.DEBUG)
            # Prevent propagation to root logger (which defaults to stderr in MCP mode)
            lg.propagate = False
        except OSError:
            # Disk full, permission denied, etc. — skip, don't block server startup
            pass
