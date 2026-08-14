"""executer/report_parser.py 纯函数单元测试。

依赖 openpyxl（executer 包导入链），缺失时整模块跳过。
"""

import pytest

pytest.importorskip("openpyxl")
pytest.importorskip("asyncssh")

from pathlib import Path

from executer.report_parser import (
    _find_latest_xlsx,
    _match_column,
    _norm,
    _truthy_pass,
)


# ── _norm ────────────────────────────────────────────────────────────────────


def test_norm_lowercases_and_collapses():
    assert _norm("  Pass Case  ") == "passcase"


def test_norm_none_returns_empty():
    assert _norm(None) == ""


# ── _match_column ────────────────────────────────────────────────────────────


def test_match_column_exact_alias():
    assert _match_column("运行结果", ("运行结果", "result"))
    assert _match_column("Result", ("result",))


def test_match_column_substring_fallback():
    assert _match_column("运行结果 (pass/fail)", ("运行结果",))


def test_match_column_no_match():
    assert not _match_column("备注", ("运行结果", "result"))


# ── _truthy_pass ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    ["pass", "passed", "success", "通过", "成功", "1", "true", "yes", "PASS"],
)
def test_truthy_pass_recognized(value):
    assert _truthy_pass(value) is True


@pytest.mark.parametrize(
    "value",
    ["fail", "failed", "error", "失败", "未通过", "0", "false", "no", None, ""],
)
def test_truthy_pass_fail_recognized(value):
    assert _truthy_pass(value) is False


def test_truthy_pass_unknown_defaults_fail():
    # ATK 引入未知状态（如 skip）时当前实现保守计 fail
    assert _truthy_pass("skip") is False
    assert _truthy_pass("unknown_status") is False


# ── _find_latest_xlsx ────────────────────────────────────────────────────────


def test_find_latest_xlsx_none_when_missing(tmp_path):
    assert _find_latest_xlsx(tmp_path / "nope") is None


def test_find_latest_xlsx_picks_newest(tmp_path):
    (tmp_path / "a.xlsx").write_bytes(b"")
    (tmp_path / "b.xlsx").write_bytes(b"")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")
    latest = _find_latest_xlsx(tmp_path)
    assert latest is not None
    assert latest.suffix == ".xlsx"
    # 目录内只有两个 xlsx，b 是后写的（mtime 更大）
    assert latest.name in {"a.xlsx", "b.xlsx"}
    assert Path(tmp_path / "c.txt") not in (latest,)
