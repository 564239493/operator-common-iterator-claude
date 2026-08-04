"""guard_project_writes.py 权限钩子单元测试。

覆盖问题 4 的三个目标缺陷（当前为红，修复后转绿）：
1. WRITE_OR_DELETE 关键词误伤只读命令（如 grep "mkdir"）
2. INLINE_PYTHON 可被 `python -X utf8 -c` / `py -c` 绕过
3. read_scope 对 run_id 不做格式校验（"*" 残留会锁死会话）

以及问题 8 引入的维护期豁免前缀机制（回归保护）。
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "guard_project_writes_under_test",
        ROOT / ".claude" / "hooks" / "guard_project_writes.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _payload(tool_name: str, tool_input: dict, session_id: str = "test-session") -> dict:
    return {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": session_id,
        "cwd": str(ROOT),
    }


# ── 基础读写边界 ────────────────────────────────────────────────────────────


def test_read_always_allowed():
    payload = _payload("Read", {"file_path": "README.md"})
    assert guard.guard_file_tool(payload, ROOT) is None


def test_protected_write_denied():
    payload = _payload("Write", {"file_path": "executer/ssh.py"})
    reason = guard.guard_file_tool(payload, ROOT)
    assert reason is not None and "受保护路径" in reason


def test_protected_write_denied_for_generators():
    payload = _payload("Write", {"file_path": "agent/generators/facade.py"})
    reason = guard.guard_file_tool(payload, ROOT)
    assert reason is not None and "受保护路径" in reason


# ── 问题 8：维护期豁免前缀机制（默认空 = 保护语义不变） ──────────────────────


def test_exempt_prefixes_default_empty():
    assert guard.WRITE_EXEMPT_PREFIXES == ()


def test_exempt_prefix_releases_specific_path(monkeypatch):
    monkeypatch.setattr(
        guard, "WRITE_EXEMPT_PREFIXES", ("executer/runner.py",)
    )
    payload = _payload("Write", {"file_path": "executer/runner.py"})
    assert guard.guard_file_tool(payload, ROOT) is None
    # 同目录其他文件仍受保护
    payload_other = _payload("Write", {"file_path": "executer/ssh.py"})
    assert guard.guard_file_tool(payload_other, ROOT) is not None


# ── 问题 4-1：关键词误伤只读命令 ─────────────────────────────────────────────


def test_shell_grep_mkdir_not_blocked():
    """只读搜索命令（grep 引号内含 mkdir）不应被写守卫误伤。"""
    payload = _payload(
        "Bash", {"command": 'grep -n "mkdir" scripts/init_run.py'}
    )
    assert guard.guard_shell(payload, ROOT) is None


def test_shell_grep_rm_not_blocked():
    payload = _payload("Bash", {"command": 'grep -rn "rm " scripts/'})
    assert guard.guard_shell(payload, ROOT) is None


def test_shell_git_log_format_md_not_blocked():
    payload = _payload("Bash", {"command": 'git log --format="%h %s"'})
    assert guard.guard_shell(payload, ROOT) is None


@pytest.mark.xfail(reason="问题4修复目标：绑定态下 grep 引号内 mkdir 被误伤", strict=True)
def test_shell_grep_mkdir_not_blocked_when_bound(scope_payload):
    """绑定状态下只读搜索命令同样不应被误伤（问题 4-1 目标）。"""
    payload = _payload(
        "Bash", {"command": 'grep -n "mkdir" scripts/init_run.py'}, session_id="s6"
    )
    scope = guard.scope_file(payload, ROOT)
    scope.write_text(json.dumps({"run_id": "aclnnFoo-123"}), encoding="utf-8")
    assert guard.guard_shell(payload, ROOT) is None


# ── 问题 4-2：内联 Python 拦截缺口 ───────────────────────────────────────────


def test_shell_inline_python_c_blocked():
    payload = _payload("Bash", {"command": 'python -c "print(1)"'})
    assert guard.guard_shell(payload, ROOT) is not None


def test_shell_inline_python_dash_blocked():
    payload = _payload("Bash", {"command": "python - <<'EOF'\nprint(1)\nEOF"})
    assert guard.guard_shell(payload, ROOT) is not None


@pytest.mark.xfail(reason="问题4修复目标：python -X utf8 -c 可绕过 INLINE_PYTHON", strict=True)
def test_shell_inline_python_X_utf8_blocked():
    """`python -X utf8 -c` 不应绕过内联 Python 拦截。"""
    payload = _payload("Bash", {"command": 'python -X utf8 -c "print(1)"'})
    assert guard.guard_shell(payload, ROOT) is not None


@pytest.mark.xfail(reason="问题4修复目标：py -c 未被识别", strict=True)
def test_shell_py_c_blocked():
    """`py -c` 启动器同样应被拦截。"""
    payload = _payload("Bash", {"command": 'py -c "print(1)"'})
    assert guard.guard_shell(payload, ROOT) is not None


@pytest.mark.xfail(reason="问题4修复目标：pythonw -c 未被识别", strict=True)
def test_shell_pythonw_c_blocked():
    payload = _payload("Bash", {"command": 'pythonw -c "print(1)"'})
    assert guard.guard_shell(payload, ROOT) is not None


# ── 问题 4-3：run_id 格式校验 ────────────────────────────────────────────────


@pytest.fixture
def scope_payload(tmp_path, monkeypatch):
    """把 scope 文件重定向到临时目录，避免污染 .claude/runtime/。"""

    def _fake_scope_file(payload, root):
        session = str(payload.get("session_id") or "unknown")
        return tmp_path / f"{session}.json"

    monkeypatch.setattr(guard, "scope_file", _fake_scope_file)
    return _payload


def test_run_id_normal_returned(scope_payload):
    payload = _payload("Write", {"file_path": "README.md"}, session_id="s1")
    scope = guard.scope_file(payload, ROOT)
    scope.write_text(json.dumps({"run_id": "aclnnFoo-123"}), encoding="utf-8")
    assert guard.read_scope(payload, ROOT) == "aclnnFoo-123"


@pytest.mark.xfail(reason="问题4修复目标：run_id 无格式校验，'*' 残留会锁死会话", strict=True)
def test_run_id_star_treated_as_unbound(scope_payload):
    """残留的 `{"run_id": "*"}` 必须视为未绑定，而不是锁死整个会话。"""
    payload = _payload("Write", {"file_path": "README.md"}, session_id="s2")
    scope = guard.scope_file(payload, ROOT)
    scope.write_text(json.dumps({"run_id": "*"}), encoding="utf-8")
    assert guard.read_scope(payload, ROOT) is None


@pytest.mark.xfail(reason="问题4修复目标：空 run_id 应视为未绑定", strict=True)
def test_run_id_empty_treated_as_unbound(scope_payload):
    payload = _payload("Write", {"file_path": "README.md"}, session_id="s3")
    scope = guard.scope_file(payload, ROOT)
    scope.write_text(json.dumps({"run_id": ""}), encoding="utf-8")
    assert guard.read_scope(payload, ROOT) is None


def test_run_id_missing_file_is_unbound(scope_payload):
    payload = _payload("Write", {"file_path": "README.md"}, session_id="s4")
    assert guard.read_scope(payload, ROOT) is None


# ── 常规命令放行 ─────────────────────────────────────────────────────────────


def test_shell_plain_ls_allowed():
    payload = _payload("Bash", {"command": "ls"})
    assert guard.guard_shell(payload, ROOT) is None


def test_shell_run_reference_binds_scope(scope_payload):
    payload = _payload(
        "Bash",
        {"command": "ls runs/aclnnFoo-123/"},
        session_id="s5",
    )
    assert guard.guard_shell(payload, ROOT) is None
    assert guard.read_scope(payload, ROOT) == "aclnnFoo-123"
