"""execution MCP tools: execute_cases_generate, execute_cases_real, execute_cases_mock, validate_execution, validate_executor.

Strictly aligned with scripts/execute_cases.py. Only adaptation:
- RuntimeError/raise SystemExit → return {"ok": False, ...} for MCP protocol
- argparse CLI → MCP tool function parameters
- _emit() stdout prints → MCP return dict
- No changes to business logic whatsoever.
"""

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


# ---------------------------------------------------------------------------
# Helpers from scripts/execute_cases.py (L63-161), unchanged logic
# ---------------------------------------------------------------------------

def _load_server_config(path: Path) -> list[dict[str, Any]]:
    """Pull the ``servers`` list out of the validated config (original: L63-69)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    servers = payload.get("servers")
    if not isinstance(servers, list):
        return []
    return servers


def _read_json_object(path: Path) -> dict[str, Any] | None:
    """Read and parse a JSON object file; return None on any error (original: L72-77)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _platform_from_cases_name(path: Path) -> str | None:
    """Extract platform string from cases_<platform>.json filename (original: L80-84)."""
    name = path.name
    if not name.startswith("cases_") or not name.endswith(".json"):
        return None
    return name[len("cases_") : -len(".json")].replace("_", "/")


def _load_operator_supported_platforms(iter_dir: Path | None) -> list[str]:
    """Return platform names the operator has generated/supports (original: L87-115).

    Priority is the contract source ``constraints.json.product_support``.
    ``generation_summary.json`` and per-platform case filenames are fallbacks
    for ad-hoc runs where constraints were not passed along.
    """
    if iter_dir is None:
        return []

    constraints = _read_json_object(iter_dir / "constraints.json")
    product_support = constraints.get("product_support") if constraints else None
    if isinstance(product_support, list):
        return [str(p) for p in product_support if str(p).strip()]

    summary = _read_json_object(iter_dir / "generation_summary.json")
    platforms = summary.get("platforms") if summary else None
    if isinstance(platforms, dict):
        return [str(p) for p in platforms.keys() if str(p).strip()]
    per_platform_files = summary.get("per_platform_files") if summary else None
    if isinstance(per_platform_files, dict):
        return [str(p) for p in per_platform_files.keys() if str(p).strip()]

    inferred: list[str] = []
    for path in sorted(iter_dir.glob("cases_*.json")):
        platform = _platform_from_cases_name(path)
        if platform:
            inferred.append(platform)
    return inferred


def _select_server_for_execution(
    servers: list[dict[str, Any]],
    requested_platform: str | None,
    operator_platforms: list[str],
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Choose one server and one product platform for this execution (original: L118-161).

    Without ``--platform``, selection follows ``servers.json`` order:
    iterate servers in file order, then each server's ``platforms`` array in
    priority order, and pick the first product supported by the operator.
    """
    if requested_platform:
        for server in servers:
            if requested_platform in (server.get("platforms") or []):
                return server, requested_platform, None
        return (
            None,
            None,
            f"servers.json 中没有匹配平台 {requested_platform!r} 的条目。",
        )

    if not operator_platforms:
        if servers:
            server = servers[0]
            platforms = server.get("platforms") or []
            selected = platforms[0] if platforms else None
            return server, selected, None
        return None, None, "servers.json 中没有可用服务器。"

    supported = set(operator_platforms)
    for server in servers:
        server_platforms = server.get("platforms") or []
        for platform in server_platforms:
            if platform in supported:
                return server, platform, None

    return (
        None,
        None,
        "servers.json 中配置的 platforms 与算子 product_support 没有交集: "
        f"servers={[s.get('platforms') for s in servers]}, "
        f"operator={operator_platforms}",
    )


def _extract_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        k: result.get(k)
        for k in ("status", "mode", "passed", "failed", "total", "engine_error")
    }


# ---------------------------------------------------------------------------
# MCP tool functions — aligned with scripts/execute_cases.py main()
# ---------------------------------------------------------------------------

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
    """Generate executor and expanded cases (no SSH/ATK).

    Aligned with scripts/execute_cases.py L258-370 for effective_mode == "generate".
    """
    t0 = time.monotonic()
    log("execute_cases_generate", "start", cases=cases, output=output, operator=operator, run_id=run_id)
    project_root = get_project_root()

    # Heavy import — kept inline for robustness
    from opci.executer.runner import RunRequest, run_cases, load_cases_payload, validate_server_info

    cases_path = resolve_input_path(cases, project_root)
    output_path = resolve_input_path(output, project_root)
    doc_path = resolve_input_path(doc, project_root)
    log("execute_cases_generate", "paths_resolved", cases_path=str(cases_path), output_path=str(output_path))

    # original: L271-285 — doc/operator required for non-mock modes
    # (MCP tool already requires doc and operator as parameters, so this check is implicit)

    # original: L290-291
    iter_dir = cases_path.parent if cases_path.parent.is_dir() else None
    operator_platforms = _load_operator_supported_platforms(iter_dir)

    # original: L293-296
    config_path, config_errors = validate_server_config(server_config, project_root)
    if config_errors:
        log("execute_cases_generate", "config_error", errors=config_errors)
        return config_error_payload(config_path, config_errors)

    # original: L298-303
    servers = _load_server_config(config_path)
    server, selected_platform, select_error = _select_server_for_execution(
        servers, None, operator_platforms,
    )
    if server is None:
        log("execute_cases_generate", "no_server", error=select_error)
        return {
            "ok": False,
            "requires_user_action": True,
            "code": "NO_SERVER_FOR_PLATFORM",
            "message": select_error or "没有可用于执行该算子的服务器平台。",
            "server_config": str(config_path),
            "operator_platforms": operator_platforms,
        }

    # original: L316-321 — adjust platforms order
    selected_server = dict(server)
    if selected_platform:
        original_platforms = list(server.get("platforms") or [])
        selected_server["platforms"] = [selected_platform] + [
            p for p in original_platforms if p != selected_platform
        ]

    # original: L343-345 — generate mode: just sanity-check field presence
    _, _ = validate_server_config(server_config, project_root)

    # original: L347-357 — artifact_dir resolution
    if artifact_dir:
        artifact = resolve_input_path(artifact_dir, project_root)
    elif iter_dir is not None:
        artifact = iter_dir / "execution_logs"
    else:
        artifact = project_root / "execution_results" / run_id
    artifact.mkdir(parents=True, exist_ok=True)

    # original: L359-368 — RunRequest construction
    log("execute_cases_generate", "create_request", selected_platform=selected_platform)
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

    # original: L256 + L370
    cases_data = load_cases_payload(cases_path)
    log("execute_cases_generate", "run_generate")
    result = run_cases("generate", cases_data, request=request)

    # original: L372-375 — write output
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
    """Execute cases in real mode (SSH + ATK).

    Aligned with scripts/execute_cases.py L264-370 for effective_mode == "real".
    """
    t0 = time.monotonic()
    log("execute_cases_real", "start", cases=cases, output=output, operator=operator, platform=platform)
    project_root = get_project_root()

    # Heavy import — kept inline for robustness
    from opci.executer.runner import RunRequest, run_cases, load_cases_payload, validate_server_info

    cases_path = resolve_input_path(cases, project_root)
    output_path = resolve_input_path(output, project_root)
    doc_path = resolve_input_path(doc, project_root)
    log("execute_cases_real", "paths_resolved", cases_path=str(cases_path))

    # original: L290-291
    iter_dir = cases_path.parent if cases_path.parent.is_dir() else None
    operator_platforms = _load_operator_supported_platforms(iter_dir)

    # original: L293-296
    config_path, config_errors = validate_server_config(server_config, project_root)
    if config_errors:
        log("execute_cases_real", "config_error", errors=config_errors)
        return config_error_payload(config_path, config_errors)

    # original: L298-315
    servers = _load_server_config(config_path)
    server, selected_platform, select_error = _select_server_for_execution(
        servers, platform, operator_platforms,
    )
    if server is None:
        log("execute_cases_real", "no_server", error=select_error)
        return {
            "ok": False,
            "requires_user_action": True,
            "code": "NO_SERVER_FOR_PLATFORM",
            "message": select_error or "没有可用于执行该算子的服务器平台。",
            "server_config": str(config_path),
            "operator_platforms": operator_platforms,
        }

    # original: L316-321 — adjust platforms order
    selected_server = dict(server)
    if selected_platform:
        original_platforms = list(server.get("platforms") or [])
        selected_server["platforms"] = [selected_platform] + [
            p for p in original_platforms if p != selected_platform
        ]

    # original: L327-342 — real mode: strict credential validation
    server_error = validate_server_info(selected_server)
    if server_error:
        log("execute_cases_real", "server_config_incomplete", error=server_error)
        return {
            "ok": False,
            "requires_user_action": True,
            "code": "SERVER_CONFIG_INCOMPLETE",
            "message": server_error,
            "server_config": str(config_path),
            "hint": "编辑 servers.json, 填写真实 ip/username/password 后再执行。",
        }

    # original: L347-357 — artifact_dir resolution
    if artifact_dir:
        artifact = resolve_input_path(artifact_dir, project_root)
    elif iter_dir is not None:
        artifact = iter_dir / "execution_logs"
    else:
        artifact = project_root / "execution_results" / run_id
    artifact.mkdir(parents=True, exist_ok=True)

    # original: L359-368 — RunRequest construction
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

    # original: L256 + L370
    cases_data = load_cases_payload(cases_path)
    log("execute_cases_real", "run_real")
    result = run_cases("real", cases_data, request=request)

    # original: L372-375 — write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log_elapsed("execute_cases_real", "done", t0, status=result.get("status"))
    return {"ok": True, **_extract_summary(result)}


def execute_cases_mock(
    cases: str,
    output: str,
    fail_every: int = 3,
) -> dict[str, Any]:
    """Execute cases in mock mode (local, deterministic).

    Aligned with scripts/execute_cases.py L264-269.
    """
    t0 = time.monotonic()
    log("execute_cases_mock", "start", cases=cases, output=output, fail_every=fail_every)
    project_root = get_project_root()

    from opci.executer.runner import run_cases, load_cases_payload

    cases_path = resolve_input_path(cases, project_root)
    output_path = resolve_input_path(output, project_root)
    log("execute_cases_mock", "paths_resolved", cases_path=str(cases_path))

    cases_data = load_cases_payload(cases_path)
    log("execute_cases_mock", "run_mock")
    # original: L264-269 — max(0, fail_every) guards against negative values
    result = run_cases("mock", cases_data, fail_every=max(0, fail_every))

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
        log("execute_cases_real", "exception", error=str(exc))
        errors = [str(exc)]
    return {"valid": not errors, "errors": errors}


def validate_executor(path: str) -> dict[str, Any]:
    """Validate cases_executor.py (dummy markers + syntax)."""
    t0 = time.monotonic()
    log("validate_executor", "start", path=path)
    errors = _validate_executor(path)
    log_elapsed("validate_executor", "done", t0, valid=not errors, error_count=len(errors))
    return {"valid": not errors, "errors": errors}
