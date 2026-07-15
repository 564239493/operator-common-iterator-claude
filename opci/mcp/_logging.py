"""MCP tool debug logging.

Writes timestamped step-level logs to project_root/.opci/logs/mcp.log.
Each log entry records: tool name, step, parameters, timing.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _log_dir() -> Path:
    """Return the log directory under the current project root."""
    from opci.config import get_project_root
    root = get_project_root()
    log_dir = root / ".opci" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _log_file() -> Path:
    """Return the current log file path (date-based)."""
    return _log_dir() / f"mcp_{time.strftime('%Y%m%d')}.log"


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

    try:
        _log_file().write_text(
            _log_file().read_text(encoding="utf-8") + line,
            encoding="utf-8",
        )
    except FileNotFoundError:
        _log_file().write_text(line, encoding="utf-8")


def log_elapsed(tool: str, step: str, start_time: float, **details: Any) -> None:
    """Log a step with elapsed time from start_time (monotonic)."""
    elapsed = time.monotonic() - start_time
    log(tool, step, elapsed_s=f"{elapsed:.3f}", **details)
