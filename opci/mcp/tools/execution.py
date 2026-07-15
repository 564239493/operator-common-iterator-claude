"""execution MCP tools: execute_cases_generate, execute_cases_real, execute_cases_mock, validate_execution, validate_executor."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from opci.config import (
    config_error_payload,
    get_project_root,
    resolve_input_path,
    validate_server_config,
)
from opci.mcp._logging import log, log_elapsed
from opci.mcp._shared import validate_execution as _validate_execution
from opci.mcp._shared import validate_executor as _validate_executor


def execute_cases_generate(
    cases: str,
    output: str,
    doc: str,
    operator: str,
    server_config: str = "servers.json",
    run_id: str = "manual",
    env_init: str | None = None,
    artifact_dir: str | None = None,
) -> dict[str, Any]:
    """Generate executor and expanded cases (no SSH/ATK)."""
    t0 = time.monotonic()
    log("execute_cases_generate", "start", cases=cases, output=output, operator=operator, run_id=run_id)
    project_root = get_project_root()

    from opci.executer.runner import RunRequest, run_cases

    cases_path = resolve_input_path(cases, project_root)
    output_path = resolve_input_path(output, project_root)
    doc_path = resolve_input_path(doc, project_root)
    log("execute_cases_generate", "paths_resolved", cases_path=str(cases_path), output_path=str(output_path))

    config_path, config_errors = validate_server_config(server_config, project_root)
    if config_errors:
        log("execute_cases_generate", "config_error", errors=config_errors)
        return config_error_payload(config_path, config_errors)

    iter_dir = cases_path.parent if cases_path.parent.is_dir() else None
    artifact = resolve_input_path(artifact_dir, project_root) if artifact_dir else (
        iter_dir / "execution_logs" if iter_dir else project_root / "execution_results" / run_id
    )

    log("execute_cases_generate", "create_request")
    request = RunRequest(
        cases_path=cases_path,
        server_info={},
        operator_name=operator,
        run_id=run_id,
        artifact_dir=artifact,
        project_root=project_root,
        env_init=env_init,
        iter_dir=iter_dir,
    )

    log("execute_cases_generate", "load_cases")
    from opci.executer.runner import load_cases_payload
    cases_data = load_cases_payload(cases_path)

    log("execute_cases_generate", "run_generate")
    result = run_cases("generate", cases_data, request=request)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log_elapsed("execute_cases_generate", "done", t0, status=result.get("status"))
    return {"ok": True, "status": result.get("status"), **_extract_summary(result)}


def execute_cases_real(
    cases: str,
    output: str,
    doc: str,
    operator: str,
    server_config: str = "servers.json",
    run_id: str = "manual",
    platform: str | None = None,
    env_init: str | None = None,
    artifact_dir: str | None = None,
) -> dict[str, Any]:
    """Execute cases in real mode (SSH + ATK)."""
    t0 = time.monotonic()
    log("execute_cases_real", "start", cases=cases, output=output, operator=operator, platform=platform)
    project_root = get_project_root()

    from opci.executer.runner import RunRequest, run_cases, load_cases_payload, validate_server_info

    cases_path = resolve_input_path(cases, project_root)
    output_path = resolve_input_path(output, project_root)
    doc_path = resolve_input_path(doc, project_root)
    log("execute_cases_real", "paths_resolved", cases_path=str(cases_path))

    config_path, config_errors = validate_server_config(server_config, project_root)
    if config_errors:
        log("execute_cases_real", "config_error", errors=config_errors)
        return config_error_payload(config_path, config_errors)

    log("execute_cases_real", "select_server")
    servers = json.loads(config_path.read_text(encoding="utf-8")).get("servers", [])
    selected_server, selected_platform, select_error = _select_server(servers, platform, [])
    if selected_server is None:
        log("execute_cases_real", "no_server", error=select_error)
        return {
            "ok": False,
            "requires_user_action": True,
            "code": "NO_SERVER_FOR_PLATFORM",
            "message": select_error or "没有可用服务器。",
        }

    server_error = validate_server_info(selected_server)
    if server_error:
        log("execute_cases_real", "server_config_incomplete", error=server_error)
        return {
            "ok": False,
            "requires_user_action": True,
            "code": "SERVER_CONFIG_INCOMPLETE",
            "message": server_error,
        }

    iter_dir = cases_path.parent if cases_path.parent.is_dir() else None
    artifact = resolve_input_path(artifact_dir, project_root) if artifact_dir else (
        iter_dir / "execution_logs" if iter_dir else project_root / "execution_results" / run_id
    )

    log("execute_cases_real", "create_request", selected_platform=selected_platform)
    request = RunRequest(
        cases_path=cases_path,
        server_info=selected_server,
        operator_name=operator,
        run_id=run_id,
        artifact_dir=artifact,
        project_root=project_root,
        env_init=env_init or selected_server.get("env_init_script"),
        iter_dir=iter_dir,
    )

    log("execute_cases_real", "load_cases")
    cases_data = load_cases_payload(cases_path)

    log("execute_cases_real", "run_real")
    result = run_cases("real", cases_data, request=request)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log_elapsed("execute_cases_real", "done", t0, status=result.get("status"))
    return {"ok": True, **_extract_summary(result)}


def execute_cases_mock(
    cases: str,
    output: str,
    fail_every: int = 3,
) -> dict[str, Any]:
    """Execute cases in mock mode (local, deterministic)."""
    t0 = time.monotonic()
    log("execute_cases_mock", "start", cases=cases, output=output, fail_every=fail_every)
    project_root = get_project_root()

    from opci.executer.runner import run_cases, load_cases_payload

    cases_path = resolve_input_path(cases, project_root)
    output_path = resolve_input_path(output, project_root)
    log("execute_cases_mock", "paths_resolved", cases_path=str(cases_path))

    cases_data = load_cases_payload(cases_path)
    log("execute_cases_mock", "run_mock")
    result = run_cases("mock", cases_data, fail_every=fail_every)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log_elapsed("execute_cases_mock", "done", t0, status=result.get("status"))
    return {"ok": True, **_extract_summary(result)}


def validate_execution(path: str) -> dict[str, Any]:
    """Validate execution_result.json."""
    t0 = time.monotonic()
    log("validate_execution", "start", path=path)
    file_path = Path(path).resolve()
    if not file_path.is_file():
        log("validate_execution", "file_not_found", path=path)
        return {"valid": False, "errors": [f"File not found: {path}"]}
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
        log("validate_execution", "json_parsed", keys=list(value.keys())[:5])
        errors = _validate_execution(value)
        log_elapsed("validate_execution", "done", t0, valid=not errors, error_count=len(errors))
    except Exception as exc:
        log("validate_execution", "exception", error=str(exc))
        errors = [str(exc)]
    return {"valid": not errors, "errors": errors}


def validate_executor(path: str) -> dict[str, Any]:
    """Validate cases_executor.py (dummy markers + syntax)."""
    t0 = time.monotonic()
    log("validate_executor", "start", path=path)
    errors = _validate_executor(path)
    log_elapsed("validate_executor", "done", t0, valid=not errors, error_count=len(errors))
    return {"valid": not errors, "errors": errors}


def _select_server(servers, requested_platform, operator_platforms):
    """Select a server and platform for execution."""
    if requested_platform:
        for server in servers:
            if requested_platform in (server.get("platforms") or []):
                return server, requested_platform, None
        return None, None, f"没有匹配平台 {requested_platform!r} 的服务器条目"

    for server in servers:
        server_platforms = server.get("platforms") or []
        for platform in server_platforms:
            return server, platform, None
    return None, None, "servers.json 中没有可用服务器"


def _extract_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        k: result.get(k)
        for k in ("status", "mode", "passed", "failed", "total", "engine_error")
    }
