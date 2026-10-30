#!/usr/bin/env python3
"""把 OperatorTestCoverage 子模块内容分类同步到 .opencode 目录。
脚本读取根目录下的 ``third_party/OperatorTestCoverage``（git 子模块），按子模块内的
目录分类同步：
- ``skills/``    下的全部内容 → ``.opencode/skills/``
- ``agents/``    下的全部内容 → ``.opencode/agents/``
- ``commands/``  下的全部内容 → ``.opencode/commands/``
**同步模式**：
- 默认（拷贝）：若目标已存在同名文件/目录则**直接覆盖**（``shutil.copytree``
  的 ``dirs_exist_ok=True`` 对文件做覆盖、对目录做递归合并），**不**清空目标目录、
  **不**删除目标里子模块没有的其他文件（即子模块只"增量覆盖同名 + 新增缺失"，目标目录
  的其余内容原样保留）。传 ``--clean`` 时，仅删除目标中与源侧同名的技能/agent 条目
  （子目录或文件）后再拷贝，确保这些技能/agent 内部无残留文件，而目标目录里源侧没有的
  其他内容仍原样保留。
- ``--link``（软链接）：按子项粒度创建软链接，``skills/<name>/`` 为目录软链接、
  ``agents/<name>.md`` / ``commands/<name>.md`` 为文件软链接。若目标已存在同名条目
  （文件/目录/旧软链接），先删除再重建。Windows 需开启开发者模式或以管理员运行
  （"Create symbolic links" 用户权利），权限不足时脚本报错提示。
子模块缺失（尚未 init）或 ``skills/``/``agents/``/``commands/`` 都不存在时不做操作。
Python 版本要求：>=3.8（依赖 shutil.copytree dirs_exist_ok 参数）
"""
from __future__ import annotations
import argparse
import os
import shutil
import sys
from pathlib import Path
# Python版本校验
if sys.version_info < (3, 8):
    print("[cov_init] ERROR: 需要 Python >=3.8，当前版本 %s.%s" % sys.version_info[:2])
    sys.exit(1)
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = PROJECT_ROOT / "third_party" / "OperatorTestCoverage"
DEFAULT_SKILLS_DEST = PROJECT_ROOT / ".opencode" / "skills"
DEFAULT_AGENTS_DEST = PROJECT_ROOT / ".opencode" / "agents"
DEFAULT_COMMANDS_DEST = PROJECT_ROOT / ".opencode" / "commands"
IGNORED_NAMES = {".git", "__pycache__", ".DS_Store"}
# (源子目录名, 目标目录名, 文件后缀过滤 or None)
SYNC_CHANNELS = [
    ("skills", "skills", None),
    ("agents", "agents", ".md"),
    ("commands", "commands", ".md"),
]
def _ignore(_directory: str, names: list[str]) -> set[str]:
    """copytree ignore回调：过滤忽略文件"""
    return {name for name in names if name in IGNORED_NAMES}
def _has_content(root: Path) -> bool:
    """目录下是否存在非忽略项（文件或子目录）。"""
    if not root.exists():
        return False
    try:
        return any(child.name not in IGNORED_NAMES for child in root.iterdir())
    except PermissionError:
        print(f"[cov_init] WARN: 无法读取目录 {root}")
        return False
def _count_payload_files(root: Path) -> int:
    """统计源目录内非忽略文件总数（仅统计源侧，不等于实际写入数量）"""
    total = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_NAMES]
        total += len([name for name in filenames if name not in IGNORED_NAMES])
    return total
def _list_names(root: Path, suffix: str | None = None) -> list[str]:
    """列出 root 直接子项名（技能目录名或 agent 文件名）。"""
    if not root.exists():
        return []
    out = []
    for child in sorted(root.iterdir()):
        if child.name in IGNORED_NAMES:
            continue
        if suffix is not None and not child.name.endswith(suffix):
            continue
        out.append(child.name)
    return out
def _list_skill_names(root: Path) -> list[str]:
    """列出 root 直接子目录中含 SKILL.md 的技能目录名（校验技能可被 opencode 发现）。"""
    if not root.exists():
        return []
    out = []
    for child in root.iterdir():
        if child.name in IGNORED_NAMES or not child.is_dir():
            continue
        if (child / "SKILL.md").is_file():
            out.append(child.name)
    return sorted(out)
def _clear_matching(src_sub: Path, dest: Path, suffix: str | None = None) -> None:
    """删除 dest 中与 src_sub 直接子项同名且符合 suffix 过滤的条目，
    保留 dest 内源侧没有的其他内容。"""
    if not dest.exists():
        return
    for child in src_sub.iterdir():
        if child.name in IGNORED_NAMES:
            continue
        if suffix is not None and not child.name.endswith(suffix):
            continue
        target = dest / child.name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
def _check_type_conflicts(src_sub: Path, dest: Path, suffix: str | None = None) -> list[str]:
    conflicts = []
    if not dest.exists():
        return conflicts
    for child in src_sub.iterdir():
        if child.name in IGNORED_NAMES:
            continue
        if suffix is not None and not child.name.endswith(suffix):
            continue
        target = dest / child.name
        if target.exists() and not target.is_symlink():
            if child.is_dir() and target.is_file():
                conflicts.append(f"源 {child} 是目录，但目标 {target} 是文件")
            elif child.is_file() and target.is_dir():
                conflicts.append(f"源 {child} 是文件，但目标 {target} 是目录")
    return conflicts
def _copy_into(src_sub: Path, dest: Path, suffix: str | None = None,
               clean: bool = False, dry_run: bool = False) -> int:
    if not _has_content(src_sub):
        return 0
    src_file_count = _count_payload_files(src_sub)
    if dry_run:
        action = "删除同名条目并同步" if clean else "同步"
        print(f"[cov_init][dry-run] 将{action}目录 {src_sub} -> {dest}，源侧文件总数：{src_file_count}")
        return src_file_count
    conflicts = _check_type_conflicts(src_sub, dest, suffix=suffix)
    if conflicts:
        for c in conflicts:
            print(f"[cov_init] ERROR: 类型冲突: {c}")
        return -1
    if clean:
        _clear_matching(src_sub, dest, suffix=suffix)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_sub, dest, dirs_exist_ok=True, ignore=_ignore)
    return src_file_count
def _make_symlink(src: Path, dest: Path) -> str:
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.is_dir():
            shutil.rmtree(dest)
        action = "replaced"
    else:
        action = "created"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(src, dest, target_is_directory=src.is_dir())
    return action
def sync(
    source: Path,
    skills_dest: Path,
    agents_dest: Path,
    commands_dest: Path,
    clean: bool = False,
    dry_run: bool = False,
    link: bool = False,
) -> int:
    if not source.exists():
        print(f"[cov_init] 子模块目录不存在: {source}")
        print(
            "[cov_init] 请先执行: "
            "git submodule update --init third_party/OperatorTestCoverage"
        )
        return 1
    if not source.is_dir():
        print(f"[cov_init] 源路径不是目录: {source}")
        return 1
    dest_map = {
        "skills": skills_dest,
        "agents": agents_dest,
        "commands": commands_dest,
    }
    any_has_content = False
    for src_name, _dest_name, _suffix in SYNC_CHANNELS:
        if _has_content(source / src_name):
            any_has_content = True
            break
    if not any_has_content:
        print(f"[cov_init] 子模块内 skills/、agents/、commands/ 均为空，跳过: {source}")
        return 0
    if link:
        return _sync_link(source, dest_map, dry_run)
    return _sync_copy(source, dest_map, clean, dry_run)
def _sync_copy(
    source: Path,
    dest_map: dict[str, Path],
    clean: bool = False,
    dry_run: bool = False,
) -> int:
    total_src_files = 0
    for src_name, dest_name, suffix in SYNC_CHANNELS:
        src_sub = source / src_name
        dest = dest_map[dest_name]
        if not _has_content(src_sub):
            print(f"[cov_init] {src_name}: 源 {src_name}/ 为空，跳过: {src_sub}")
            continue
        try:
            n = _copy_into(src_sub, dest, suffix=suffix, clean=clean, dry_run=dry_run)
            if n < 0:
                return 1
            total_src_files += n
            src_names = _list_names(src_sub, suffix=suffix)
            print(
                f"[cov_init] {src_name}: 源侧待同步文件总数 {n} "
                f"{src_sub} -> {dest}"
            )
            if src_name == "skills":
                print(f"[cov_init]   源技能目录列表: {', '.join(src_names)}")
                if not dry_run:
                    found = _list_skill_names(dest)
                    if found:
                        print(f"[cov_init]   目标可发现技能 {len(found)} 个: {', '.join(found)}")
                    else:
                        print("[cov_init]   警告: 目标目录下未发现 SKILL.md，opencode 无法加载技能")
            else:
                print(f"[cov_init]   源 {src_name} 文件列表: {', '.join(src_names)}")
        except Exception as e:
            print(f"[cov_init] ERROR: 拷贝 {src_name} 失败: {e}")
            return 1
    if clean:
        print(f"[cov_init] 合计源侧待同步文件 {total_src_files} 个（已删除目标中同名条目，其余内容保留）")
    else:
        print(f"[cov_init] 合计源侧待同步文件 {total_src_files} 个（同名覆盖、未清空目标、未动其他文件）")
    if dry_run:
        print("[cov_init] DRY-RUN: 仅预览，没有执行任何文件拷贝")
    return 0
def _sync_link(
    source: Path,
    dest_map: dict[str, Path],
    dry_run: bool = False,
) -> int:
    total_created = 0
    total_replaced = 0
    total_failed = 0
    for src_name, dest_name, suffix in SYNC_CHANNELS:
        src_sub = source / src_name
        dest = dest_map[dest_name]
        if not _has_content(src_sub):
            print(f"[cov_init] {src_name}: 源 {src_name}/ 为空，跳过: {src_sub}")
            continue
        names = _list_names(src_sub, suffix=suffix)
        conflicts = _check_type_conflicts(src_sub, dest, suffix=suffix)
        if conflicts:
            for c in conflicts:
                print(f"[cov_init] ERROR: 类型冲突: {c}")
            return 1
        if dry_run:
            print(
                f"[cov_init][dry-run] 将为 {len(names)} 个条目创建软链接 "
                f"{src_sub} -> {dest}"
            )
            continue
        for name in names:
            src = src_sub / name
            target = dest / name
            try:
                action = _make_symlink(src, target)
            except OSError as e:
                print(f"[cov_init] {src_name}/{name}: 创建软链接失败: {e}")
                print("[cov_init]   Windows 需开启开发者模式或以管理员运行")
                total_failed += 1
                continue
            if action == "created":
                total_created += 1
            else:
                total_replaced += 1
            print(f"[cov_init] {src_name}/{name}: {action} -> {src}")
        print(f"[cov_init]   {src_name} 软链接: {len(names)} 个")
    if total_failed:
        print(
            f"[cov_init] ERROR: 新建 {total_created} 个, 替换 {total_replaced} 个, "
            f"失败 {total_failed} 个"
        )
        return 1
    print(
        f"[cov_init] 合计: 新建 {total_created} 个软链接, "
        f"替换 {total_replaced} 个（同名覆盖、未清空目标、未动其他文件）"
    )
    if dry_run:
        print("[cov_init] DRY-RUN: 仅预览，没有执行任何软链接操作")
    return 0
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "把 third_party/OperatorTestCoverage 子模块分类同步到 .opencode： "
            "skills/ -> .opencode/skills/，agents/ -> .opencode/agents/，"
            "commands/ -> .opencode/commands/；"
            "默认拷贝模式（同名覆盖、不清空目标、不动目标其他文件，--clean 仅删除"
            "目标中同名条目后再拷贝）；--link 改用软链接模式（按子项粒度创建"
            "目录/文件软链接，同名先删后建）。Python >=3.8"
        )
    )
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help="子模块源目录（默认 third_party/OperatorTestCoverage）",
    )
    parser.add_argument(
        "--skills-dest",
        default=str(DEFAULT_SKILLS_DEST),
        help="技能目标目录（默认 .opencode/skills）",
    )
    parser.add_argument(
        "--agents-dest",
        default=str(DEFAULT_AGENTS_DEST),
        help="agent 目标目录（默认 .opencode/agents）",
    )
    parser.add_argument(
        "--commands-dest",
        default=str(DEFAULT_COMMANDS_DEST),
        help="命令目标目录（默认 .opencode/commands）",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="拷贝前删除目标中与源侧同名的条目（其余内容保留），避免残留过期文件",
    )
    parser.add_argument(
        "--link",
        action="store_true",
        help="软链接模式：按子项粒度创建软链接（skills 目录链接、agents/commands 文件链接），"
             "同名先删后建；默认（不传）为拷贝模式",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式：只打印将要执行操作，不实际拷贝/创建文件",
    )
    args = parser.parse_args()
    if args.link and args.clean:
        parser.error("--link 与 --clean 互斥：软链接模式按子项先删后建，无需 clean")
    return sync(
        Path(args.source).resolve(),
        Path(args.skills_dest).resolve(),
        Path(args.agents_dest).resolve(),
        Path(args.commands_dest).resolve(),
        clean=args.clean,
        dry_run=args.dry_run,
        link=args.link,
    )
if __name__ == "__main__":
    sys.exit(main())
