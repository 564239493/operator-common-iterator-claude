#!/usr/bin/env python3
"""Offline regression tests for PLOG collection and execution metadata."""
from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from executer.plog import (
    _safe_extract,
    build_plog_snapshot_command,
    collect_plog_snapshot,
)
from executer.ssh import CommandResult
from validate_artifacts import validate_execution


class PlogCommandTests(unittest.TestCase):
    def test_snapshot_command_captures_error_lines_and_all_files(self) -> None:
        command = build_plog_snapshot_command(
            "/root/ascend/log/debug",
            "/tmp/run/plog.tar.gz",
            "/tmp/run/plog_errors.log",
            "/tmp/run/plog_archive_stderr.log",
        )
        self.assertIn("grep -rn --binary-files=without-match -- ERROR", command)
        self.assertIn("tar -czf /tmp/run/plog.tar.gz", command)
        self.assertIn("__PLOG_GREP_RC__", command)
        self.assertIn("__PLOG_TAR_RC__", command)
        self.assertIn("find /root/ascend/log/debug -type f", command)
        self.assertIn("__PLOG_MISSING__", command)

    def test_safe_extract_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "bad.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                member = tarfile.TarInfo("../outside.log")
                payload = b"ERROR"
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            with self.assertRaisesRegex(ValueError, "unsafe PLOG"):
                _safe_extract(archive_path, root / "raw")

    def test_safe_extract_materializes_raw_plog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "plog.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                member = tarfile.TarInfo("device/plog.log")
                payload = b"[ERROR] device failure\n"
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            count = _safe_extract(archive_path, root / "raw")
            self.assertEqual(count, 1)
            self.assertEqual(
                (root / "raw" / "device" / "plog.log").read_bytes(), payload,
            )


class PlogCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_collector_downloads_summary_and_extracts_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_archive = root / "source.tar.gz"
            with tarfile.open(source_archive, "w:gz") as archive:
                member = tarfile.TarInfo("device/plog.log")
                log_payload = b"context\nERROR device failure\n"
                member.size = len(log_payload)
                archive.addfile(member, io.BytesIO(log_payload))

            async def fake_download(_conn, remote_path, local_path, **_kwargs):
                local_path.parent.mkdir(parents=True, exist_ok=True)
                if remote_path.endswith("plog.tar.gz"):
                    local_path.write_bytes(source_archive.read_bytes())
                elif remote_path.endswith("plog_errors.log"):
                    local_path.write_text(
                        "/root/ascend/log/debug/device/plog.log:2:ERROR device failure\n",
                        encoding="utf-8",
                    )
                else:
                    local_path.write_text("", encoding="utf-8")

            snapshot = CommandResult(
                exit_code=0,
                stdout=(
                    "__PLOG_FOUND__\n"
                    "__PLOG_GREP_RC__=0\n"
                    "__PLOG_TAR_RC__=0\n"
                    "__PLOG_FILE_COUNT__=1\n"
                    "__PLOG_ERROR_COUNT__=1\n"
                ),
                stderr="",
                duration=0.1,
            )
            artifacts = root / "artifacts"
            with (
                patch("executer.plog.run", new=AsyncMock(return_value=snapshot)),
                patch("executer.plog.download_file", new=fake_download),
            ):
                result = await collect_plog_snapshot(
                    object(),
                    remote_run_dir="/tmp/operator_run",
                    artifact_dir=artifacts,
                )

            self.assertEqual(result["status"], "collected")
            self.assertEqual(result["file_count"], 1)
            self.assertEqual(result["error_count"], 1)
            self.assertEqual(result["extracted_file_count"], 1)
            self.assertFalse((artifacts / "plog" / "plog.tar.gz").exists())
            self.assertIn(
                "ERROR device failure",
                (artifacts / "plog" / "raw" / "device" / "plog.log").read_text(
                    encoding="utf-8"
                ),
            )
            manifest = json.loads(
                (artifacts / "plog" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "collected")

    async def test_collector_reports_remote_permission_or_tar_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary) / "artifacts"

            async def fake_download(_conn, _remote_path, local_path, **_kwargs):
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_text("Permission denied\n", encoding="utf-8")

            snapshot = CommandResult(
                exit_code=0,
                stdout=(
                    "__PLOG_FOUND__\n"
                    "__PLOG_GREP_RC__=2\n"
                    "__PLOG_TAR_RC__=2\n"
                    "__PLOG_FILE_COUNT__=0\n"
                    "__PLOG_ERROR_COUNT__=0\n"
                ),
                stderr="",
                duration=0.1,
            )
            with (
                patch("executer.plog.run", new=AsyncMock(return_value=snapshot)),
                patch("executer.plog.download_file", new=fake_download),
            ):
                result = await collect_plog_snapshot(
                    object(),
                    remote_run_dir="/tmp/operator_run",
                    artifact_dir=artifacts,
                )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["grep_exit_code"], 2)
            self.assertEqual(result["tar_exit_code"], 2)
            self.assertIn("remote PLOG snapshot failed", result["collection_error"])


class PlogContractTests(unittest.TestCase):
    def test_real_ttk_requires_plog_metadata(self) -> None:
        execution = {
            "status": "failed", "mode": "ttk_e2e",
            "passed": 0, "failed": 1, "total": 1,
            "records": [], "engine_error": "",
        }
        errors = validate_execution(execution)
        self.assertTrue(any("must include PLOG" in error for error in errors), errors)

    def test_collected_plog_paths_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plog = Path(temporary) / "plog"
            raw = plog / "raw"
            raw.mkdir(parents=True)
            summary = plog / "error_summary.log"
            manifest = plog / "manifest.json"
            summary.write_text("x.log:1:ERROR\n", encoding="utf-8")
            manifest.write_text(json.dumps({"status": "collected"}), encoding="utf-8")
            execution = {
                "status": "failed", "mode": "ttk_e2e",
                "passed": 0, "failed": 1, "total": 1,
                "records": [], "engine_error": "",
                "plog": {
                    "status": "collected",
                    "remote_plog_dir": "/root/ascend/log/debug",
                    "local_dir": str(plog),
                    "raw_dir": str(raw),
                    "error_summary": str(summary),
                    "manifest": str(manifest),
                    "file_count": 1,
                    "error_count": 1,
                    "collection_error": "",
                },
            }
            self.assertEqual(validate_execution(execution), [])


if __name__ == "__main__":
    unittest.main()
