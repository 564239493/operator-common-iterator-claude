#!/usr/bin/env python3
"""Prepare plugin value contracts for a TTK run (EXTRACT helper).

Steps:
1. resolve ``run_state.ttk_plugin`` (local file/dir or remote ``/path``);
2. statically analyze the plugin -> ``inputs/plugin_requirements.json``;
3. extract candidate value rules -> ``inputs/value_constraints.candidates.json``;
4. with ``--confirm``, finalize reviewed rules -> ``inputs/value_constraints.json``
   and validate the artifact.

Without ``--confirm`` the candidates are written for review; generation skips
them until a later ``--confirm`` pass.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_ttk_plugin import analyze  # noqa: E402
from extract_value_constraints import extract  # noqa: E402
from finalize_value_constraints import finalize  # noqa: E402
from validate_artifacts import validate_value_constraints  # noqa: E402


def _load_state(run_dir: Path) -> dict:
    state_path = run_dir / "run_state.json"
    if not state_path.is_file():
        raise SystemExit(f"run_state.json not found: {state_path}")
    return json.loads(state_path.read_text(encoding="utf-8"))


def _companion_paths(state: dict) -> list[Path]:
    paths: list[Path] = []
    for item in state.get("companion_docs") or []:
        if not isinstance(item, dict):
            continue
        snapshot = item.get("snapshot") or item.get("source")
        if snapshot and Path(snapshot).is_file():
            paths.append(Path(snapshot))
    return paths


def _snapshot_remote(remote: str, snapshot_dir: Path) -> Path:
    from executer.ssh import (
        ServerEndpoint,
        connect,
        download_file,
        sftp_download_tree,
    )

    config_path = ROOT / "servers.json"
    if not config_path.is_file():
        raise SystemExit("servers.json is required to snapshot a remote plugin")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    servers = payload.get("servers") if isinstance(payload, dict) else None
    if not isinstance(servers, list) or not servers:
        raise SystemExit("servers.json has no servers array")
    endpoint = ServerEndpoint.from_server_row(servers[0])
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    name = Path(remote.rstrip("/")).name or "plugin"
    local = snapshot_dir / name

    async def _run() -> None:
        conn = await connect(endpoint)
        try:
            try:
                await sftp_download_tree(conn, remote, local)
            except Exception:
                if local.is_file():
                    local.unlink()
                await download_file(conn, remote, local)
        finally:
            conn.close()

    asyncio.run(_run())
    if not local.exists():
        raise SystemExit(f"remote plugin snapshot failed: {remote} -> {local}")
    return local


def _resolve_plugin(raw: str, snapshot_dir: Path) -> Path:
    path = Path(raw).expanduser()
    if path.exists():
        if path.is_dir():
            target = snapshot_dir / path.name
            if target.exists():
                return target
        return path
    if str(path).startswith("/"):
        return _snapshot_remote(str(path), snapshot_dir)
    raise SystemExit(f"plugin path not found: {raw}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare plugin value contracts")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--plugin", default=None, help="override run_state.ttk_plugin")
    parser.add_argument(
        "--constraints", default=None,
        help="constraints.json override (default: iter_<N>/constraints.json)",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="mark candidates as reviewed and write inputs/value_constraints.json",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    state = _load_state(run_dir)
    if state.get("test_framework") != "ttk":
        print(json.dumps({
            "ok": True, "skipped": "test_framework is not ttk",
            "test_framework": state.get("test_framework"),
        }, ensure_ascii=False))
        return 0
    raw_plugin = args.plugin or str(state.get("ttk_plugin") or "")
    if not raw_plugin.strip():
        print(json.dumps({
            "ok": False,
            "code": "TTK_PLUGIN_REQUIRED",
            "message": "Metadata/TTK run requires --ttk-plugin / run_state.ttk_plugin",
        }, ensure_ascii=False))
        return 2

    iteration = int(state.get("current_iteration") or 1)
    iter_dir = run_dir / f"iter_{iteration:03d}"
    constraints_path = (
        Path(args.constraints).expanduser().resolve()
        if args.constraints
        else iter_dir / "constraints.json"
    )
    if not constraints_path.is_file():
        print(json.dumps({
            "ok": False,
            "code": "CONSTRAINTS_NOT_FOUND",
            "constraints": str(constraints_path),
        }, ensure_ascii=False))
        return 2
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))

    inputs_dir = run_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = inputs_dir / "ttk_plugin_snapshot"
    plugin = _resolve_plugin(raw_plugin, snapshot_dir)

    requirements = analyze(plugin, constraints)
    requirements_path = inputs_dir / "plugin_requirements.json"
    requirements_path.write_text(
        json.dumps(requirements, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    doc = Path(str(state.get("operator_doc") or ""))
    if not doc.is_file():
        print(json.dumps({
            "ok": False, "code": "OPERATOR_DOC_NOT_FOUND", "doc": str(doc),
        }, ensure_ascii=False))
        return 2
    candidates = extract(doc, _companion_paths(state), requirements)
    candidates_path = inputs_dir / "value_constraints.candidates.json"
    candidates_path.write_text(
        json.dumps({
            "ok": True,
            "source": {
                "doc": str(doc),
                "companion_docs": [str(path) for path in _companion_paths(state)],
                "plugin": str(plugin),
            },
            "value_constraints": candidates,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = {
        "ok": True,
        "plugin": str(plugin),
        "plugin_sha256": requirements.get("plugin_sha256"),
        "required_value_attrs": len(requirements.get("required_value_attrs") or []),
        "candidates": len(candidates),
        "requirements": str(requirements_path),
        "candidates_path": str(candidates_path),
        "confirmed": bool(args.confirm),
    }
    if args.confirm:
        accepted, dropped = finalize(candidates, requirements, True)
        value_constraints_path = iter_dir / "value_constraints.json"
        payload_text = json.dumps({
            "ok": True,
            "confirmed": True,
            "value_constraints": accepted,
            "dropped": dropped,
        }, ensure_ascii=False, indent=2) + "\n"
        value_constraints_path.write_text(payload_text, encoding="utf-8")
        if iteration == 1:
            (inputs_dir / "value_constraints.json").write_text(
                payload_text, encoding="utf-8",
            )
        errors = validate_value_constraints(
            {"value_constraints": accepted}
        )
        summary["value_constraints"] = str(value_constraints_path)
        summary["accepted"] = len(accepted)
        summary["dropped"] = len(dropped)
        summary["validation_errors"] = errors
        if errors:
            summary["ok"] = False
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
