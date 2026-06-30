"""Pydantic models for the project-local executer.

Mirrors ``operator-common-iterator/executer/execution_result.py`` so the
runtime contract matches the reference: ``ExecutionResult`` carries the
SSH/ATK command outcome plus the structured ATK report records and the
remote log, and is what ``scripts/execute_cases.py`` flattens into the
``execution_result.json`` artifact under each ``runs/<run-id>/iter_*/``.

Kept deliberately small — only what the executer needs.  Anything else is
projected into ``ReportRecord.extra`` for forward compatibility.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportRecord(BaseModel):
    """A single row from the ATK xlsx report."""

    id: str | None = Field(default=None, description="用例 ID (xlsx 标识列).")
    run_result: str | None = Field(
        default=None,
        description="运行结果列 (pass / fail / skip / error 等).",
    )
    failure_reason: str | None = Field(default=None, description="失败原因列.")
    case_json: dict[str, Any] | None = Field(
        default=None,
        description="用例 JSON 信息列 (反序列化后的对象).",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="xlsx 其它列的兜底存储.",
    )


class TaskReportData(BaseModel):
    """Structured data extracted from the ATK ``report/`` xlsx file."""

    report_path: str | None = Field(
        default=None,
        description="xlsx 报告的本地缓存路径 (供后续下载).",
    )
    sheet_name: str | None = Field(default=None, description="读取的工作表名.")
    record_count: int = Field(default=0, description="解析出的报告记录数.")
    passed: int = Field(default=0, description="通过的用例数.")
    failed: int = Field(default=0, description="失败的用例数.")
    report_records: list[ReportRecord] = Field(
        default_factory=list,
        description="逐条解析出的报告记录.",
    )
    parse_error: str | None = Field(
        default=None,
        description="报告解析阶段的错误信息 (不影响主流程, 仅记录).",
    )


class ExecutionResult(BaseModel):
    """Canonical result object emitted by the local executer.

    ``status`` distinguishes four outcomes:

    * ``success`` — atk command exited 0, results extracted.
    * ``failed`` — atk command exited non-zero (test failure, not infra).
    * ``timeout`` — atk command exceeded the configured timeout.
    * ``error`` — engine-level failure (SSH / SFTP / file IO / config);
      ``error_message`` is populated and downstream callers must surface
      this verbatim into ``execution_result.json.engine_error``.
    """

    status: Literal["success", "failed", "timeout", "error"] = Field(
        default="success",
        description="ATK 命令的执行结果状态.",
    )
    exit_code: int | None = Field(
        default=None,
        description="远端 ``atk task`` 命令的退出码.",
    )
    stdout: str = Field(default="", description="远端命令的 stdout 截取.")
    stderr: str = Field(default="", description="远端命令的 stderr 截取.")
    duration: float = Field(
        default=0.0,
        description="整个执行阶段耗时 (秒, 含 SSH / 上传 / atk 命令 / 拉取).",
    )
    task_report_data: TaskReportData = Field(
        default_factory=TaskReportData,
        description="从 ATK report/ 目录提取的结构化结果.",
    )
    log_content: str = Field(default="", description="远端 ``log/atk.log`` 内容.")
    error_message: str | None = Field(
        default=None,
        description="错误描述 (status=error 或 failed 时填充).",
    )
    remote_output_dir: str | None = Field(
        default=None,
        description="远端 ATK 实际使用的输出目录 (含 operator_name 前缀).",
    )

    def to_flat(self) -> dict[str, Any]:
        """Project to the flat ``execution_result.json`` contract.

        Mirrors what the reference ``run_atk`` returns — top-level passed /
        failed / total / records / status fields, with the full Pydantic
        body preserved under ``task_report_data`` for callers that want
        the rich shape.
        """
        record_count = self.task_report_data.record_count
        passed = self.task_report_data.passed
        failed = self.task_report_data.failed
        records = [
            {
                "id": r.id,
                "run_result": r.run_result,
                "failure_reason": r.failure_reason,
                "case_json": r.case_json,
            }
            for r in self.task_report_data.report_records
        ]
        payload: dict[str, Any] = {
            "status": self.status,
            "mode": "real",
            "passed": passed,
            "failed": failed,
            "total": passed + failed,
            "records": records,
            "engine_error": self.error_message or "",
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration": self.duration,
            "log_content": self.log_content,
            "remote_output_dir": self.remote_output_dir,
            "task_report_data": self.task_report_data.model_dump(),
        }
        return payload


__all__ = ["ExecutionResult", "ReportRecord", "TaskReportData"]
