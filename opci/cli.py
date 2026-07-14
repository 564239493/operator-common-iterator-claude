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

from opci.config import PACKAGE_ROOT


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

    # 8. Create empty directories
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


def cmd_mcp_server(args: argparse.Namespace) -> int:
    """Start the fastmcp MCP server in stdio mode."""
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
