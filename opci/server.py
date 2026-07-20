"""opci MCP Server - fastmcp stdio mode.

All 22 tools are registered here using @mcp.tool() decorators.
The server is launched by Claude Code via the `opci mcp-server` CLI command.
"""

from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP("opci")

# ---------------------------------------------------------------------------
# Register all tools from each module
# ---------------------------------------------------------------------------

from opci.mcp.tools.run_management import (
    init_run,
    update_run_state,
    find_latest_operator_prompt,
    validate_server_config as validate_server_config_tool,
    read_operator_prompt,
    write_operator_prompt,
)
from opci.mcp.tools.batch_management import (
    init_batch,
    batch_claim,
    batch_attach_run,
    batch_complete,
    batch_show,
)
from opci.mcp.tools.constraints import (
    normalize_constraints as normalize_constraints_tool,
    validate_constraints as validate_constraints_tool,
)
from opci.mcp.tools.cases import (
    generate_cases as generate_cases_tool,
    validate_cases as validate_cases_tool,
)
from opci.mcp.tools.execution import (
    execute_cases_generate,
    execute_cases_real,
    execute_cases_mock,
    validate_execution as validate_execution_tool,
    validate_executor as validate_executor_tool,
)
from opci.mcp.tools.analysis import (
    validate_analysis as validate_analysis_tool,
)
from opci.mcp.tools.registry import (
    show_workforce as show_workforce_tool,
)

# Explicitly register each function as an MCP tool
mcp.tool(init_run)
mcp.tool(update_run_state)
mcp.tool(find_latest_operator_prompt)
mcp.tool(validate_server_config_tool)
mcp.tool(read_operator_prompt)
mcp.tool(write_operator_prompt)
mcp.tool(init_batch)
mcp.tool(batch_claim)
mcp.tool(batch_attach_run)
mcp.tool(batch_complete)
mcp.tool(batch_show)
mcp.tool(normalize_constraints_tool)
mcp.tool(validate_constraints_tool)
mcp.tool(generate_cases_tool)
mcp.tool(validate_cases_tool)
mcp.tool(execute_cases_generate)
mcp.tool(execute_cases_real)
mcp.tool(execute_cases_mock)
mcp.tool(validate_execution_tool)
mcp.tool(validate_executor_tool)
mcp.tool(validate_analysis_tool)
mcp.tool(show_workforce_tool)
