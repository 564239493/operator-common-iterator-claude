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


class ComparisonRatio(BaseModel):
    """精度对比的实际比值 (记录性, 不入成败)."""

    max_re_ratio: float | None = Field(default=None, description="最大相对误差比.")
    avg_re_ratio: float | None = Field(default=None, description="平均相对误差比.")
    root_mean_squared_ratio: float | None = Field(
        default=None, description="均方根误差比."
    )


class ComparisonResult(BaseModel):
    """fusion 精度对比结果 (仅记录, 不影响 status / passed / failed).

    default 流程不填充. fusion 流程由 step4 ``accuracy_load`` 产出 xlsx 解析得到,
    阈值取自 ``acc_config.txt`` 的 ``cv_fused_double_benchmark``.
    """

    thresholds: dict[str, Any] | None = Field(
        default=None,
        description="来自 acc_config.txt 的 cv_fused_double_benchmark 阈值 (max/avg/rms).",
    )
    actual: ComparisonRatio | None = Field(
        default=None,
        description="step4 accuracy_load 产出的实际比值.",
    )


class FusionPhase(BaseModel):
    """fusion 4 步流程中某一步的执行留痕."""

    phase: str = Field(
        ...,
        description="cpu_benchmark | npu_cascaded | rename | accuracy_load.",
    )
    command: str = Field(default="", description="执行的远程命令原文 (不截断).")
    exit_code: int | None = Field(default=None, description="命令退出码.")
    duration: float = Field(default=0.0, description="该步耗时 (秒).")
    output_dir: str | None = Field(
        default=None,
        description="该步产出的 case_{time}/output 目录 (rename 步为 None).",
    )
    dir_check_passed: bool | None = Field(
        default=None,
        description="路径门禁 (rank_0/rank_1 非空) 结果; rename/accuracy_load 步为 None.",
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

    status: Literal["success", "failed", "timeout", "error", "generate"] = Field(
        default="success",
        description=(
            "ATK 命令的执行结果状态。"
            "``generate`` 表示已生成本地产物 (cases_executor.py + cases_expanded.json) "
            "但未连远端 ATK, 常用于 ``scripts/execute_cases.py --generate``。"
        ),
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
    execution_strategy: Literal["default", "fusion"] = Field(
        default="default",
        description="执行策略: default 走现有单卡单后端流程; fusion 走通算融合 4 步流程.",
    )
    comparison_result: ComparisonResult | None = Field(
        default=None,
        description="fusion 精度对比结果 (仅记录, 不影响 status/passed/failed); default 为 None.",
    )
    fusion_phases: list[FusionPhase] = Field(
        default_factory=list,
        description="fusion 4 步流程每步留痕; default 为空.",
    )
    _generate_artifacts: dict[str, Path] | None = None
    _generate_atk_command: str | None = None
    _generate_remote_paths: dict[str, str] | None = None

    def set_generate_artifacts(
        self,
        artifacts: dict[str, Path],
        *,
        atk_command: str = "",
        remote_paths: dict[str, str] | None = None,
    ) -> None:
        """Attach local paths the user needs to SFTP-upload (generate mode).

        Keys are arbitrary labels (``cases.json``, ``cases_expanded.json``,
        ``cases_executor.py``); values are absolute ``Path`` objects.  Only
        surfaced when ``status == "generate"``.

        Optionally records the ``atk_command`` to run on the remote host
        and ``remote_paths`` mapping local artifact labels → remote SFTP
        destination paths — surfaced verbatim in ``to_flat()`` so the user
        knows exactly what to SFTP-upload and execute.
        """
        object.__setattr__(self, "_generate_artifacts", artifacts)
        if atk_command:
            object.__setattr__(self, "_generate_atk_command", atk_command)
        if remote_paths:
            object.__setattr__(self, "_generate_remote_paths", remote_paths)

    def to_flat(self) -> dict[str, Any]:
        """Project to the flat ``execution_result.json`` contract.

        Mirrors what the reference ``run_atk`` returns — top-level passed /
        failed / total / records / status fields, with the full Pydantic
        body preserved under ``task_report_data`` for callers that want
        the rich shape.  Generate mode emits ``status="generate"``,
        ``total=0``, ``records=[]``, with the local artifacts the user
        still needs to SFTP-upload listed in ``generate_artifacts``,
        the ``atk`` shell command in ``generate_atk_command``, and
        remote SFTP destinations in ``generate_remote_paths``.
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
        generate_artifacts: list[dict[str, str]] = []
        if self.status == "generate":
            for k, v in (self._generate_artifacts or {}).items():
                generate_artifacts.append({"key": k, "path": str(v)})
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
            "generate_artifacts": generate_artifacts,
        }
        if self.status == "generate":
            payload["generate_atk_command"] = self._generate_atk_command or ""
            payload["generate_remote_paths"] = self._generate_remote_paths or {}
        payload["execution_strategy"] = self.execution_strategy
        if self.execution_strategy == "fusion":
            payload["comparison_result"] = (
                self.comparison_result.model_dump() if self.comparison_result else None
            )
            payload["fusion_phases"] = [p.model_dump() for p in self.fusion_phases]
        return payload


__all__ = [
    "ExecutionResult",
    "ReportRecord",
    "TaskReportData",
    "ComparisonRatio",
    "ComparisonResult",
    "FusionPhase",
]
