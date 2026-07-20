"""opci CLI entry point.

Commands:
  opci setup       — Copy Agent Pack resources to user's working directory
  opci mcp-server  — Start the MCP server in stdio mode
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from opci.config import PACKAGE_ROOT, PROJECT_ROOT_MARKER


def cmd_setup(args: argparse.Namespace) -> int:
    """Copy Agent Pack + bundled data to the user's working directory."""
    target = Path(args.target).resolve() if args.target else Path.cwd().resolve()
    resources_dir = PACKAGE_ROOT / "resources"

    if not resources_dir.is_dir():
        print(f"Error: resources directory not found at {resources_dir}", file=sys.stderr)
        return 1

    # 1. Copy .claude/ structure
    claude_source = resources_dir / "claude"
    claude_target = target / ".claude"
    if claude_source.is_dir():
        # Copy agents
        agents_src = claude_source / "agents"
        agents_dst = claude_target / "agents"
        if agents_src.is_dir():
            shutil.copytree(agents_src, agents_dst, dirs_exist_ok=True)

        # Copy skills
        skills_src = claude_source / "skills"
        skills_dst = claude_target / "skills"
        if skills_src.is_dir():
            shutil.copytree(skills_src, skills_dst, dirs_exist_ok=True)

        # Copy hooks
        hooks_src = claude_source / "hooks"
        hooks_dst = claude_target / "hooks"
        if hooks_src.is_dir():
            shutil.copytree(hooks_src, hooks_dst, dirs_exist_ok=True)

    # 2. Copy .mcp.json to project root (Claude Code loads MCP servers from this file)
    mcp_json_src = resources_dir / ".mcp.json"
    mcp_json_dst = target / ".mcp.json"
    if mcp_json_src.is_file():
        shutil.copy2(mcp_json_src, mcp_json_dst)

    # 3. Generate .claude/settings.json (permissions, hooks, sandbox — no mcpServers)
    settings_src = resources_dir / "settings_template.json"
    settings_dst = claude_target / "settings.json"
    if settings_src.is_file():
        settings = json.loads(settings_src.read_text(encoding="utf-8"))
        # MCP config goes in .mcp.json, not settings.json — remove if present
        settings.pop("mcpServers", None)
        claude_target.mkdir(parents=True, exist_ok=True)
        settings_dst.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # 4. Copy prompts
    prompts_src = resources_dir / "prompts"
    prompts_dst = target / "prompts"
    if prompts_src.is_dir():
        shutil.copytree(prompts_src, prompts_dst, dirs_exist_ok=True)

    # 5. Copy docs
    docs_src = resources_dir / "docs"
    docs_dst = target / "docs"
    if docs_src.is_dir():
        shutil.copytree(docs_src, docs_dst, dirs_exist_ok=True)

    # 6. Copy knowledge
    knowledge_src = resources_dir / "knowledge"
    knowledge_dst = target / "knowledge"
    if knowledge_src.is_dir():
        shutil.copytree(knowledge_src, knowledge_dst, dirs_exist_ok=True)

    # 7. Copy servers.example.json
    servers_src = resources_dir / "servers.example.json"
    servers_dst = target / "servers.example.json"
    if servers_src.is_file():
        shutil.copy2(servers_src, servers_dst)

    # 8. Create .opci_project_root marker (pins project root for MCP tools)
    marker_path = target / PROJECT_ROOT_MARKER
    marker_path.write_text(str(target) + "\n", encoding="utf-8")

    # 9. Create empty directories
    (target / "runs").mkdir(exist_ok=True)
    (target / "operator_docs").mkdir(exist_ok=True)
    (target / "prompts").mkdir(exist_ok=True)  # ensure it exists even if no bundled prompts

    print(json.dumps({
        "ok": True,
        "target": str(target),
        "created_files": [
            str(mcp_json_dst),
            str(settings_dst),
        ],
        "created_directories": [
            str(target / ".claude"),
            str(target / "runs"),
            str(target / "operator_docs"),
            str(target / "prompts"),
        ],
        "message": (
            f"Agent Pack 已安装到 {target}。\n"
            f"MCP 配置写入 .mcp.json，Claude Code 启动时自动加载。\n"
            f"请确保 opci 命令在 PATH 中可用（pip install opci 后激活对应环境）。"
        ),
    }, ensure_ascii=False, indent=2))
    return 0


def _warmup() -> None:
    """Pre-import ALL dependencies before MCP stdio protocol starts.

    In MCP stdio mode, stdout is the JSON-RPC protocol channel. Heavy C
    extensions (z3, numpy, torch, etc.) may write to stdout during import,
    which would corrupt the protocol and cause Claude Code to timeout.

    This warmup loads **every** module that any MCP tool function might need,
    with stdout temporarily redirected to stderr so any accidental writes
    land in stderr (free for diagnostics) instead of the protocol pipe.

    After warmup, all successfully imported modules are cached in
    ``sys.modules`` — tool calls never trigger a fresh import, no protocol
    corruption risk. Import failures are logged prominently at startup so
    developers see them immediately, not during a tool call.
    """
    _original_stdout = sys.stdout
    sys.stdout = sys.stderr  # protect protocol channel

    _ok: list[str] = []
    _fail: list[tuple[str, str]] = []

    def _try_import(label: str, statement: str) -> None:
        """Try an import; record success or failure."""
        try:
            exec(statement, {"__builtins__": __builtins__})
            print(f"[warmup] {label} OK", file=sys.stderr)
            _ok.append(label)
        except Exception as exc:
            msg = str(exc).split("\n")[0][:120]
            print(f"[warmup] {label} FAIL: {msg}", file=sys.stderr)
            _fail.append((label, msg))

    try:
        print("[warmup] Pre-importing all dependencies...", file=sys.stderr)

        # ── C extensions ──
        _try_import("z3",              "import z3")
        _try_import("numpy",           "import numpy")
        _try_import("torch",           "import torch")
        _try_import("scipy",           "import scipy")
        _try_import("asyncssh",        "import asyncssh")
        _try_import("openpyxl",        "import openpyxl")

        # ── Generator chain ──
        _try_import("OperatorRule",
                    "from opci.agent.generators.common_model_definition import OperatorRule")
        _try_import("TestCaseGenerator",
                    "from opci.agent.generators.facade import TestCaseGenerator")
        _try_import("RunPlatform",
                    "from opci.agent.generators.data_definition.param_models_def import RunPlatform")
        _try_import("single_operator_handle",
                    "from opci.agent.generators.operator_handle_main import single_operator_handle")

        # ── Executer chain ──
        _try_import("RunRequest",
                    "from opci.executer.runner import RunRequest")
        _try_import("run_cases",
                    "from opci.executer.runner import run_cases")
        _try_import("load_cases_payload",
                    "from opci.executer.runner import load_cases_payload")
        _try_import("validate_server_info",
                    "from opci.executer.runner import validate_server_info")

        # ── Summary ──
        print("", file=sys.stderr)
        if _fail:
            print(f"[warmup] *** {len(_fail)} IMPORT FAILURES ***", file=sys.stderr)
            for label, msg in _fail:
                print(f"[warmup]   {label}: {msg}", file=sys.stderr)
            print(f"[warmup] Tools requiring failed modules will return errors when called.", file=sys.stderr)
        else:
            print("[warmup] All dependencies loaded successfully.", file=sys.stderr)
        print(f"[warmup] {len(_ok)} OK, {len(_fail)} FAIL", file=sys.stderr)

    finally:
        sys.stdout = _original_stdout  # restore protocol channel


def cmd_mcp_server(args: argparse.Namespace) -> int:
    """Start the fastmcp MCP server in stdio mode."""
    _warmup()

    # Attach FileHandlers to deterministic Python loggers so their output
    # lands in the unified logs/tools/ directory (not just stderr).
    from opci.mcp._logging import setup_tool_logging
    setup_tool_logging()

    from opci.server import mcp
    mcp.run()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="opci - Operator Common Iterator MCP Server + Agent Pack"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # setup
    setup_parser = subparsers.add_parser("setup", help="Copy Agent Pack to working directory")
    setup_parser.add_argument("--target", default=None, help="Target directory (default: cwd)")

    # mcp-server
    mcp_parser = subparsers.add_parser("mcp-server", help="Start MCP server in stdio mode")

    args = parser.parse_args()
    if args.command == "setup":
        return cmd_setup(args)
    elif args.command == "mcp-server":
        return cmd_mcp_server(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
