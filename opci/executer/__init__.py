"""Project-local ATK execution engine.

This package replaces the cross-project dependency on
``operator-agent/packages/agent/src/agent.nodes.executer_subgraph`` that the
previous ``scripts/execute_cases.py`` reached into.  It mirrors the reference
implementation under ``D:\\operator_project\\operator-common-iterator\\executer``
but stays inside this project:

* no ``langchain_openai.ChatOpenAI`` step (CLI is the LLM, not Python);
* no ``.env`` / Pydantic ``Settings`` validation against external projects;
* no ``agent.nodes.*`` / ``agent.runtime.*`` imports;
* all SSH, ATK invocation, and xlsx parsing logic lives here.

Public surface (used by :mod:`scripts.execute_cases`):

* :func:`runner.run_cases` — drives mock or real mode, returns a flat
  ``execution_result.json``-shaped dict.
* :class:`models.ExecutionResult` — canonical return shape.
"""

from __future__ import annotations

from .models import ExecutionResult, ReportRecord, TaskReportData
from .runner import run_cases

__all__ = [
    "ExecutionResult",
    "ReportRecord",
    "TaskReportData",
    "run_cases",
]
