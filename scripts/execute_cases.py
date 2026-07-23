#!/usr/bin/env python3
"""Project-local CLI entry point for the EXECUTE stage.

Driven by the ``case-executor`` agent (see ``.claude/agents/case-executor.md``)
through the ``execute-cases`` skill.  This script is the single CLI glue
between the deterministic executer (`executer.runner`) and the
``runs/<run-id>/iter_*/execution_result.json`` artifact contract.

Why this rewrite
----------------

The previous version imported
``agent.nodes.executer_subgraph.create_executer_subgraph`` by hacking
``sys.path`` to point at the external ``operator-agent`` package.  That
triggers Pydantic ``Settings`` validation at module-import time and
requires ``ZAI_API_KEY`` / other secrets in the external project's
``operator-agent/.env`` — which is precisely the ``environment-blocked
(ZAI_API_KEY 占位符未替换)`` failure the user reported.

This rewrite keeps everything inside this project:

* No ``sys.path`` reach-around to ``D:\\operator_project\\operator-common-iterator``
  (reference) or ``D:\\operator_project\\operator-agent`` (the old consumer).
* No ``langchain_openai.ChatOpenAI`` / ``Settings(active_api_key=...)``
  imports — the CLI itself is the LLM (per ``CLAUDE.md``).
* Real execution still goes through SSH / asyncssh against the host
  declared in ``servers.json`` — we just hand control to the
  project-local ``executer.runner.RunRequest`` path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # noqa: E402  (sys.path bootstrap above)
    from runtime_config import (
        config_error_payload,
        resolve_input_path,
        validate_server_config,
    )
except ModuleNotFoundError:  # imported as ``scripts.execute_cases`` in tests
    from scripts.runtime_config import (
        config_error_payload,
        resolve_input_path,
        validate_server_config,
    )


def _emit(payload: dict[str, Any]) -> None:
    """Print a structured JSON envelope — used for user-action prompts."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


def _load_server_config(path: Path) -> list[dict[str, Any]]:
    """Pull the ``servers`` list out of the validated config."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    servers = payload.get("servers")
    if not isinstance(servers, list):
        raise SystemExit("servers.json: servers 字段必须是数组")
    return servers


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _platform_from_cases_name(path: Path) -> str | None:
    name = path.name
    if not name.startswith("cases_") or not name.endswith(".json"):
        return None
    return name[len("cases_") : -len(".json")].replace("_", "/")


def _load_operator_supported_platforms(iter_dir: Path | None) -> list[str]:
    """Return platform names the operator has generated/supports.

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
    """Choose one server and one product platform for this execution.

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


def main() -> int:
    from executer import run_cases  # noqa: WPS433  (lazy: needs asyncssh)
    from executer.runner import (  # noqa: WPS433
        RunRequest,
        load_cases_payload,
        validate_server_info,
    )

    parser = argparse.ArgumentParser(
        description=(
            "执行已生成的测试用例并写出 execution_result.json。"
            "默认 real 模式；显式 --mode mock 才回退到本地 Mock。"
            "real 模式不再自动生成 executor：必须先跑 --generate 产出 "
            "cases_executor.py + cases_expanded.json，并由 atc-cpu-golden-derivation "
            "skill 完成 CPU golden 推导后，再以 real 上传执行。"
        )
    )
    parser.add_argument(
        "--mode",
        choices=("mock", "real"),
        default="real",
        help="执行模式 (默认 real)。real 仅上传+跑 atk，不再生成 executor。",
    )
    parser.add_argument(
        "--cases", required=True, help="cases.json 路径 (项目内或外部)。"
    )
    parser.add_argument(
        "--output", required=True, help="execution_result.json 输出路径。"
    )
    parser.add_argument(
        "--fail-every",
        type=int,
        default=3,
        help="mock 模式下每隔 N 条标记一次失败 (默认 3, 0 表示全通过)。",
    )
    parser.add_argument(
        "--doc",
        help=(
            "算子文档快照路径 (real 模式必填; 指向 run/inputs/ 内的快照)。"
        ),
    )
    parser.add_argument(
        "--operator",
        help="算子名 (real 模式必填, 与文档快照同名)。",
    )
    parser.add_argument(
        "--platform",
        default=None,
        help=(
            "可选: 手动指定执行平台。未指定时按 servers.json 中每台服务器 "
            "platforms 数组顺序, 选择第一个与算子 product_support 匹配的平台。"
        ),
    )
    parser.add_argument(
        "--server-config",
        default="servers.json",
        help="服务器配置文件路径 (默认 servers.json)。",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help=(
            "仅跑平台过滤 + generator.py, 不连 SSH/ATK。"
            "产出 cases_executor.py (含 dummy CPU golden) + cases_expanded.json "
            "到 iter_dir。real 模式的前置步骤：generate 生成 → atc-cpu-golden-derivation "
            "skill 改写 CPU golden → real 上传执行。"
        ),
    )
    parser.add_argument(
        "--run-id",
        default="manual",
        help="运行标识符, 用于缓存和诊断; 默认 manual。",
    )
    parser.add_argument(
        "--env-init",
        default=None,
        help=(
            "可选: 覆盖 servers.json 中的 env_init / env_init_script, "
            "用于 source CANN 环境等。优先级: --env-init > "
            "server.env_init > server.env_init_script > 内置默认。"
        ),
    )
    parser.add_argument(
        "--artifact-dir",
        default=None,
        help=(
            "下载的 ATK 产物落盘目录; "
            "默认 <run-dir>/iter_NNN/remote_artifacts。"
        ),
    )
    parser.add_argument(
        "--strategy",
        choices=("default", "fusion"),
        default="default",
        help=(
            "执行策略 (默认 default)。fusion 走通算融合 4 步流程 "
            "(CPU 标杆→NPU 级联标杆→改名→精度对比)。"
            "正常迭代由 case-executor 读 run_state.execution_strategy 后透传, "
            "此处仅作人工覆盖项。"
        ),
    )
    parser.add_argument(
        "--num",
        type=int,
        default=None,
        help=(
            "fusion 专用: 本次实际执行用例数, 透传 atk -e {num}。"
            "default 流程不使用。"
        ),
    )
    args = parser.parse_args()

    cases_path = resolve_input_path(args.cases)
    output_path = resolve_input_path(args.output)
    cases = load_cases_payload(cases_path)

    # --generate 隐式选择 generate 模式, 除非显式 --mode mock。
    # 这是为了让用户调试时少敲一段。
    effective_mode = args.mode
    if args.generate and effective_mode == "real":
        effective_mode = "generate"

    if args.mode == "mock":
        result = run_cases(
            "mock",
            cases,
            fail_every=max(0, args.fail_every),
        )
    else:
        if not args.doc or not args.operator:
            _emit(
                {
                    "ok": False,
                    "requires_user_action": True,
                    "code": "OPERATOR_DOC_REQUIRED",
                    "message": (
                        "真实/generate 执行需要 --doc 和 --operator; "
                        "请传入 run 目录中的算子文档快照与算子名。"
                    ),
                }
            )
            return 2
        args.doc = str(resolve_input_path(args.doc))
        args.operator = args.operator or Path(args.doc).stem

        # Iter directory is used by the runner to find constraints.json
        # + generation_summary.json for platform-based case filtering,
        # and to determine where ATK's log + xlsx + result.json land.
        iter_dir = cases_path.parent if cases_path.parent.is_dir() else None
        operator_platforms = _load_operator_supported_platforms(iter_dir)

        config_path, config_errors = validate_server_config(args.server_config)
        if config_errors:
            _emit(config_error_payload(config_path, config_errors))
            return 2

        servers = _load_server_config(config_path)
        server, selected_platform, select_error = _select_server_for_execution(
            servers,
            args.platform,
            operator_platforms,
        )
        if server is None:
            _emit(
                {
                    "ok": False,
                    "requires_user_action": True,
                    "code": "NO_SERVER_FOR_PLATFORM",
                    "message": select_error or "没有可用于执行该算子的服务器平台。",
                    "server_config": str(config_path),
                    "operator_platforms": operator_platforms,
                }
            )
            return 2
        selected_server = dict(server)
        if selected_platform:
            original_platforms = list(server.get("platforms") or [])
            selected_server["platforms"] = [selected_platform] + [
                p for p in original_platforms if p != selected_platform
            ]

        # Generate skips SSH / ATK, so it can run even when servers.json
        # still has placeholder credentials.  Relax the password check to
        # the schema level (presence / fields) only — leave the strict
        # placeholder detection for ``mode == real``.
        if effective_mode == "real":
            server_error = validate_server_info(selected_server)
            if server_error:
                _emit(
                    {
                        "ok": False,
                        "requires_user_action": True,
                        "code": "SERVER_CONFIG_INCOMPLETE",
                        "message": server_error,
                        "server_config": str(config_path),
                        "hint": (
                            "编辑 servers.json, 填写真实 ip/username/password 后再执行。"
                        ),
                    }
                )
                return 2
        else:
            # Generate: just sanity-check field presence.
            _, _ = validate_server_config(args.server_config)

        # Default to ``runs/<run-id>/iter_NNN/execution_logs/`` when we can
        # infer the iter layout — keeps ATK artifacts co-located with
        # the contract artifact (execution_result.json).  Ad-hoc runs
        # fall back to ``<project_root>/execution_results/<run_id>/``.
        if args.artifact_dir:
            artifact_dir = resolve_input_path(args.artifact_dir)
        elif iter_dir is not None:
            artifact_dir = iter_dir / "execution_logs"
        else:
            artifact_dir = ROOT / "execution_results" / args.run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        request = RunRequest(
            cases_path=cases_path,
            server_info=selected_server,
            operator_name=args.operator,
            run_id=args.run_id,
            artifact_dir=artifact_dir,
            project_root=ROOT,
            env_init=args.env_init,  # CLI override only; runner resolves full chain
            iter_dir=iter_dir,
            execution_strategy=args.strategy,
            case_count=args.num,
        )

        result = run_cases(effective_mode, cases, request=request)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in (
                    "status",
                    "mode",
                    "passed",
                    "failed",
                    "total",
                )
            },
            ensure_ascii=False,
        )
    )
    # Generate: surface the concrete next steps so the user can proceed
    # without reading the full execution_result.json.
    if result.get("status") == "generate":
        artifacts = result.get("generate_artifacts") or []
        remote_paths = result.get("generate_remote_paths") or {}
        atk_cmd = result.get("generate_atk_command") or ""
        print(
            json.dumps(
                {
                    "hint": "本地产物已就绪, 请 SFTP 上传后执行 atk 命令",
                    "generate_artifacts": [
                        {**a, "remote": remote_paths.get(a.get("key", ""), "?")}
                        for a in artifacts
                    ],
                    "generate_atk_command": atk_cmd,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0 if not result.get("engine_error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
    