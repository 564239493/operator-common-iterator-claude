"""Top-level entry point for the project-local executer.

Mirrors the responsibilities of the reference ``executer.run_atk`` node —
SSH connect → SFTP upload cases + executor → run ``atk node --backend cpu
task`` → discover & download report → parse xlsx → return a flat
``execution_result.json``-shaped dict.

Differences from the reference:

* The LLM ``exec_generate_atk`` step that *produced* the ATK executor
  file is replaced by the deterministic :mod:`generators.generator`
  shipped under ``executer/resources/``.  Both the operator signature
  table (``aclnn_extracted.txt``) and the code-generation script
  (``generator.py``) live next to the executer.
* The LLM ``exec_cpu_derivation`` step is dropped from Python entirely.
  The CPU golden derivation prompt from the reference has been promoted
  to a Claude *skill* at ``.claude/skills/atc-cpu-golden-derivation/SKILL.md``;
  Python only does deterministic actions (per ``CLAUDE.md``).
* With the LLM step gone, the ``ChatOpenAI`` /
  ``Settings(active_api_key=...)`` import chain is gone too — no more
  ``ZAI_API_KEY`` placeholder blocking the EXECUTE stage.
* Uses :mod:`executer.ssh` and :mod:`executer.report_parser` exclusively
  — never reaches into ``operator-agent`` or ``operator-common-iterator``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ExecutionResult
from .report_parser import parse_xlsx_report
from .ssh import (
    CommandResult,
    SSHEngineError,
    ServerEndpoint,
    connect,
    find_latest_output_dir,
    run,
    sftp_download_file,
    sftp_list_dir,
    sftp_upload,
)

logger = logging.getLogger(__name__)

# ── Remote layout (project-local constants) ───────────────────────────────

_REMOTE_HOME = "/home/operator_atk"
_REMOTE_CASES_DIR = f"{_REMOTE_HOME}/cases"
_REMOTE_EXECUTOR_DIR = f"{_REMOTE_HOME}/atk_executor"
_REMOTE_OUTPUT_ROOT = f"{_REMOTE_HOME}/atk_output"

_DEFAULT_ENV_INIT = "cd /home/operator_atk && source /home/marine/miniconda3/etc/profile.d/conda.sh && conda activate atk_env"
_DEFAULT_ATK_TIMEOUT = 1800.0

# ── Local generator assets (mirrored from operator-common-iterator) ───────

_RESOURCES_DIR = Path(__file__).resolve().parent / "resources"
_GENERATOR_SCRIPT = _RESOURCES_DIR / "generator.py"
_SIGNATURES_FILE = _RESOURCES_DIR / "aclnn_extracted.txt"

_GENERATOR_TIMEOUT = 60.0


# ── Configuration validation ──────────────────────────────────────────────


@dataclass(frozen=True)
class RunRequest:
    """Validated inputs for :func:`run_cases` real mode."""

    cases_path: Path
    server_info: dict[str, Any]
    operator_name: str
    run_id: str
    artifact_dir: Path
    project_root: Path
    task_type: str = "accuracy"
    env_init: str | None = None
    atk_timeout: float = _DEFAULT_ATK_TIMEOUT
    iter_dir: Path | None = None  # runs/<run-id>/iter_NNN — used to find constraints.json + generation_summary.json for platform filtering


def _resolve_env_init(value: str | None) -> str:
    if value and value.strip():
        return value.strip()
    return _DEFAULT_ENV_INIT


def _looks_like_placeholder(value: Any) -> bool:
    """Heuristic: detect unfilled template values in ``server_info``."""
    if not value:
        return True
    text = str(value).strip().lower()
    if not text:
        return True
    return any(
        marker in text
        for marker in ("replace-me", "your-", "<", "todo", "changeme")
    )


def validate_server_info(
    server_info: dict[str, Any] | None,
    *,
    strict: bool = True,
) -> str | None:
    """Return ``None`` if usable, else a short user-facing error message.

    Mirrors the runtime guard from ``scripts/runtime_config.py`` but runs
    locally so the executer is self-contained.  Strict-check on
    ``password`` placeholder — that's exactly what tripped the previous
    ``environment-blocked (ZAI_API_KEY 占位符未替换)`` style failure on
    the old cross-project path.

    When ``strict=False`` (preflight mode), password placeholder values
    are tolerated — the caller won't open an SSH connection, so it only
    needs the field to be *present* (for platform selection), not real.
    """
    if not isinstance(server_info, dict):
        return "server_info 缺失或不是 JSON object"
    for key in ("ip", "username", "password"):
        if not str(server_info.get(key) or "").strip():
            return f"server_info.{key} 必填且不能为空"
    if strict and _looks_like_placeholder(server_info.get("password")):
        return (
            "server_info.password 仍为占位符 (replace-me / your-...), "
            "请在 servers.json 中填写真实口令后再执行。"
        )
    if not isinstance(server_info.get("platforms"), list) or not server_info[
        "platforms"
    ]:
        return "server_info.platforms 必须是非空数组"
    return None


def pick_server(
    servers: list[dict[str, Any]], platform: str
) -> dict[str, Any] | None:
    """Pick the first server whose ``platforms`` list contains ``platform``.

    Falls back to the first server if none match exactly — the original
    project assumes one Atlas A3 development host.
    """
    if not servers:
        return None
    for server in servers:
        if platform in server.get("platforms", []):
            return server
    return servers[0] if servers else None


# ── Path helpers ───────────────────────────────────────────────────────────


def _remote_cases_path(operator_name: str) -> str:
    """Remote path for the *expanded* cases.json consumed by ATK.

    Generator writes ``cases_expanded.json`` at iter root; we upload
    that to the host (NOT the raw ``cases.json``), so the remote file
    ends in ``_cases_expanded.json``.
    """
    return (
        f"{_REMOTE_CASES_DIR}/{operator_name}_cases_expanded.json"
    )


def _remote_executor_path(operator_name: str) -> str:
    return f"{_REMOTE_EXECUTOR_DIR}/{operator_name}_executor.py"


def _build_atk_command(
    operator_name: str,
    task_type: str,
    env_init: str,
) -> str:
    """Compose ``atk node --backend cpu task ...`` for the remote host."""
    cases_remote = _remote_cases_path(operator_name)
    executor_remote = _remote_executor_path(operator_name)
    return (
        f"{env_init} && "
        f"atk node --backend cpu task "
        f"-c {cases_remote} "
        f"-p {executor_remote} "
        f"--task {task_type} "
        f"--bind_cpu_type BIND_IN_PHYSICAL"
    )


def _safe_operator(value: str) -> str:
    """Make ``operator_name`` shell-safe for use in remote paths."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value).strip("_") or "operator"
    return cleaned


def _resolve_cache_dir(
    req: RunRequest, operator_name: str
) -> Path:
    """ATK artifact staging dir.

    The CLI passes ``RunRequest.artifact_dir``; under the project's
    artifact contract this is ``runs/<run-id>/iter_NNN/execution_logs/``,
    so ATK log / xlsx report land next to the iter's
    ``execution_result.json``.  Falls back to ``<project_root>/execution_results/...``
    only for ad-hoc invocations that bypass the iter layout.
    """
    if req.artifact_dir:
        cache = req.artifact_dir
    else:
        safe_operator = _safe_operator(operator_name)
        safe_run = re.sub(r"[^A-Za-z0-9_.-]", "_", req.run_id)[:48] or "run"
        cache = (
            req.project_root
            / "execution_results"
            / safe_operator
            / safe_run
        )
    cache.mkdir(parents=True, exist_ok=True)
    return cache


# ── ATK executor generation ────────────────────────────────────────────────


def filter_cases_by_platform(
    cases: list[dict[str, Any]],
    product_support: list[str],
    platforms_count: dict[str, int],
    server_platforms: list[str],
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Keep only the cases whose slice matches a server-supported platform.

    ``cases.json`` from ``scripts/generate_cases.py`` interleaves all
    supported platforms in :data:`product_support` order — first
    ``platforms_count[p]`` cases belong to the first product, and so on.
    The generator emits the xlsx faithfully, so a 3-product operator
    produces 30 cases even though a given execution server only supports
    one of them.  This helper slices that matrix down to the platforms
    the chosen server actually supports.

    Returns ``(filtered_cases, None)`` on success or ``(None, message)``
    when the server's platforms don't intersect with the operator's
    product_support list (callers should surface the message as
    ``engine_error`` — never as a fake case failure).
    """
    if not isinstance(server_platforms, list) or not server_platforms:
        return None, "server_info.platforms 为空, 无法按平台过滤"

    matching = [p for p in product_support if p in server_platforms]
    if not matching:
        return None, (
            "服务器平台与算子 product_support 没有交集: "
            f"server={server_platforms}, operator={list(product_support)}"
        )

    out: list[dict[str, Any]] = []
    cursor = 0
    for platform in product_support:
        count = int(platforms_count.get(platform, 0))
        chunk = cases[cursor:cursor + count]
        if platform in matching:
            out.extend(chunk)
        cursor += count

    return out, None


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    """Best-effort JSON reader — returns None for missing / malformed files.

    Cases files coming from external sources (the project contract is one
    run_dir/iter_NNN/ but the CLI may pass a path elsewhere) —
    we should never crash the orchestrator on a missing summary file.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _platform_to_cases_filename(platform: str) -> str:
    """``Atlas A2 ... / Atlas A2 ...`` → ``cases_Atlas A2 ... _Atlas A2 ....json``.

    Per-platform cases filename mirrors ``scripts/generate_cases.py``:
    replace ``/`` with ``_`` (only char illegal in Windows filenames);
    spaces, Chinese, ``_`` etc. survive intact because ``Path`` handles
    them and we never shell-quote these names on the remote side.
    """
    safe = platform.replace("/", "_")
    return f"cases_{safe}.json"


async def _resolve_iter_cases_for_server(
    req: RunRequest,
) -> tuple[Path | None, str | None]:
    """Pick the per-platform ``cases_<platform>.json`` matching the server.

    Generation writes one file per ``product_support`` entry, named
    ``cases_<sanitized_platform>.json``.  This helper scans the iter_dir
    and copies the matching one to ``iter_dir/cases.json`` so the
    downstream tools (generator.py → atk command) only ever see a file
    called ``cases.json``.  The copied ``cases.json`` is what generator
    expands into ``cases_expanded.json`` for SFTP-upload.

    Returns ``(cases_path, error)``.  When ``cases_path`` is non-None,
    the executor should consume it as if it were the original
    ``cases.json``.
    """
    iter_dir = req.iter_dir or req.cases_path.parent

    server_platforms = req.server_info.get("platforms") or []
    if not isinstance(server_platforms, list) or not server_platforms:
        return None, "server_info.platforms 为空, 无法选择产品用例"

    # Try each server platform (in priority order) until we find a file.
    for platform in server_platforms:
        candidate = iter_dir / _platform_to_cases_filename(platform)
        if candidate.is_file():
            target = iter_dir / "cases.json"
            target.write_bytes(candidate.read_bytes())
            logger.info(
                "platform_select: %s -> iter_dir/cases.json (%d bytes)",
                candidate.name,
                target.stat().st_size,
            )
            return target, None

    # Fallback: legacy layout — single combined ``cases.json`` in iter_dir.
    legacy = iter_dir / "cases.json"
    if legacy.is_file() and legacy != req.cases_path:
        logger.info(
            "platform_select: no per-platform file, using legacy %s",
            legacy,
        )
        return legacy, None

    available = sorted(p.name for p in iter_dir.glob("cases_*.json"))
    return None, (
        "未找到匹配 server.platforms={} 的产品用例文件；"
        "iter_dir 下可用的有: {}".format(
            server_platforms, available or "(none)"
        )
    )


def _run_generator_blocking(cmd: list[str]) -> tuple[int, str, str]:
    """Synchronous wrapper for ``subprocess.run`` (called via thread pool).

    The reference's ``exec_generate_atk`` used ``subprocess.run`` with
    ``encoding="utf-8", errors="replace"`` — that choice is mandatory on
    Windows: ``text=True`` alone defaults to ``locale.getpreferredencoding``
    (cp1252) and the internal reader will raise ``UnicodeDecodeError`` the
    moment the child writes a UTF-8 byte it can't decode.
    """
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GENERATOR_TIMEOUT,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _resolve_generated_executors(output_dir: Path, stem: str) -> list[Path]:
    """Find executor files emitted by ``generator.py``.

    Generator writes either ``{stem}.py`` (single-op pass) or
    ``{stem}_{op_name}.py`` (multi-op).  We pick up every match in the
    output dir so the SSH step can ship whichever the ATK framework needs.
    """
    candidates = sorted(output_dir.glob(f"{stem}*.py"))
    return candidates


async def _generate_atk_executor(
    req: RunRequest,
    work_dir: Path,
) -> dict[str, Any]:
    """Run ``generator.py`` locally to produce the per-operator executor.

    Returns a dict with keys ``executor_files`` and ``expanded_cases``.
    Both are local Paths; the caller is responsible for SFTP-upload.
    Surfaced as an :class:`SSHEngineError` so it integrates with the same
    fail-fast envelope — generator failures are not case failures.
    """
    if not _GENERATOR_SCRIPT.is_file():
        raise SSHEngineError(
            f"executor generator script missing: {_GENERATOR_SCRIPT}"
        )
    if not _SIGNATURES_FILE.is_file():
        raise SSHEngineError(
            f"signature table missing: {_SIGNATURES_FILE}"
        )

    work_dir.mkdir(parents=True, exist_ok=True)

    # Generator uses the cases.json basename for its outputs (e.g.
    # cases.json → cases_expanded.json + cases_<op>.py).  Stage the
    # copy + outputs in ``work_dir`` so they sit alongside the original
    # cases.json at iter root (per the project's artifact layout).
    stem = req.cases_path.stem  # e.g. "cases"
    cases_copy = work_dir / f"{stem}.json"
    cases_copy.write_bytes(req.cases_path.read_bytes())

    output_target = work_dir / f"{stem}_executor.py"

    cmd = [
        sys.executable,
        str(_GENERATOR_SCRIPT),
        str(cases_copy),
        "-o",
        str(output_target),
        "--signatures",
        str(_SIGNATURES_FILE),
    ]

    try:
        returncode, stdout, stderr = await asyncio.to_thread(
            _run_generator_blocking, cmd
        )
    except subprocess.TimeoutExpired as exc:
        raise SSHEngineError(
            f"generator.py 超时 ({_GENERATOR_TIMEOUT}s) — cases.json 过大?"
        ) from exc

    if returncode != 0:
        raise SSHEngineError(
            f"generator.py 退出码 {returncode}; stderr={stderr.strip() or '(empty)'}"
        )

    executor_files = _resolve_generated_executors(work_dir, stem)
    if not executor_files:
        raise SSHEngineError(
            "generator.py 未生成任何 executor .py 文件 — "
            "请确认 cases.json 含 aclnn_name 且在 aclnn_extracted.txt 中能查到签名。"
        )

    expanded = work_dir / f"{stem}_expanded.json"
    if not expanded.is_file():
        candidates = sorted(work_dir.glob(f"{stem}_expanded*.json"))
        if not candidates:
            raise SSHEngineError(
                f"generator.py 未写出 expanded cases JSON; stdout={stdout.strip() or '(empty)'}"
            )
        expanded = candidates[0]

    logger.info(
        "generate_atk: produced %d executor file(s) + %s -> %s",
        len(executor_files),
        expanded.name,
        work_dir,
    )

    return {
        "executor_files": executor_files,
        "expanded_cases": expanded,
        "generator_stdout": stdout,
    }


# ── Execution logging ───────────────────────────────────────────────────────


def _setup_execution_log(log_dir: Path) -> logging.FileHandler | None:
    """Add a file handler so every run leaves a persistent audit trail.

    The handler writes to ``<log_dir>/execution.log`` (UTF-8, append mode
    if the file already exists).  Returns the handler so the caller can
    remove it after the run completes — keeps the logger clean for the
    next invocation.
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    log_path = log_dir / "execution.log"
    try:
        handler = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s | %(message)s"
            )
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        return handler
    except OSError as exc:
        logger.warning("cannot open execution log %s: %s", log_path, exc)
        return None


def _cleanup_log_handler(handler: logging.FileHandler | None) -> None:
    """Remove and close a log handler installed by :func:`_setup_execution_log`."""
    if handler is not None:
        try:
            logger.removeHandler(handler)
            handler.close()
        except Exception:
            pass  # best-effort cleanup


# ── Orchestrator ───────────────────────────────────────────────────────────


async def _execute_preflight(req: RunRequest) -> ExecutionResult:
    """Run the local-only preparation steps of :class:`RunRequest`.

    Skips SSH / ATK / report parsing — stops after generator.py writes
    ``cases_expanded.json`` and ``cases_<op>_executor.py`` to iter_dir.
    The user can SFTP-upload those files manually and run the exact atk
    command surfaced in ``scripts/execute_cases.py --preflight``.

    Used to validate the executor pipeline without a remote host
    available.  ``status="preflight"`` so callers can distinguish from
    real outcomes.
    """
    operator_name = _safe_operator(req.operator_name)
    result = ExecutionResult(status="preflight")
    overall_start = time.monotonic()

    log_dir = req.iter_dir or _resolve_cache_dir(req, operator_name)
    log_handler = _setup_execution_log(log_dir)
    logger.info(
        "===== preflight start: operator=%s run_id=%s =====",
        operator_name,
        req.run_id,
    )

    try:
        # Preflight only needs field presence + valid platforms — it won't
        # open an SSH connection, so placeholder passwords are tolerated.
        server_error = validate_server_info(req.server_info, strict=False)
        if server_error:
            result.status = "error"
            result.error_message = server_error
            result.duration = time.monotonic() - overall_start
            return result

        cache_dir = _resolve_cache_dir(req, operator_name)

        # Platform selection: same as _execute_real section 1.
        scoped_cases_path, select_error = await _resolve_iter_cases_for_server(req)
        if scoped_cases_path is None:
            logger.error("preflight: %s", select_error)
            result.status = "error"
            result.error_message = select_error or "无法选择产品用例文件"
            result.duration = time.monotonic() - overall_start
            return result

        scoped_request = RunRequest(
            cases_path=scoped_cases_path,
            server_info=req.server_info,
            operator_name=req.operator_name,
            run_id=req.run_id,
            artifact_dir=req.artifact_dir,
            project_root=req.project_root,
            task_type=req.task_type,
            env_init=req.env_init,
            atk_timeout=req.atk_timeout,
            iter_dir=req.iter_dir,
        )

        generator_work_dir = req.iter_dir or cache_dir

        try:
            generated = await _generate_atk_executor(
                scoped_request, generator_work_dir
            )
        except SSHEngineError as exc:
            logger.exception(
                "preflight: ATK executor generation failed for %s",
                operator_name,
            )
            result.status = "error"
            result.error_message = f"生成 ATK executor 失败: {exc}"
            result.duration = time.monotonic() - overall_start
            return result

        executor_files = generated["executor_files"]
        expanded_cases = generated["expanded_cases"]

        atk_command = _build_atk_command(
            operator_name, req.task_type, _resolve_env_init(req.env_init)
        )
        remote_paths = {
            "cases_expanded.json": _remote_cases_path(operator_name),
            "cases_executor.py": _remote_executor_path(operator_name),
        }

        # Only list files the user needs to SFTP-upload.  ``cases.json`` is
        # the intermediate source for generator.py and stays local — it is
        # NOT consumed by ATK on the remote host.
        result.set_preflight_artifacts(
            {
                "cases_expanded.json": expanded_cases,
                "cases_executor.py": executor_files[0],
            },
            atk_command=atk_command,
            remote_paths=remote_paths,
        )
        logger.info(
            "preflight: source cases.json = %s", scoped_cases_path
        )
        if len(executor_files) > 1:
            logger.warning(
                "preflight: generator produced %d executor files; "
                "the first (operator-prefixed) is the one ATK consumes.",
                len(executor_files),
            )

        result.duration = time.monotonic() - overall_start
        logger.info(
            "preflight: ok for %s (%.2fs) -> %s",
            operator_name,
            result.duration,
            generator_work_dir,
        )
        for label, path in result._preflight_artifacts.items():  # type: ignore[union-attr]
            logger.info("  %s -> %s", label, path)
        logger.info("preflight atk command: %s", atk_command)
        logger.info(
            "===== preflight done: operator=%s status=%s duration=%.2fs =====",
            operator_name,
            result.status,
            result.duration,
        )
        return result
    finally:
        _cleanup_log_handler(log_handler)


async def _execute_real(req: RunRequest) -> ExecutionResult:
    """Run :class:`RunRequest` end-to-end.  Returns — never raises."""
    operator_name = _safe_operator(req.operator_name)
    result = ExecutionResult()
    overall_start = time.monotonic()

    # --- execution audit log ------------------------------------------------
    log_dir = req.iter_dir or _resolve_cache_dir(req, operator_name)
    log_handler = _setup_execution_log(log_dir)
    logger.info(
        "===== execute start: operator=%s run_id=%s mode=real =====",
        operator_name,
        req.run_id,
    )

    server_error = validate_server_info(req.server_info)
    if server_error:
        _cleanup_log_handler(log_handler)
        result.status = "error"
        result.error_message = server_error
        result.duration = time.monotonic() - overall_start
        return result

    endpoint = ServerEndpoint.from_server_row(req.server_info)
    cache_dir = _resolve_cache_dir(req, operator_name)
    env_init = _resolve_env_init(req.env_init)

    logger.info(
        "execute_cases: operator=%s server=%s task=%s",
        operator_name,
        endpoint.host,
        req.task_type,
    )

    # ── 1. Pick the per-platform cases file matching the server ─────────
    # Generation writes one ``cases_<platform>.json`` per product_support
    # entry; we copy the matching one to ``iter_dir/cases.json`` so the
    # downstream pipeline (generator.py → ATK) sees a single canonical
    # input file.  Without this, ATK would still consume only the server's
    # platform subset, but the xlsx's ``total`` would be 10 instead of 30
    # without an obvious reason, and the executor would have no place to
    # write the platform-annotated ATK report.
    scoped_cases_path, select_error = await _resolve_iter_cases_for_server(req)
    if scoped_cases_path is None:
        logger.error(
            "execute_cases: per-platform cases selection failed: %s",
            select_error,
        )
        result.status = "error"
        result.error_message = select_error or "无法选择产品用例文件"
        result.duration = time.monotonic() - overall_start
        _cleanup_log_handler(log_handler)
        return result

    scoped_request = RunRequest(
        cases_path=scoped_cases_path,
        server_info=req.server_info,
        operator_name=req.operator_name,
        run_id=req.run_id,
        artifact_dir=req.artifact_dir,
        project_root=req.project_root,
        task_type=req.task_type,
        env_init=req.env_init,
        atk_timeout=req.atk_timeout,
        iter_dir=req.iter_dir,
    )

    # ── 2. Generate per-operator ATK executor (deterministic) ─────────
    # Runs locally before SSH so a missing signature is surfaced as
    # engine_error instead of wasting a remote connection.  Generator
    # outputs (cases_expanded.json, cases_<op>_executor.py) are pure
    # rebuilds of inputs that already live at iter root — write them
    # there, NOT inside execution_logs (which is reserved for raw ATK
    # artifacts downloaded from the remote host).
    generator_work_dir = req.iter_dir or cache_dir
    try:
        generated = await _generate_atk_executor(
            scoped_request, generator_work_dir
        )
    except SSHEngineError as exc:
        logger.exception(
            "execute_cases: ATK executor generation failed for %s",
            operator_name,
        )
        result.status = "error"
        result.error_message = f"生成 ATK executor 失败: {exc}"
        result.duration = time.monotonic() - overall_start
        _cleanup_log_handler(log_handler)
        return result

    # ── 3. Connect ────────────────────────────────────────────────────
    try:
        conn = await connect(endpoint, timeout=30.0)
    except SSHEngineError as exc:
        logger.exception(
            "execute_cases: SSH connect failed for %s", operator_name
        )
        result.status = "error"
        result.error_message = f"SSH 连接失败: {exc}"
        result.duration = time.monotonic() - overall_start
        _cleanup_log_handler(log_handler)
        return result

    try:
        # ── 4. SFTP upload cases + executors ───────────────────────────
        try:
            await sftp_upload(
                conn,
                str(generated["expanded_cases"]),
                _remote_cases_path(operator_name),
            )
            # Generator may emit multiple files (multi-op); ATK only
            # consumes the operator_name-prefixed one — use the first.
            await sftp_upload(
                conn,
                str(generated["executor_files"][0]),
                _remote_executor_path(operator_name),
            )
        except SSHEngineError as exc:
            logger.exception(
                "execute_cases: SFTP upload failed for %s", operator_name
            )
            result.status = "error"
            result.error_message = f"SFTP 上传失败: {exc}"
            result.duration = time.monotonic() - overall_start
            _cleanup_log_handler(log_handler)
            return result

        # ── 5. Run atk command ─────────────────────────────────────────
        cmd = _build_atk_command(operator_name, req.task_type, env_init)
        logger.info("execute_cases: running %s", cmd)

        timed_out = False
        cmd_result: CommandResult | None = None
        try:
            cmd_result = await run(conn, cmd, timeout=req.atk_timeout)
        except SSHEngineError as exc:
            if "超时" in str(exc):
                timed_out = True
                cmd_result = CommandResult(
                    exit_code=-1,
                    stdout="",
                    stderr=str(exc),
                    duration=req.atk_timeout,
                )
                logger.warning(
                    "execute_cases: remote atk command timed out for %s",
                    operator_name,
                )
            else:
                logger.exception(
                    "execute_cases: remote atk command failed for %s",
                    operator_name,
                )
                result.status = "error"
                result.error_message = str(exc)
                result.duration = time.monotonic() - overall_start
                _cleanup_log_handler(log_handler)
                return result

        assert cmd_result is not None  # for type-checkers
        result.exit_code = cmd_result.exit_code
        result.stdout = cmd_result.stdout
        result.stderr = cmd_result.stderr
        if timed_out:
            result.status = "timeout"
        elif cmd_result.exit_code == 0:
            result.status = "success"
        else:
            result.status = "failed"

        # ── 6. Discover + download + parse outputs ─────────────────────
        try:
            output_dir = await find_latest_output_dir(
                conn, _REMOTE_OUTPUT_ROOT, operator_name
            )
        except SSHEngineError as exc:
            logger.warning("execute_cases: listdir failed: %s", exc)
            output_dir = None

        result.remote_output_dir = output_dir

        if output_dir:
            remote_report_dir = f"{output_dir}/report"
            remote_log_path = f"{output_dir}/log/atk.log"

            local_log_path = cache_dir / "atk.log"
            remote_entries = await sftp_list_dir(conn, remote_report_dir)
            for entry in remote_entries:
                await sftp_download_file(
                    conn, f"{remote_report_dir}/{entry}", cache_dir / entry
                )
            await sftp_download_file(conn, remote_log_path, local_log_path)

            report_data = parse_xlsx_report(cache_dir)
            result.task_report_data = report_data

            log_content = ""
            if local_log_path.exists():
                try:
                    log_content = local_log_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    if len(log_content) > 200_000:
                        log_content = (
                            log_content[:200_000]
                            + "\n... [log truncated]"
                        )
                except Exception as exc:
                    logger.warning(
                        "execute_cases: failed to read atk.log: %s", exc
                    )
            result.log_content = log_content

            if report_data.parse_error:
                logger.warning(
                    "execute_cases: report parse error for %s: %s",
                    operator_name,
                    report_data.parse_error,
                )
            logger.info(
                "execute_cases: extracted %d records (%d passed / %d failed) for %s",
                report_data.record_count,
                report_data.passed,
                report_data.failed,
                operator_name,
            )
        else:
            logger.warning(
                "execute_cases: no output dir under %s for %s",
                _REMOTE_OUTPUT_ROOT,
                operator_name,
            )
            result.error_message = (
                f"未找到 {operator_name}_ 前缀的输出目录 "
                f"({_REMOTE_OUTPUT_ROOT})"
            )

        # ── 7. Final classification ────────────────────────────────────
        if (
            result.status == "failed"
            and not result.task_report_data.report_records
        ):
            if not result.error_message:
                result.error_message = (
                    f"atk 命令退出码={result.exit_code}, 且未解析出任何用例记录"
                )
            # If we never even found an output dir, treat it as engine error
            # so downstream consumers don't masquerade as business failure.
            if not output_dir:
                result.status = "error"

    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover — cleanup best effort
            pass

    result.duration = time.monotonic() - overall_start

    # Persist a sibling result.json so the per-run cache dir carries
    # everything an operator needs to inspect / replay without re-running.
    try:
        (cache_dir / "result.json").write_text(
            result.model_dump_json(indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # pragma: no cover — best effort
        logger.warning(
            "execute_cases: failed to write result.json: %s", exc
        )

    logger.info(
        "===== execute done: operator=%s status=%s passed=%d failed=%d duration=%.2fs =====",
        operator_name,
        result.status,
        result.task_report_data.passed,
        result.task_report_data.failed,
        result.duration,
    )
    _cleanup_log_handler(log_handler)
    return result


# ── Public API ─────────────────────────────────────────────────────────────


def _mock_execute(cases: list[dict[str, Any]], fail_every: int) -> ExecutionResult:
    """Deterministic local mock — same contract as the CLI's mock mode."""
    records = []
    for index, case in enumerate(cases, start=1):
        failed = fail_every > 0 and index % fail_every == 0
        records.append(
            {
                "id": str(case.get("id") if isinstance(case, dict) else index),
                "run_result": "fail" if failed else "pass",
                "failure_reason": (
                    "MOCK_CONSTRAINT_MISMATCH: deterministic diagnostic failure"
                    if failed
                    else ""
                ),
                "case_json": case,
            }
        )
    passed = sum(item["run_result"] == "pass" for item in records)
    failed = len(records) - passed
    result = ExecutionResult(status="success" if failed == 0 else "failed")
    result.task_report_data.record_count = len(records)
    result.task_report_data.passed = passed
    result.task_report_data.failed = failed
    payload_records: list = []
    from .models import ReportRecord

    for item in records:
        payload_records.append(
            ReportRecord(
                id=item["id"],
                run_result=item["run_result"],
                failure_reason=item["failure_reason"],
                case_json=item["case_json"],
            )
        )
    result.task_report_data.report_records = payload_records
    return result


def run_cases(
    mode: str,
    cases: list[dict[str, Any]],
    *,
    request: RunRequest | None = None,
    fail_every: int = 3,
) -> dict[str, Any]:
    """Drive mock or real mode and return the flat ``execution_result.json``.

    Parameters
    ----------
    mode:
        ``"mock"`` runs deterministic local data; ``"real"`` requires
        :class:`RunRequest` and opens an SSH connection.
    cases:
        The case list (a Python object, not a path).  Used by mock mode;
        real mode reads from ``request.cases_path``.
    request:
        Required for ``mode=="real"``.
    fail_every:
        Mock only — every Nth case is marked fail for round-trip testing.

    Returns
    -------
    dict
        Conforming to the project-wide artifact contract — keys include
        ``status``, ``mode``, ``passed``, ``failed``, ``total``, ``records``,
        ``engine_error``.  Validation runs ``scripts/validate_artifacts.py
        execution <output>`` against this payload.
    """
    if mode == "mock":
        result = _mock_execute(cases, fail_every=fail_every)
        payload = result.to_flat()
        payload["mode"] = "mock"
        return payload

    if mode not in ("real", "preflight"):
        return {
            "status": "error",
            "mode": mode,
            "passed": 0,
            "failed": 0,
            "total": 0,
            "records": [],
            "engine_error": f"未知执行模式: {mode!r}; 仅支持 mock / real / preflight。",
        }

    if request is None:
        return {
            "status": "error",
            "mode": mode,
            "passed": 0,
            "failed": 0,
            "total": 0,
            "records": [],
            "engine_error": (
                "real/preflight 模式必须通过 RunRequest 提供 "
                "cases_path 和 server_info。"
            ),
        }

    if mode == "preflight":
        try:
            result = asyncio.run(_execute_preflight(request))
        except Exception as exc:
            logger.exception("execute_cases: preflight crashed")
            return {
                "status": "error",
                "mode": "preflight",
                "passed": 0,
                "failed": 0,
                "total": 0,
                "records": [],
                "engine_error": f"preflight 捕获未处理异常: {exc}",
            }
        payload = result.to_flat()
        payload["mode"] = "preflight"
        return payload

    try:
        result = asyncio.run(_execute_real(request))
    except Exception as exc:
        logger.exception("execute_cases: unexpected exception")
        return {
            "status": "error",
            "mode": "real",
            "passed": 0,
            "failed": 0,
            "total": 0,
            "records": [],
            "engine_error": f"execute_cases 捕获未处理异常: {exc}",
        }
    return result.to_flat()


# ── Convenience helper for scripts/execute_cases.py ────────────────────────


def load_cases_payload(cases_path: Path) -> list[dict[str, Any]]:
    """Read & minimal-validate a cases.json file.

    Raises ``SystemExit`` (so the CLI script exits cleanly with a user-
    facing message) on missing/empty/malformed input.  Mirrors the
    pre-existing behavior in :mod:`scripts.execute_cases`.
    """
    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise SystemExit("cases must be a non-empty JSON array")
    return payload


__all__ = [
    "RunRequest",
    "load_cases_payload",
    "pick_server",
    "run_cases",
    "validate_server_info",
]
