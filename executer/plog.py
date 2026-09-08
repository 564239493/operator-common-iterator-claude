"""Collect Ascend PLOG artifacts after a real remote TTK execution."""
from __future__ import annotations

import json
import shlex
import shutil
import tarfile
from pathlib import Path
from typing import Any

from .ssh import download_file, run

DEFAULT_PLOG_DIR = "/root/ascend/log/debug"


def build_plog_snapshot_command(
    plog_dir: str,
    remote_archive: str,
    remote_errors: str,
    remote_tar_stderr: str,
) -> str:
    """Build the remote snapshot command with fully quoted fixed paths."""
    source = shlex.quote(plog_dir)
    archive = shlex.quote(remote_archive)
    errors = shlex.quote(remote_errors)
    tar_stderr = shlex.quote(remote_tar_stderr)
    return (
        f"if [ -d {source} ]; then "
        f"grep -rn --binary-files=without-match -- ERROR {source} "
        f"> {errors} 2>/dev/null; grep_rc=$?; "
        "if [ \"$grep_rc\" -eq 1 ]; then grep_rc=0; fi; "
        f"tar -czf {archive} -C {source} . 2> {tar_stderr}; tar_rc=$?; "
        f"printf '__PLOG_FOUND__\\n'; "
        "printf '__PLOG_GREP_RC__=%s\\n' \"$grep_rc\"; "
        "printf '__PLOG_TAR_RC__=%s\\n' \"$tar_rc\"; "
        f"printf '__PLOG_FILE_COUNT__=%s\\n' "
        f"\"$(find {source} -type f 2>/dev/null | wc -l)\"; "
        f"printf '__PLOG_ERROR_COUNT__=%s\\n' "
        f"\"$(wc -l < {errors} 2>/dev/null || printf 0)\"; "
        "else "
        f": > {errors}; : > {tar_stderr}; "
        "printf '__PLOG_MISSING__\\n'; "
        "printf '__PLOG_GREP_RC__=0\\n'; "
        "printf '__PLOG_TAR_RC__=0\\n'; "
        "printf '__PLOG_FILE_COUNT__=0\\n'; "
        "printf '__PLOG_ERROR_COUNT__=0\\n'; "
        "fi"
    )


def _marker_int(output: str, marker: str) -> int:
    prefix = marker + "="
    for line in output.splitlines():
        if line.startswith(prefix):
            try:
                return max(0, int(line[len(prefix):].strip()))
            except ValueError:
                return 0
    return 0


def _safe_extract(archive_path: Path, output_dir: Path) -> int:
    """Extract regular PLOG files while rejecting traversal and links."""
    output_dir.mkdir(parents=True, exist_ok=True)
    root = output_dir.resolve()
    extracted_files = 0
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            if not (member.isdir() or member.isfile()):
                continue
            target = (output_dir / member.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"unsafe PLOG archive member: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = archive.extractfile(member)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            extracted_files += 1
    return extracted_files


def _write_manifest(local_dir: Path, payload: dict[str, Any]) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def collect_plog_snapshot(
    conn,
    *,
    remote_run_dir: str,
    artifact_dir: Path,
    plog_dir: str = DEFAULT_PLOG_DIR,
    transfer_mode: str = "auto",
    enabled: bool = True,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Archive, download, extract, and summarize the current execution PLOG."""
    local_dir = artifact_dir / "plog"
    raw_dir = local_dir / "raw"
    error_summary = local_dir / "error_summary.log"
    tar_stderr = local_dir / "archive_stderr.log"
    archive_path = local_dir / "plog.tar.gz"
    remote_archive = f"{remote_run_dir}/plog.tar.gz"
    remote_errors = f"{remote_run_dir}/plog_errors.log"
    remote_tar_stderr = f"{remote_run_dir}/plog_archive_stderr.log"
    payload: dict[str, Any] = {
        "status": "disabled" if not enabled else "pending",
        "remote_plog_dir": plog_dir,
        "local_dir": str(local_dir.resolve()),
        "raw_dir": str(raw_dir.resolve()),
        "error_summary": str(error_summary.resolve()),
        "manifest": str((local_dir / "manifest.json").resolve()),
        "file_count": 0,
        "error_count": 0,
        "grep_exit_code": 0,
        "tar_exit_code": 0,
        "collection_error": "",
    }
    if not enabled:
        _write_manifest(local_dir, payload)
        return payload
    if not plog_dir.startswith("/"):
        payload.update({
            "status": "error",
            "collection_error": "ttk.plog_dir must be an absolute remote path",
        })
        _write_manifest(local_dir, payload)
        return payload

    try:
        command = build_plog_snapshot_command(
            plog_dir, remote_archive, remote_errors, remote_tar_stderr,
        )
        snapshot = await run(conn, command, timeout=timeout)
        payload["file_count"] = _marker_int(snapshot.stdout, "__PLOG_FILE_COUNT__")
        payload["error_count"] = _marker_int(snapshot.stdout, "__PLOG_ERROR_COUNT__")
        payload["grep_exit_code"] = _marker_int(snapshot.stdout, "__PLOG_GREP_RC__")
        payload["tar_exit_code"] = _marker_int(snapshot.stdout, "__PLOG_TAR_RC__")
        if "__PLOG_MISSING__" in snapshot.stdout:
            payload.update({
                "status": "missing",
                "collection_error": f"remote PLOG directory does not exist: {plog_dir}",
            })
            error_summary.parent.mkdir(parents=True, exist_ok=True)
            error_summary.write_text("", encoding="utf-8")
            _write_manifest(local_dir, payload)
            return payload
        if "__PLOG_FOUND__" not in snapshot.stdout:
            raise ValueError(
                "remote PLOG snapshot command returned no status marker: "
                f"exit_code={snapshot.exit_code}, stderr={snapshot.stderr.strip()}"
            )

        await download_file(
            conn, remote_errors, error_summary, transfer_mode=transfer_mode,
        )
        await download_file(
            conn, remote_tar_stderr, tar_stderr, transfer_mode=transfer_mode,
        )
        if payload["grep_exit_code"] != 0 or payload["tar_exit_code"] != 0:
            raise ValueError(
                "remote PLOG snapshot failed: "
                f"grep_exit_code={payload['grep_exit_code']}, "
                f"tar_exit_code={payload['tar_exit_code']}"
            )
        await download_file(
            conn, remote_archive, archive_path, transfer_mode=transfer_mode,
        )
        if not error_summary.is_file():
            error_summary.parent.mkdir(parents=True, exist_ok=True)
            error_summary.write_text("", encoding="utf-8")
        if not archive_path.is_file():
            raise ValueError("remote PLOG archive was not created or downloaded")
        extracted = _safe_extract(archive_path, raw_dir)
        archive_path.unlink()
        payload.update({
            "status": "collected",
            "extracted_file_count": extracted,
            "archive_removed_after_extract": True,
        })
    except Exception as exc:  # PLOG failure must not erase the TTK result.
        payload.update({
            "status": "error",
            "collection_error": f"{type(exc).__name__}: {exc}",
        })
    _write_manifest(local_dir, payload)
    return payload


__all__ = [
    "DEFAULT_PLOG_DIR",
    "build_plog_snapshot_command",
    "collect_plog_snapshot",
]
