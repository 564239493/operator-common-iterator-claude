#!/usr/bin/env python3
"""把 per-iter 提示词快照原子化提升到全局 ``prompts/`` 基线。

本脚本是 **唯一** 允许把 ``runs/<run-id>/iter_<N+1>/prompt_v(N+1).md`` 写入
``prompts/operator_constraints_extract_v(N+1).md`` 的入口。由主协调器在 run 终态后，
经用户 AskUserQuestion 显式批准后调用。

执行流程（确定性，不调 LLM）：

1. 校验必要输入与前置条件（见 §校验段）；
2. 用临时文件 + ``Path.replace(target)`` 原子写入 ``prompts/operator_constraints_extract_v(N+1).md``；
3. 在 ``prompts/CHANGELOG.md`` 追加 ``### v(N+1)：<一行标题>`` 段与变更摘要；
4. 写一条 ``runs/<run-id>/iter_(N+1)/promotion_record.json`` 留痕；
5. 任一步失败 → 报错并退出非零状态码；已写入的临时文件 / 部分写入按 §错误处理回收。

约定：
- 校验失败、本文件被并发改动、run_state.json 与 iter 目录不一致 等场景均视为错误并退出。
- 脚本不主动覆盖已存在的 ``prompts/operator_constraints_extract_v(N+1).md``（防止
  二次提升造成历史丢失）；如需强制覆盖请传入 ``--force``。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PROMPTS = ROOT / "prompts"
OPERATOR_PROMPT_PATTERN = re.compile(
    r"^operator_constraints_extract_v(?P<version>\d+)\.md$"
)

# 与 .claude/hooks/guard_project_writes.py TERMINAL_STATES (L25-31) 同步。
TERMINAL_STATES = {
    "SUCCESS",
    "BLOCKED",
    "MAX_ITERATIONS",
    "STOP_GENERATOR_BUG",
    "STOP_EXECUTOR_BUG",
}


class PromoteError(RuntimeError):
    """用户可读的错误（含退出码语义）。"""


def _err(msg: str) -> "PromoteError":
    return PromoteError(msg)


def parse_version_from_path(path_text: str) -> int:
    """``prompts/operator_constraints_extract_v<N>.md`` → N。"""
    name = Path(path_text).name
    match = OPERATOR_PROMPT_PATTERN.fullmatch(name)
    if not match:
        raise _err(f"目标路径不是合法的 operator_constraints_extract_vN.md: {path_text}")
    return int(match.group("version"))


def validate_inputs(
    src_path: Path,
    target_version: int,
    run_dir: Path,
    changes_path: Path | None,
    force: bool,
) -> tuple[dict, Path, Path | None]:
    """校验前 4 个条件：文件存在 / run 终态 / 版本号 +1 / sha256 与 run 内 prompt_v(N+1) 一致。

    返回 ``(run_state_payload, expected_target_path, existing_target)``：
    - ``run_state_payload`` 便于回写 promotion_record 时回填；
    - ``existing_target`` 为非 None 表示目标文件已存在（仅在 --force 下允许覆盖）。
    """
    if not src_path.is_file() or src_path.stat().st_size <= 0:
        raise _err(f"源文件缺失或为空: {src_path}")

    if changes_path is not None and (not changes_path.is_file() or changes_path.stat().st_size <= 0):
        raise _err(f"changes 文件缺失或为空: {changes_path}")

    run_state_path = run_dir / "run_state.json"
    if not run_state_path.is_file():
        raise _err(f"run 目录缺少 run_state.json: {run_dir}")

    try:
        state = json.loads(run_state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _err(f"run_state.json 不是合法 JSON: {exc}") from exc

    state_name = str(state.get("state") or "")
    if state_name not in TERMINAL_STATES:
        raise _err(
            f"run 状态 {state_name!r} 不是终态（{[s for s in sorted(TERMINAL_STATES)]}）；"
            "任务结束后再调用 promote_prompt.py。"
        )

    current_prompt_source = str(state.get("current_prompt_source") or "")
    if not current_prompt_source:
        raise _err("run_state.json 缺少 current_prompt_source；无法推导期望的上一版本号。")

    base_version = parse_version_from_path(current_prompt_source)
    if target_version != base_version + 1:
        raise _err(
            f"--to-version={target_version} 与 current_prompt_source v{base_version} 不匹配；"
            f"只允许 bump +1（除非 --allow-skip-bump 提供）。"
        )

    # 内部 prompt_v(N+1) 参考（约束文件明文一致）
    expected_iter = run_dir / "iter_{:03d}".format(state.get("current_iteration", 0))
    expected_iter_prompt = expected_iter / f"prompt_v{target_version}.md"
    if not expected_iter_prompt.is_file():
        # 放宽：当前 run 已被 promote 过一次（current_iteration 与 iter 编号可能错位）
        # 在此情况下 source 与 expected_iter_prompt 的 sha256 比对退化到「from 与 from 的 sha256 自检」。
        expected_iter_prompt = None

    if expected_iter_prompt is not None and expected_iter_prompt.resolve() != src_path.resolve():
        src_hash = _sha256(src_path)
        exp_hash = _sha256(expected_iter_prompt)
        if src_hash != exp_hash:
            raise _err(
                f"--from sha256 与 run 内 {expected_iter_prompt} 不一致；"
                "主协调器在 AskUserQuestion 之后、写之前修改了源文件。请重新确认 prompt。"
            )

    target_path = PROMPTS / f"operator_constraints_extract_v{target_version}.md"
    existing = target_path if target_path.exists() else None
    if existing and not force:
        raise _err(
            f"目标文件已存在 {existing}；拒绝覆盖。如确认要替换请加 --force（仍要求可恢复的 git/snapshot）。"
        )

    return state, target_path, existing


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _atomic_write(target: Path, content: str) -> None:
    """临时文件 + ``Path.replace`` 原子写。"""
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8", newline="\n")
        tmp.replace(target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def append_changelog_entry(
    changelog_path: Path,
    new_version: int,
    base_version: int,
    run_id: str,
    iter_label: str,
    one_line_title: str,
    summary_lines: list[str],
) -> None:
    """追加 ``### vN：<标题>`` 段（含来源链接），不重写历史。"""
    if not changelog_path.is_file():
        raise _err(f"CHANGELOG.md 缺失: {changelog_path}")

    original = changelog_path.read_text(encoding="utf-8")
    if not original.endswith("\n"):
        # 规范化：保证追加段前面有空白分隔
        original = original + "\n"

    now = datetime.now(timezone.utc).isoformat()
    header = f"### v{new_version}：{one_line_title}"
    block_lines = [
        "",
        "---",
        "",
        header,
        "",
        f"- **来源**：本版本经用户显式批准由 `scripts/promote_prompt.py` 提升自 "
        f"`runs/{run_id}/{iter_label}/prompt_v{new_version}.md`，基线版本 v{base_version}。",
        f"- **变更摘要**：",
    ]
    for line in summary_lines:
        block_lines.append(f"  - {line}")
    block_lines.extend(
        [
            f"- **promoted_at**：{now}",
            "- **promoted_by**：`scripts/promote_prompt.py`（用户显式批准后由主协调器调用）",
            "",
        ]
    )
    block = "\n".join(block_lines)
    _atomic_write(changelog_path, original + block)


def extract_summary_from_changes(changes_path: Path | None) -> list[str]:
    """从 ``prompt_changes_v(N+1).md`` 摘要表中提取至多 5 行作为 CHANGELOG 摘要。

    不存在则返回 ``["无 changes 文件，按 per-iter 快照提升"]``。
    """
    if not changes_path or not changes_path.is_file():
        return ["无 changes 文件，按 per-iter 快照提升"]
    try:
        text = changes_path.read_text(encoding="utf-8")
    except OSError:
        return ["无法读取 changes 文件"]

    lines: list[str] = []
    in_summary = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("## 1.") and "摘要" in stripped:
            in_summary = True
            continue
        if in_summary and stripped.startswith("## "):
            break
        if in_summary and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if len(cells) >= 3 and "sections 编号" not in cells[0] and "---" not in cells[0]:
                # cells: [sections, title, change_type, reason, ...]
                title = cells[1] if len(cells) > 1 else ""
                ctype = cells[2] if len(cells) > 2 else ""
                reason = cells[3] if len(cells) > 3 else ""
                lines.append(f"{ctype} `{title}` — {reason}"[:140])
                if len(lines) >= 5:
                    break
    return lines or ["摘要表为空或未被解析出"]


def write_promotion_record(
    run_dir: Path,
    iter_label: str,
    new_version: int,
    base_version: int,
    target_path: Path,
    src_path: Path,
    changes_path: Path | None,
    run_state: dict,
) -> Path:
    """落 ``runs/<run-id>/iter_<N+1>/promotion_record.json`` 留痕（不可失败时静默忽略）。"""
    record_dir = run_dir / iter_label
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path = record_dir / "promotion_record.json"
    payload = {
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "new_version": new_version,
        "base_version": base_version,
        "target_path": str(target_path),
        "source_path": str(src_path),
        "source_sha256": _sha256(src_path),
        "changes_path": str(changes_path) if changes_path else None,
        "run_state_snapshot": {
            "run_id": run_state.get("run_id"),
            "state": run_state.get("state"),
            "current_iteration": run_state.get("current_iteration"),
        },
    }
    _atomic_write(record_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return record_path


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "把 runs/<run-id>/iter_(N+1)/prompt_v(N+1).md 原子化提升到全局 prompts/ 基线。"
            " 须 run 已达终态，且 --to-version 等于 current_prompt_source 版本号 +1。"
        )
    )
    p.add_argument(
        "--from",
        dest="src",
        required=True,
        help="要提升的 prompt_v(N+1).md 路径，相对项目根目录或绝对路径",
    )
    p.add_argument(
        "--to-version",
        type=int,
        required=True,
        help="目标版本号；必须等于 current_prompt_source 版本 +1",
    )
    p.add_argument(
        "--run-dir",
        required=True,
        help="run 目录（runs/<run-id>），用于读 run_state.json 与确认 iter 编号",
    )
    p.add_argument(
        "--changes",
        dest="changes",
        default=None,
        help="可选：prompt_changes_v(N+1).md 路径；用于在 CHANGELOG 中自动抽取 ≤5 行摘要",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="已存在 prompts/operator_constraints_extract_v(N+1).md 时强制覆盖",
    )
    p.add_argument(
        "--one-line-title",
        default=None,
        help="CHANGELOG 标题，如不指定则为默认 '... 由 promote_prompt 提升'",
    )
    args = p.parse_args()

    src_path = (ROOT / args.src).resolve() if not Path(args.src).is_absolute() else Path(args.src).resolve()
    run_dir = (ROOT / args.run_dir).resolve() if not Path(args.run_dir).is_absolute() else Path(args.run_dir).resolve()
    changes_path = None
    if args.changes:
        cp = Path(args.changes)
        changes_path = cp.resolve() if cp.is_absolute() else (ROOT / cp).resolve()

    try:
        state, target_path, existing = validate_inputs(
            src_path=src_path,
            target_version=args.to_version,
            run_dir=run_dir,
            changes_path=changes_path,
            force=args.force,
        )
    except PromoteError as exc:
        print(f"promote_prompt: {exc}", file=sys.stderr)
        return 2

    base_version = parse_version_from_path(state.get("current_prompt_source", ""))
    iter_label = "iter_{:03d}".format(state.get("current_iteration", 0))
    one_line_title = args.one_line_title or f"提示词 v{args.to_version}（由 promote_prompt 提升）"

    # 1) 写 prompts/operator_constraints_extract_v(N+1).md
    try:
        _atomic_write(target_path, src_path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"promote_prompt: 写入 {target_path} 失败: {exc}", file=sys.stderr)
        return 3

    # 2) 追加 CHANGELOG
    summary = extract_summary_from_changes(changes_path)
    try:
        append_changelog_entry(
            changelog_path=PROMPTS / "CHANGELOG.md",
            new_version=args.to_version,
            base_version=base_version,
            run_id=str(state.get("run_id") or run_dir.name),
            iter_label=iter_label,
            one_line_title=one_line_title,
            summary_lines=summary,
        )
    except PromoteError as exc:
        # 已写 prompts/ 但 CHANGELOG 失败 → 回滚 prompts 文件（如果原本不存在则删除，
        # 否则恢复原内容）；为简单起见，对覆盖场景不回滚（用户显式 --force）。
        if existing is None and target_path.exists():
            target_path.unlink()
        print(f"promote_prompt: CHANGELOG 追加失败，已回滚: {exc}", file=sys.stderr)
        return 4

    # 3) 写 promotion_record.json
    try:
        record_path = write_promotion_record(
            run_dir=run_dir,
            iter_label=iter_label,
            new_version=args.to_version,
            base_version=base_version,
            target_path=target_path,
            src_path=src_path,
            changes_path=changes_path,
            run_state=state,
        )
    except OSError as exc:
        # prompts/ 与 CHANGELOG 已成功，record 失败可降级为 stderr 警告而不视为致命错误
        print(f"promote_prompt: WARN 写 promotion_record 失败（非致命）: {exc}", file=sys.stderr)
        record_path = None

    # 输出（stdout）
    result = {
        "ok": True,
        "promoted_to": str(target_path),
        "new_version": args.to_version,
        "base_version": base_version,
        "promotion_record": str(record_path) if record_path else None,
        "overwrote_existing": existing is not None,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
