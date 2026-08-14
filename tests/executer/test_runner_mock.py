"""executer/runner.py mock 模式单元测试（确定性，无外部依赖）。

依赖 asyncssh（runner 导入链），缺失时整模块跳过。
"""

import pytest

pytest.importorskip("asyncssh")

from executer.runner import _mock_execute  # noqa: E402


def test_mock_all_pass():
    result = _mock_execute([{"id": 1}, {"id": 2}], fail_every=0)
    assert result.status == "success"
    assert result.task_report_data.passed == 2
    assert result.task_report_data.failed == 0
    assert result.task_report_data.record_count == 2


def test_mock_deterministic_failures():
    result = _mock_execute(
        [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}], fail_every=2
    )
    assert result.status == "failed"
    assert result.task_report_data.failed == 2
    assert result.task_report_data.passed == 2
    records = result.task_report_data.report_records
    assert records[0].run_result == "pass"
    assert records[1].run_result == "fail"
    assert "MOCK_CONSTRAINT_MISMATCH" in records[1].failure_reason
    assert records[2].run_result == "pass"
    assert records[3].run_result == "fail"


def test_mock_fail_every_greater_than_count():
    result = _mock_execute([{"id": 1}, {"id": 2}], fail_every=5)
    assert result.status == "success"
    assert result.task_report_data.failed == 0


def test_mock_empty_cases():
    result = _mock_execute([], fail_every=1)
    assert result.status == "success"
    assert result.task_report_data.record_count == 0
