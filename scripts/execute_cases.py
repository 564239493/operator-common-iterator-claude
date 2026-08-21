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
import ast
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ACLNN_GOLDEN_PLUGINS = {
    "aclnnGroupedMatmulV5": "aclnn_grouped_matmul_v5_golden.py",
    "aclnnScatterPaKvCache": "aclnn_scatter_pa_kv_cache_golden.py",
}

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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _artifact_fingerprints(iter_dir: Path, cases_path: Path) -> dict[str, Any]:
    """Bind an execution result to the exact generated inputs it consumed."""
    result: dict[str, Any] = {}
    for name, path in (
        ("constraints", iter_dir / "constraints.json"),
        ("cases_json", iter_dir / "cases.json"),
        ("cases_csv", cases_path),
        ("generation_summary", iter_dir / "generation_summary.json"),
    ):
        if not path.is_file():
            continue
        content = path.read_bytes()
        stat = path.stat()
        result[name] = {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "mtime_ns": stat.st_mtime_ns,
        }
    return result


def _attempt_artifact_dir(iter_dir: Path, kind: str) -> Path:
    """Keep repeated EXECUTE attempts isolated instead of mixing stale logs."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    return iter_dir / f"{kind}_artifacts" / f"attempt_{stamp}"


def _read_ttk_csv_identity(path: Path) -> tuple[str, str]:
    """Return (TTK mode, api_name) from the first CSV row."""
    with path.open(encoding="utf-8", newline="") as handle:
        first = next(csv.DictReader(handle), {})
    api_name = str(first.get("api_name") or "").strip()
    if not api_name:
        raise ValueError("TTK CSV has no data row with api_name")
    return ("aclnn" if api_name.startswith("aclnn") else "e2e"), api_name


def _resolve_aclnn_golden_plugin(
    iter_dir: Path,
    api_name: str,
    *,
    disabled: bool = False,
) -> Path | None:
    """Resolve iteration-specific Golden first, then the operator registry."""
    if disabled:
        return None
    iteration_golden = iter_dir / "golden.py"
    if iteration_golden.is_file():
        return iteration_golden

    registered_name = ACLNN_GOLDEN_PLUGINS.get(api_name)
    if registered_name:
        registered = (
            ROOT
            / "agent"
            / "hs"
            / "ttk_plugins"
            / registered_name
        )
        if registered.is_file():
            return registered
    return None


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


def _load_ttk_selected_platform(iter_dir: Path | None) -> str | None:
    """Return the platform whose canonical cases.json was converted to CSV."""
    if iter_dir is None:
        return None
    summary = _read_json_object(iter_dir / "generation_summary.json")
    selected = summary.get("selected_platform") if summary else None
    return str(selected) if selected else None


def _load_ttk_generated_platforms(iter_dir: Path | None) -> list[str]:
    """Return platforms that actually have generated per-platform artifacts."""
    if iter_dir is None:
        return []
    summary = _read_json_object(iter_dir / "generation_summary.json")
    per_platform_files = summary.get("per_platform_files") if summary else None
    if isinstance(per_platform_files, dict):
        return [str(p) for p in per_platform_files if str(p).strip()]
    platforms = summary.get("platforms") if summary else None
    if isinstance(platforms, dict):
        return [str(p) for p in platforms if str(p).strip()]
    return _load_operator_supported_platforms(iter_dir)


def _resolve_generated_case_path(iter_dir: Path, raw_path: Any) -> Path | None:
    """Resolve old absolute/project-relative/per-iteration summary paths."""
    if not raw_path:
        return None
    path = Path(str(raw_path))
    candidates = [path] if path.is_absolute() else [ROOT / path, iter_dir / path, iter_dir / path.name]
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def _execution_file_mapping(summary: dict[str, Any]) -> dict[str, Any]:
    """Prefer adapter-materialized files while retaining old-run compatibility."""
    execution_files = summary.get("per_platform_execution_files")
    if isinstance(execution_files, dict):
        return execution_files
    raw_files = summary.get("per_platform_files")
    return raw_files if isinstance(raw_files, dict) else {}


def _validate_generation_contract(
    iter_dir: Path,
    summary: dict[str, Any],
) -> None:
    status_path = iter_dir / "generation_status.json"
    status = _read_json_object(status_path)
    if status is not None and status.get("state") != "complete":
        raise RuntimeError(
            "TTK generation is incomplete; rerun GENERATE before EXECUTE "
            f"(status={status.get('state')!r})"
        )

    expected_parent = iter_dir.resolve()
    for field in ("intermediate_model", "output"):
        raw_path = summary.get(field)
        if not raw_path:
            continue
        resolved = _resolve_generated_case_path(iter_dir, raw_path)
        if resolved is not None and resolved.parent != expected_parent:
            raise RuntimeError(
                f"generation_summary.{field} points outside the current "
                f"iteration: {resolved}"
            )


def _qli_csv_int64_issues(cases_path: Path) -> list[str]:
    """Detect the exact float-rounding regression before remote execution."""
    issues: list[str] = []
    with cases_path.open(encoding="utf-8", newline="") as handle:
        for row_index, row in enumerate(csv.DictReader(handle)):
            if row.get("api_name") != "torch_npu.npu_quant_lightning_indexer":
                continue
            try:
                attributes = ast.literal_eval(row.get("attributes") or "{}")
            except (SyntaxError, ValueError) as exc:
                issues.append(f"row {row_index}: malformed attributes ({exc})")
                continue
            for name in ("pre_tokens", "next_tokens"):
                value = attributes.get(name)
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value != 9_223_372_036_854_775_807
                ):
                    issues.append(
                        f"row {row_index}: {name} is not exact INT64_MAX "
                        f"({value!r})"
                    )
    return issues


def _ttk_e2e_refresh_reasons(
    cases_path: Path,
    selected_platform: str,
) -> list[str]:
    """Return reasons why canonical JSON/CSV must be rebuilt."""
    iter_dir = cases_path.parent
    summary = _read_json_object(iter_dir / "generation_summary.json")
    if not summary:
        raise RuntimeError("missing readable generation_summary.json")
    _validate_generation_contract(iter_dir, summary)

    execution_files = _execution_file_mapping(summary)
    if selected_platform not in execution_files:
        raise RuntimeError(
            f"no execution-ready cases for platform {selected_platform!r}; "
            f"available={list(execution_files)}"
        )
    source = _resolve_generated_case_path(
        iter_dir, execution_files[selected_platform],
    )
    if source is None:
        raise RuntimeError(
            "selected execution-ready cases file is missing: "
            f"{execution_files[selected_platform]!r}"
        )

    reasons: list[str] = []
    if summary.get("selected_platform") != selected_platform:
        reasons.append("selected platform changed")
    canonical = iter_dir / "cases.json"
    if not canonical.is_file():
        reasons.append("canonical cases.json is missing")
    elif canonical.read_bytes() != source.read_bytes():
        reasons.append("canonical cases.json differs from selected execution file")
    if not cases_path.is_file():
        reasons.append("TTK CSV is missing")
    elif cases_path.stat().st_mtime_ns < source.stat().st_mtime_ns:
        reasons.append("TTK CSV is older than selected execution file")

    hashes = summary.get("artifact_hashes")
    if isinstance(hashes, dict):
        expected = {
            "selected_execution_file": source,
            "cases_json": canonical,
            "cases_csv": cases_path,
        }
        for name, path in expected.items():
            recorded = hashes.get(name)
            if path.is_file() and recorded and _sha256_file(path) != recorded:
                reasons.append(f"{name} hash differs from generation summary")

    if cases_path.is_file():
        reasons.extend(_qli_csv_int64_issues(cases_path))
    constraints = _read_json_object(iter_dir / "constraints.json")
    if constraints:
        from scripts.atc_to_ttk import _ordered_input_tensor_names

        expected_tensor_order = _ordered_input_tensor_names(constraints)
        conversion_audit = _read_json_object(
            iter_dir / "ttk_conversion_audit.json"
        )
        recorded_tensor_order = (
            conversion_audit.get("tensor_order")
            if conversion_audit else None
        )
        if recorded_tensor_order != expected_tensor_order:
            reasons.append(
                "TTK CSV tensor order differs from current runtime ABI mapping"
            )
    return list(dict.fromkeys(reasons))


def _retarget_ttk_e2e_csv(
    cases_path: Path,
    selected_platform: str,
) -> dict[str, Any]:
    """Reuse an existing per-platform JSON and rebuild canonical JSON + E2E CSV."""
    iter_dir = cases_path.parent
    summary_path = iter_dir / "generation_summary.json"
    summary = _read_json_object(summary_path)
    if not summary:
        raise RuntimeError(f"missing readable generation summary: {summary_path}")
    _validate_generation_contract(iter_dir, summary)
    execution_files = _execution_file_mapping(summary)
    if selected_platform not in execution_files:
        raise RuntimeError(
            f"no execution-ready cases file for platform {selected_platform!r}; "
            f"available={list(execution_files)}"
        )
    source = _resolve_generated_case_path(
        iter_dir, execution_files[selected_platform]
    )
    if source is None:
        raise RuntimeError(
            "execution-ready cases path is missing for platform "
            f"{selected_platform!r}: {execution_files[selected_platform]!r}"
        )
    constraints_path = iter_dir / "constraints.json"
    constraints = _read_json_object(constraints_path)
    if not constraints:
        raise RuntimeError(f"missing readable constraints for TTK conversion: {constraints_path}")

    canonical_cases = iter_dir / "cases.json"
    _atomic_write_text(
        canonical_cases, source.read_text(encoding="utf-8"),
    )
    from scripts.atc_to_ttk import convert_file, _ordered_input_tensor_names

    temporary_csv = cases_path.with_name(f".{cases_path.name}.tmp")
    conversion = convert_file(
        canonical_cases,
        temporary_csv,
        selected_platform,
        _ordered_input_tensor_names(constraints),
    )
    int64_issues = _qli_csv_int64_issues(temporary_csv)
    if int64_issues:
        raise RuntimeError(
            "refusing to publish TTK CSV with invalid int64 attributes: "
            + "; ".join(int64_issues[:5])
        )
    temporary_csv.replace(cases_path)
    conversion["destination"] = str(cases_path)
    _atomic_write_json(iter_dir / "ttk_conversion_audit.json", conversion)
    previous_platform = summary.get("selected_platform")
    summary.update({
        "selected_platform": selected_platform,
        "platform_selection_reason": "execute_server_config_match",
        "intermediate_model": str(canonical_cases),
        "selected_execution_file": str(source),
        "output": str(cases_path),
        "total": conversion.get("case_count", summary.get("total")),
        "semantically_clean_count": conversion.get(
            "semantically_clean_count", summary.get("semantically_clean_count")
        ),
        "artifact_hashes": {
            "selected_execution_file": _sha256_file(source),
            "cases_json": _sha256_file(canonical_cases),
            "cases_csv": _sha256_file(cases_path),
        },
    })
    _atomic_write_json(summary_path, summary)
    _atomic_write_json(iter_dir / "generation_status.json", {
        "state": "complete",
        "selected_platform": selected_platform,
        "selected_execution_file": str(source),
        "cases_json": str(canonical_cases),
        "cases_csv": str(cases_path),
        "artifact_hashes": summary["artifact_hashes"],
    })
    return {
        "retargeted": True,
        "previous_platform": previous_platform,
        "selected_platform": selected_platform,
        "source": str(source),
        "case_count": conversion.get("case_count", 0),
    }


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
    parser = argparse.ArgumentParser(
        description=(
            "执行已生成的测试用例并写出 execution_result.json。"
            "默认 real 模式；显式 --mode mock 才回退到本地 Mock。"
            "real 模式不再自动生成 executor：必须先跑 --generate 产出 "
            "cases_executor.py + cases_expanded.json。通用模板需由 "
            "atc-cpu-golden-derivation skill 完成 CPU golden 推导；专属模板直接校验，"
            "通过后再以 real 上传执行。"
        )
    )
    parser.add_argument(
        "--mode",
        choices=("mock", "real"),
        default="real",
        help="执行模式 (默认 real)。real 仅上传+跑 atk，不再生成 executor。",
    )
    parser.add_argument(
        "--test-framework", choices=("atk", "ttk"), default="atk",
        help=(
            "执行框架；ttk 根据 CSV api_name 自动选择 ACLNN 模式或 "
            "torch_npu E2E 模式。"
        ),
    )
    golden_group = parser.add_mutually_exclusive_group()
    golden_group.add_argument(
        "--no-golden",
        action="store_true",
        dest="no_golden",
        help=(
            "TTK 专用：不加载算子 Golden；仍加载内部格式运行时插件。"
        ),
    )
    golden_group.add_argument(
        "--with-golden",
        action="store_false",
        dest="no_golden",
        help="TTK 专用：加载可用的自主推导或源码 Golden（默认）。",
    )
    parser.set_defaults(no_golden=False)
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
            "产出 cases_executor.py + cases_expanded.json 到 iter_dir。通用模板含 "
            "CPU golden TODO，需推导后执行；专属模板为完整实现，校验后直接执行。"
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
        "--proc-timeout",
        type=int,
        default=180,
        help=(
            "TTK ACLNN 单用例超时时间（秒）；0 表示不设置，默认 180。"
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
    parser.add_argument(
        "--hs-scenario-mode",
        choices=("original", "planned"),
        default=None,
        help=(
            "HS torch_npu 场景模式；TTK 执行阶段透传并记录，"
            "用例本身必须已按该模式生成。"
        ),
    )
    args = parser.parse_args()
    if args.proc_timeout < 0:
        parser.error("--proc-timeout 必须大于等于 0")

    cases_path = resolve_input_path(args.cases)
    output_path = resolve_input_path(args.output)
    if args.test_framework == "ttk":
        if cases_path.suffix.lower() != ".csv":
            _emit({"ok": False, "code": "TTK_CSV_REQUIRED", "message": "TTK 输入必须是 CSV。"})
            return 2
        try:
            ttk_mode, csv_api_name = _read_ttk_csv_identity(cases_path)
        except (OSError, ValueError) as exc:
            _emit({"ok": False, "code": "TTK_CSV_INVALID", "message": str(exc)})
            return 2

        if ttk_mode == "aclnn":
            from scripts.validate_ttk_aclnn_csv import validate_csv

            iter_dir = cases_path.parent
            golden_plugin = _resolve_aclnn_golden_plugin(
                iter_dir, csv_api_name, disabled=args.no_golden,
            )
            if args.no_golden:
                golden_resolution = "disabled"
            elif golden_plugin == iter_dir / "golden.py":
                golden_resolution = "iteration"
            elif golden_plugin is not None:
                golden_resolution = "registry"
            else:
                golden_resolution = "none"
            validation = validate_csv(cases_path)
            if not validation["valid"]:
                # 执行优先：本地严格校验只作诊断，不阻止把 CSV 交给真实 TTK。
                # 无法读取文件/识别 API 等基础错误已在上方拦截。
                print(
                    "[execute_cases] warning: TTK ACLNN CSV validation failed; "
                    "continuing to engine: "
                    + json.dumps(validation, ensure_ascii=False),
                    file=sys.stderr,
                )

            command = (
                f"python3 -m ttk aclnn -i {cases_path.name} "
                "--plat=<servers.json.ttk.plat> --warmup False"
            )
            if golden_plugin is not None:
                command += f" --plugin {golden_plugin.name}"
            if args.proc_timeout > 0:
                command += f" --proc-timeout {args.proc_timeout}"
            if args.generate:
                result = {
                    "status": "generate",
                    "mode": "ttk_aclnn",
                    "test_framework": "ttk",
                    "cases": str(cases_path),
                    "operator_name": args.operator or csv_api_name,
                    "ttk_command": command,
                    "validation_command": (
                        f"python3 -m ttk aclnn -i {cases_path.name} "
                        "--plat=<servers.json.ttk.plat> --validate"
                    ),
                    "golden_required": False,
                    "golden_plugin": (
                        str(golden_plugin) if golden_plugin is not None else None
                    ),
                    "golden_resolution": golden_resolution,
                    "proc_timeout": args.proc_timeout,
                    "precision_blocking": False,
                    "validation": validation,
                    "engine_error": None,
                }
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                _emit(result)
                return 0

            if args.mode == "mock":
                total = int(validation["row_count"])
                result = {
                    "status": "success",
                    "mode": "ttk_aclnn_mock",
                    "test_framework": "ttk",
                    "ttk_mode": "aclnn",
                    "operator_name": args.operator or csv_api_name,
                    "passed": total,
                    "failed": 0,
                    "total": total,
                    "records": [
                        {"case_id": index, "status": "PASS", "mock": True}
                        for index in range(total)
                    ],
                    "engine_error": "",
                    "validation": validation,
                }
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                _emit(result)
                return 0

            config_path, config_errors = validate_server_config(args.server_config)
            if config_errors:
                _emit(config_error_payload(config_path, config_errors))
                return 2
            servers = _load_server_config(config_path)
            ttk_servers = [
                server for server in servers if isinstance(server.get("ttk"), dict)
            ]
            if not ttk_servers:
                _emit({
                    "ok": False,
                    "code": "TTK_SERVER_CONFIG_REQUIRED",
                    "message": "servers.json 中没有包含 ttk 配置的服务器。",
                })
                return 2
            operator_platforms = _load_operator_supported_platforms(iter_dir)
            csv_platform = _load_ttk_selected_platform(iter_dir)
            if csv_platform:
                if args.platform and args.platform != csv_platform:
                    _emit({
                        "ok": False,
                        "code": "TTK_CSV_PLATFORM_MISMATCH",
                        "message": (
                            f"cases_ttk.csv 由平台 {csv_platform!r} 生成，不能用 "
                            f"--platform {args.platform!r} 执行。"
                        ),
                    })
                    return 2
                operator_platforms = [csv_platform]
            server, selected_platform, select_error = _select_server_for_execution(
                ttk_servers, args.platform, operator_platforms
            )
            if server is None:
                _emit({
                    "ok": False,
                    "code": "NO_SERVER_FOR_PLATFORM",
                    "message": select_error,
                })
                return 2
            artifact_dir = (
                resolve_input_path(args.artifact_dir)
                if args.artifact_dir
                else _attempt_artifact_dir(iter_dir, "ttk_aclnn")
            )
            from scripts.execute_ttk_aclnn import run_aclnn

            result = run_aclnn(
                csv_path=cases_path,
                server=server,
                artifact_dir=artifact_dir,
                mode="npu",
                timeout=1800.0,
                proc_timeout=args.proc_timeout,
                plugin_path=golden_plugin,
            )
            result.update({
                "test_framework": "ttk",
                "ttk_mode": "aclnn",
                "operator_name": args.operator or csv_api_name,
                "selected_platform": selected_platform,
                "golden_resolution": golden_resolution,
                "passed": int(result.get("npu_passed", 0)),
                "failed": int(result.get("npu_failed", 0)),
                "total": int(result.get("npu_total", 0)),
                "precision_passed": int(result.get("precision_passed", 0)),
                "precision_failed": int(result.get("precision_failed", 0)),
                "precision_blocking": False,
                "records": result.get("records", []),
                "engine_error": result.get("engine_error") or "",
            })
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _emit({key: result.get(key) for key in (
                "status", "passed", "failed", "total", "engine_error",
                "precision_passed", "precision_failed", "precision_blocking",
                "golden_plugin", "golden_resolution", "proc_timeout",
                "remote_dir", "results_csv",
            )})
            return 0 if result.get("status") == "success" else 2

        # TTK E2E CSV is platform-specific. Select the server/platform from all
        # generated buckets first; if generation chose a different first bucket,
        # reuse the matching per-platform JSON and rebuild cases.json + CSV.
        config_path, config_errors = validate_server_config(args.server_config)
        if config_errors:
            _emit(config_error_payload(config_path, config_errors))
            return 2
        servers = _load_server_config(config_path)
        iter_dir = cases_path.parent
        generated_platforms = _load_ttk_generated_platforms(iter_dir)
        server, selected_platform, select_error = _select_server_for_execution(
            servers, args.platform, generated_platforms
        )
        if server is None or selected_platform is None:
            _emit({
                "ok": False,
                "code": "NO_SERVER_FOR_PLATFORM",
                "message": select_error,
                "generated_platforms": generated_platforms,
            })
            return 2
        csv_platform = _load_ttk_selected_platform(iter_dir)
        retarget_info = {
            "retargeted": False,
            "previous_platform": csv_platform,
            "selected_platform": selected_platform,
            "refresh_reasons": [],
        }
        try:
            refresh_reasons = _ttk_e2e_refresh_reasons(
                cases_path, selected_platform,
            )
            if refresh_reasons:
                retarget_info = _retarget_ttk_e2e_csv(cases_path, selected_platform)
                retarget_info["refresh_reasons"] = refresh_reasons
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            _emit({
                "ok": False,
                "code": "TTK_ARTIFACT_REFRESH_FAILED",
                "message": str(exc),
                "previous_platform": csv_platform,
                "selected_platform": selected_platform,
            })
            return 2

        manifest_path = cases_path.parent / "golden_manifest.json"
        manifest = _read_json_object(manifest_path)
        # Resolve the golden plugin to upload. Prefer the operator's registered
        # golden under agent/hs/ttk_plugins/ (it carries the __golden__ e2e
        # function AND the allow_internal_format bootstrap). The per-iter
        # ttk_plugin.py is only a runtime bootstrap placeholder when the
        # manifest was pre-verified, so uploading it alone makes TTK report
        # "Scanned 0 custom golden.e2e functions" and skip precision comparison.
        plugin_path = None
        for candidate in (
            cases_path.parent / "ttk_golden_fia.py",
            cases_path.parent / "ttk_plugin.py",
        ):
            # Older runs may contain a runtime-only ttk_plugin.py.  Do not let
            # that shadow a registered operator golden; runtime initialization
            # is provided independently through sitecustomize below.
            if candidate.is_file() and "__golden__" in candidate.read_text(
                encoding="utf-8", errors="ignore"
            ):
                plugin_path = candidate
                break
        if plugin_path is None:
            manifest_plugin = (manifest or {}).get("plugin")
            repo_golden = (
                ROOT / "agent" / "hs" / "ttk_plugins" / manifest_plugin
                if manifest_plugin else None
            )
            if repo_golden is not None and repo_golden.is_file():
                plugin_path = repo_golden
            else:
                from agent.hs import resolve_ttk_plugin
                resolved = resolve_ttk_plugin(csv_api_name, golden=True)
                if resolved.name != "runtime_bootstrap.py" and resolved.is_file():
                    plugin_path = resolved
        no_golden = bool(getattr(args, "no_golden", False))
        if no_golden:
            plugin_path = None
        from agent.hs import resolve_ttk_plugin
        runtime_plugin_path = resolve_ttk_plugin(csv_api_name, golden=False)
        plugin_arg = f" --plugin {plugin_path.name}" if (plugin_path and plugin_path.is_file()) else ""
        command = f"python3 -m ttk e2e -i {cases_path.name}{plugin_arg}"
        if args.generate:
            result = {
            "status": "generate",
            "mode": "ttk_e2e",
            "test_framework": "ttk",
            "cases": str(cases_path),
            "ttk_command": command,
            "plugin": str(plugin_path) if (plugin_path and plugin_path.is_file()) else None,
            "runtime_plugin": str(runtime_plugin_path),
            "precision_blocking": False,
            "validation_command": f"python3 -m ttk e2e -i {cases_path.name} --validate",
            "selected_platform": selected_platform,
            "platform_retarget": retarget_info,
            "engine_error": None,
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            _emit(result)
            return 0

        with cases_path.open(encoding="utf-8", newline="") as handle:
            first = next(csv.DictReader(handle), {})
        operator_name = args.operator or first.get("api_name") or cases_path.stem
        conversion_audit = _read_json_object(
            iter_dir / "ttk_conversion_audit.json"
        )
        expected_tensor_order = (
            conversion_audit.get("tensor_order")
            if conversion_audit else None
        )
        if not isinstance(expected_tensor_order, list):
            expected_tensor_order = None
        artifact_dir = (
            resolve_input_path(args.artifact_dir) if args.artifact_dir
            else _attempt_artifact_dir(iter_dir, "ttk")
        )
        from executer.ttk_runner import run_ttk_remote
        result = run_ttk_remote(
            cases_path=cases_path,
            plugin_path=plugin_path if (plugin_path and plugin_path.is_file()) else None,
            runtime_plugin_path=runtime_plugin_path,
            operator_name=operator_name,
            server=server,
            artifact_dir=artifact_dir,
            timeout=1800.0,
            expected_tensor_order=expected_tensor_order,
        )
        result.update({
            "selected_platform": selected_platform,
            "hs_scenario_mode": args.hs_scenario_mode,
            "platform_retarget": retarget_info,
            "input_artifacts": _artifact_fingerprints(
                iter_dir, cases_path
            ),
        })
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        _emit({key: result.get(key) for key in (
            "status", "passed", "failed", "total", "engine_error",
            "remote_output_dir", "results_csv")})
        return 0 if result.get("status") == "success" else 2
    from executer import run_cases  # noqa: WPS433  (ATK path needs asyncssh)
    from executer.runner import (  # noqa: WPS433
        RunRequest,
        load_cases_payload,
        validate_server_info,
    )
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
