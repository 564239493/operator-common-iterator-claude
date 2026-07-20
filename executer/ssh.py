"""Async SSH/asyncssh wrapper for the project-local executer.

Thin adaptation of
``operator-common-iterator/executer/ssh_executor.py`` — exposes the same
four primitives (connect, sftp_upload, run, find_output) but with all
external-package imports confined to :mod:`asyncssh` and the standard
library.  Engine-level failures (TCP / auth / SFTP / transport) raise
:class:`SSHEngineError` so the caller can short-circuit with
``engine_error`` instead of having the failure masquerade as a case fail.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncssh

logger = logging.getLogger(__name__)


class SSHEngineError(RuntimeError):
    """Raised when an engine-level SSH/SFTP/IO operation fails.

    The runner translates this into ``ExecutionResult.status = "error"``
    and ``error_message`` — never into per-case failures.
    """


@dataclass(frozen=True)
class ServerEndpoint:
    """Resolved SSH target — straight from a ``servers.json`` row."""

    host: str
    port: int
    username: str
    password: str

    @classmethod
    def from_server_row(cls, server: dict[str, Any]) -> "ServerEndpoint":
        return cls(
            host=str(server["ip"]).strip(),
            port=int(server.get("port") or 22),
            username=str(server["username"]).strip(),
            password=str(server.get("password") or "").strip(),
        )


@dataclass
class CommandResult:
    """Captured output of a remote shell command."""

    exit_code: int
    stdout: str
    stderr: str
    duration: float


# ── Connectivity ────────────────────────────────────────────────────────────


async def tcp_probe(host: str, port: int, timeout: float = 10.0) -> None:
    """Cheap TCP-level reachability check before opening SSH.

    Distinguishes "host unreachable" from "auth failure" so we can show
    a cleaner error than the generic asyncssh traceback.  asyncssh
    itself does some probing, but doing the TCP check first gives us
    fast failure on local dev boxes that have outbound traffic blocked.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        await asyncio.get_event_loop().run_in_executor(
            None, sock.connect, (host, port)
        )
        sock.close()
    except socket.timeout as exc:
        raise SSHEngineError(f"连接超时: {host}:{port} 无响应") from exc
    except ConnectionRefusedError as exc:
        raise SSHEngineError(f"连接被拒绝: {host}:{port}") from exc
    except OSError as exc:
        raise SSHEngineError(f"网络错误: {exc}") from exc


async def connect(
    endpoint: ServerEndpoint,
    *,
    timeout: float = 30.0,
) -> asyncssh.SSHClientConnection:
    """Open an SSH connection; engine-level failures raise :class:`SSHEngineError`."""
    await tcp_probe(endpoint.host, endpoint.port, timeout=timeout)

    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                endpoint.host,
                port=endpoint.port,
                username=endpoint.username,
                password=endpoint.password,
                known_hosts=None,
            ),
            timeout=timeout,
        )
    except asyncssh.PermissionDenied as exc:
        raise SSHEngineError(
            f"SSH 认证失败: {endpoint.username}@{endpoint.host}"
        ) from exc
    except asyncssh.TimeoutError as exc:
        raise SSHEngineError("SSH 认证超时") from exc
    except Exception as exc:  # asyncssh raises broad exception types
        raise SSHEngineError(f"SSH 连接失败: {exc}") from exc

    logger.info(
        "ssh.connect: connected to %s@%s:%d",
        endpoint.username,
        endpoint.host,
        endpoint.port,
    )
    return conn


# ── File transfer ───────────────────────────────────────────────────────────


async def sftp_upload(
    conn: asyncssh.SSHClientConnection,
    local_path: str | Path,
    remote_path: str,
) -> None:
    """Upload ``local_path`` to ``remote_path`` via SFTP.

    Creates the parent directory on the remote side (idempotent).  Mirrors
    the reference implementation's `mkdir -p` + best-effort
    ``sftp.makedirs`` fallback for older asyncssh versions.
    """
    local = Path(local_path)
    if not local.exists():
        raise SSHEngineError(f"本地文件不存在: {local_path}")

    parent = remote_path.rsplit("/", 1)[0] or "."
    mkdir_cmd = f"mkdir -p '{parent}'"
    try:
        await conn.run(mkdir_cmd, check=False)
    except Exception as exc:  # pragma: no cover — mkdir rarely fails
        logger.warning(
            "ssh.sftp_upload: mkdir -p %s failed (continuing): %s",
            parent,
            exc,
        )

    try:
        async with conn.start_sftp_client() as sftp:
            try:
                await sftp.makedirs(parent, exist_ok=True)
            except (AttributeError, OSError):
                try:
                    await sftp.makedirs(parent)
                except OSError:
                    pass
            await sftp.put(str(local), remote_path)
    except Exception as exc:
        raise SSHEngineError(
            f"SFTP 上传失败: {local_path} -> {remote_path}: {exc}"
        ) from exc

    logger.info(
        "ssh.sftp_upload: uploaded %s -> %s (%d bytes)",
        local_path,
        remote_path,
        local.stat().st_size,
    )


# ── Shell execution ─────────────────────────────────────────────────────────


async def scp_upload(
    conn: asyncssh.SSHClientConnection,
    local_path: str | Path,
    remote_path: str,
) -> None:
    """Upload ``local_path`` to ``remote_path`` via SCP."""
    local = Path(local_path)
    if not local.exists():
        raise SSHEngineError(f"Local file does not exist: {local_path}")

    parent = remote_path.rsplit("/", 1)[0] or "."
    try:
        await conn.run(f"mkdir -p '{parent}'", check=False)
    except Exception as exc:  # pragma: no cover - mkdir rarely fails
        logger.warning(
            "ssh.scp_upload: mkdir -p %s failed (continuing): %s",
            parent,
            exc,
        )

    try:
        await asyncssh.scp(str(local), (conn, remote_path))
    except Exception as exc:
        raise SSHEngineError(
            f"SCP upload failed: {local_path} -> {remote_path}: {exc}"
        ) from exc

    logger.info(
        "ssh.scp_upload: uploaded %s -> %s (%d bytes)",
        local_path,
        remote_path,
        local.stat().st_size,
    )


async def upload_file(
    conn: asyncssh.SSHClientConnection,
    local_path: str | Path,
    remote_path: str,
    *,
    transfer_mode: str = "auto",
) -> None:
    """Upload a file via SFTP, SCP, or auto (SFTP-first with SCP fallback).

    ``transfer_mode`` selects the transfer strategy:

    * ``"auto"`` (default) — SFTP first, SCP fallback if SFTP is
      unavailable.  Mirrors the original behaviour.
    * ``"scp"`` — SCP only, for hosts whose SFTP subsystem is disabled
      or unsupported.  Skips the SFTP attempt entirely, avoiding the
      hang/timeout that some restricted hosts exhibit before failing.
    * ``"sftp"`` — SFTP only, for hosts where SCP is blocked.
    """
    mode = (transfer_mode or "auto").strip().lower()

    if mode == "scp":
        await scp_upload(conn, local_path, remote_path)
        return

    if mode == "sftp":
        await sftp_upload(conn, local_path, remote_path)
        return

    # auto: SFTP first, then SCP fallback
    try:
        await sftp_upload(conn, local_path, remote_path)
    except SSHEngineError as sftp_exc:
        logger.warning(
            "ssh.upload_file: SFTP failed for %s -> %s; trying SCP: %s",
            local_path,
            remote_path,
            sftp_exc,
        )
        try:
            await scp_upload(conn, local_path, remote_path)
        except SSHEngineError as scp_exc:
            raise SSHEngineError(
                "SFTP/SCP upload failed: "
                f"SFTP=({sftp_exc}); SCP=({scp_exc})"
            ) from scp_exc


async def run(
    conn: asyncssh.SSHClientConnection,
    command: str,
    *,
    timeout: float = 1800.0,
) -> CommandResult:
    """Run ``command`` on the remote shell and capture its output.

    Transport / timeout failures surface as :class:`SSHEngineError`.
    Non-zero exit codes are returned, not raised — the caller decides
    whether a non-zero exit is "test failed" (record) or "engine error"
    (abort).
    """
    loop = asyncio.get_event_loop()
    started = loop.time()
    try:
        completed = await asyncio.wait_for(
            conn.run(command, check=False),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        raise SSHEngineError(
            f"远端命令执行超时 ({timeout}s): {command[:200]}"
        ) from exc
    except Exception as exc:
        raise SSHEngineError(f"远端命令执行失败: {exc}") from exc

    duration = loop.time() - started
    return CommandResult(
        exit_code=int(completed.exit_status)
        if completed.exit_status is not None
        else -1,
        stdout=str(completed.stdout or ""),
        stderr=str(completed.stderr or ""),
        duration=float(duration),
    )


# ── Output discovery ────────────────────────────────────────────────────────


async def find_latest_output_dir(
    conn: asyncssh.SSHClientConnection,
    output_root: str,
    operator_prefix: str,
) -> str | None:
    """Return the most recent ``<output_root>/<operator_prefix>*`` directory.

    ATK typically stamps output dirs with ``YYYYMMDD_HHMMSS_<hash>``
    suffixes; ``ls -td`` (sort by mtime, newest first) + ``head -1`` is
    good enough for the common case.
    """
    cmd = (
        f"if [ -d '{output_root}' ]; then "
        f"ls -1td '{output_root}'/{operator_prefix}* 2>/dev/null | head -1; "
        f"else echo __MISSING__; fi"
    )
    result = await run(conn, cmd, timeout=30.0)
    lines = (result.stdout or "").strip().splitlines()
    if not lines:
        return None
    candidate = lines[0].strip()
    if not candidate or candidate == "__MISSING__":
        return None
    return candidate


async def sftp_download_file(
    conn: asyncssh.SSHClientConnection,
    remote_path: str,
    local_path: Path,
) -> None:
    """Pull a single remote file via SFTP.  Missing file is swallowed."""
    try:
        async with conn.start_sftp_client() as sftp:
            await sftp.get(remote_path, str(local_path))
    except FileNotFoundError:
        logger.warning("ssh.sftp_download_file: %s not found", remote_path)
    except Exception as exc:
        logger.warning(
            "ssh.sftp_download_file: %s failed: %s", remote_path, exc
        )


async def sftp_list_dir(
    conn: asyncssh.SSHClientConnection,
    remote_dir: str,
) -> list[str]:
    """List entries in a remote directory.  Returns ``[]`` on any failure."""
    try:
        async with conn.start_sftp_client() as sftp:
            entries = await sftp.listdir(remote_dir)
        return [str(e) for e in entries]
    except Exception as exc:
        logger.warning("ssh.sftp_list_dir: %s failed: %s", remote_dir, exc)
        return []


async def shell_list_dir(
    conn: asyncssh.SSHClientConnection,
    remote_dir: str,
) -> list[str]:
    """List directory entries via shell ``ls -1``.  Returns ``[]`` on failure.

    Fallback for hosts whose SFTP subsystem is unavailable; mirrors the
    shell approach already used by :func:`find_latest_output_dir`.  Only
    entry names are returned, in directory order.
    """
    cmd = (
        f"if [ -d '{remote_dir}' ]; then "
        f"ls -1 '{remote_dir}' 2>/dev/null; "
        f"else echo __MISSING__; fi"
    )
    result = await run(conn, cmd, timeout=30.0)
    out = (result.stdout or "").strip()
    if not out or out == "__MISSING__":
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


async def shell_download_file(
    conn: asyncssh.SSHClientConnection,
    remote_path: str,
    local_path: Path,
) -> None:
    """Pull a remote file via shell ``base64``.  Missing file is swallowed.

    Fallback for hosts whose SFTP subsystem is unavailable (e.g. the
    ``sftp-server`` binary is absent while ``sshd_config`` still declares
    the subsystem).  Works for both binary (xlsx) and text (log) files;
    base64 round-trips byte-exact.
    """
    cmd = (
        f"if [ -f '{remote_path}' ]; then "
        f"base64 '{remote_path}'; "
        f"else echo __MISSING__; fi"
    )
    result = await run(conn, cmd, timeout=300.0)
    compact = "".join((result.stdout or "").split())
    if not compact or compact == "__MISSING__":
        logger.warning("ssh.shell_download_file: %s not found", remote_path)
        return
    try:
        data = base64.b64decode(compact)
    except Exception as exc:
        logger.warning(
            "ssh.shell_download_file: %s base64 decode failed: %s",
            remote_path,
            exc,
        )
        return
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)


async def list_dir(
    conn: asyncssh.SSHClientConnection,
    remote_dir: str,
    *,
    transfer_mode: str = "auto",
) -> list[str]:
    """List a remote directory via SFTP, shell, or auto.

    ``transfer_mode`` mirrors :func:`upload_file`:

    * ``"auto"`` (default) — SFTP first, shell fallback if SFTP returns
      nothing.  The fallback makes result recovery robust on hosts whose
      SFTP subsystem is unavailable.
    * ``"sftp"`` — SFTP only.
    * ``"shell"`` — shell ``ls -1`` only.
    """
    mode = (transfer_mode or "auto").strip().lower()
    if mode in ("shell", "scp"):
        # scp: the server has no SFTP subsystem; use shell ls for listing
        # the same way upload_file uses scp_upload to skip SFTP.
        return await shell_list_dir(conn, remote_dir)
    if mode == "sftp":
        return await sftp_list_dir(conn, remote_dir)
    # auto: SFTP first, then shell fallback if SFTP yielded nothing.
    entries = await sftp_list_dir(conn, remote_dir)
    if entries:
        return entries
    logger.warning(
        "ssh.list_dir: SFTP empty/failed for %s; trying shell ls",
        remote_dir,
    )
    return await shell_list_dir(conn, remote_dir)


async def download_file(
    conn: asyncssh.SSHClientConnection,
    remote_path: str,
    local_path: Path,
    *,
    transfer_mode: str = "auto",
) -> None:
    """Download a file via SFTP, shell, or auto (SFTP-first + shell fallback).

    ``transfer_mode`` mirrors :func:`upload_file`.  ``auto`` is the safe
    default for hosts whose SFTP subsystem may be unavailable; the shell
    ``base64`` fallback fetches the file byte-exact when SFTP cannot.
    """
    mode = (transfer_mode or "auto").strip().lower()
    if mode in ("shell", "scp"):
        # scp: the server has no SFTP subsystem; use shell base64 for
        # downloading the same way upload_file uses scp_upload to skip SFTP.
        await shell_download_file(conn, remote_path, local_path)
        return
    if mode == "sftp":
        await sftp_download_file(conn, remote_path, local_path)
        return
    # auto: SFTP first; if it didn't land the file, fall back to shell.
    # Drop any stale local copy so the exists() probe is meaningful.
    try:
        if local_path.exists():
            local_path.unlink()
    except Exception:
        pass
    await sftp_download_file(conn, remote_path, local_path)
    if local_path.exists():
        return
    logger.warning(
        "ssh.download_file: SFTP failed for %s; trying shell base64",
        remote_path,
    )
    await shell_download_file(conn, remote_path, local_path)


__all__ = [
    "CommandResult",
    "ServerEndpoint",
    "SSHEngineError",
    "connect",
    "download_file",
    "find_latest_output_dir",
    "list_dir",
    "run",
    "scp_upload",
    "sftp_download_file",
    "sftp_list_dir",
    "sftp_upload",
    "shell_download_file",
    "shell_list_dir",
    "tcp_probe",
    "upload_file",
]
