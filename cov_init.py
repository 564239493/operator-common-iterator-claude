#!/usr/bin/env python3
"""把 OperatorTestCoverage 子模块内容分类同步到 .opencode 目录。
脚本读取根目录下的 ``third_party/OperatorTestCoverage``（git 子模块），按子模块内的
目录分类拷贝：
- ``skills/``  下的全部内容 → ``.opencode/skills/``
- ``agents/``  下的全部内容 → ``.opencode/agents/``

**覆盖语义**：默认拷贝时若目标已存在同名文件/目录则**直接覆盖**（``shutil.copytree``
的 ``dirs_exist_ok=True`` 对文件做覆盖、对目录做递归合并），**不**清空目标目录、
**不**删除目标里子模块没有的其他文件（即子模块只"增量覆盖同名 + 新增缺失"，目标目录
的其余内容原样保留）。传 ``--clean`` 时，仅删除目标中与源侧同名的技能/agent 条目
（子目录或文件）后再拷贝，确保这些技能/agent 内部无残留文件，而目标目录里源侧没有的
其他内容仍原样保留。子模块缺失（尚未 init）或 ``skills/``/``agents/`` 都不存在时不做拷贝。

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
IGNORED_NAMES = {".git", "__pycache__", ".DS_Store"}


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
    """列出 root 下含 SKILL.md 的技能目录名（校验技能可被 opencode 发现）。"""
    if not root.exists():
        return []
    return sorted(
        {
            path.parent.name
            for path in root.rglob("SKILL.md")
            if not any(part in IGNORED_NAMES for part in path.parts)
        }
    )


def _clear_matching(src_sub: Path, dest: Path) -> None:
    """删除 dest 中与 src_sub 直接子项同名的条目（旧技能目录/旧 agent 文件），
    保留 dest 内源侧没有的其他内容。"""
    if not dest.exists():
        return
    for child in src_sub.iterdir():
        if child.name in IGNORED_NAMES:
            continue
        target = dest / child.name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _copy_into(src_sub: Path, dest: Path, clean: bool = False, dry_run: bool = False) -> int:
    """把 src_sub 目录内容拷进 dest。默认同名覆盖、不清空 dest、不动 dest 其他文件；
    clean=True 时先删除 dest 中与 src_sub 同名的条目再拷贝（dest 其余内容保留）。
    返回源侧待同步文件总数。dry_run=True 只遍历打印，不拷贝。
    """
    if not _has_content(src_sub):
        return 0
    src_file_count = _count_payload_files(src_sub)
    if dry_run:
        action = "删除同名条目并同步" if clean else "同步"
        print(f"[cov_init][dry-run] 将{action}目录 {src_sub} -> {dest}，源侧文件总数：{src_file_count}")
        return src_file_count

    if clean:
        _clear_matching(src_sub, dest)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_sub, dest, dirs_exist_ok=True, ignore=_ignore)
    return src_file_count


def sync(
    source: Path,
    skills_dest: Path,
    agents_dest: Path,
    clean: bool = False,
    dry_run: bool = False,
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

    src_skills = source / "skills"
    src_agents = source / "agents"

    if not _has_content(src_skills) and not _has_content(src_agents):
        print(f"[cov_init] 子模块内 skills/ 与 agents/ 均为空，跳过拷贝: {source}")
        return 0

    total_src_files = 0

    # skills/ -> .opencode/skills/
    if _has_content(src_skills):
        try:
            n = _copy_into(src_skills, skills_dest, clean=clean, dry_run=dry_run)
            total_src_files += n
            src_names = _list_names(src_skills)
            print(
                f"[cov_init] skills: 源侧待同步文件总数 {n} "
                f"{src_skills} -> {skills_dest}"
            )
            print(f"[cov_init]   源技能目录列表: {', '.join(src_names)}")
            if not dry_run:
                found = _list_skill_names(skills_dest)
                if found:
                    print(f"[cov_init]   目标可发现技能 {len(found)} 个: {', '.join(found)}")
                else:
                    print("[cov_init]   警告: 目标目录下未发现 SKILL.md，opencode 无法加载技能")
        except Exception as e:
            print(f"[cov_init] ERROR: 拷贝 skills 失败: {e}")
            return 1
    else:
        print(f"[cov_init] skills: 源 skills/ 为空，跳过: {src_skills}")

    # agents/ -> .opencode/agents/
    if _has_content(src_agents):
        try:
            n = _copy_into(src_agents, agents_dest, clean=clean, dry_run=dry_run)
            total_src_files += n
            src_names = _list_names(src_agents, suffix=".md")
            print(
                f"[cov_init] agents: 源侧待同步文件总数 {n} "
                f"{src_agents} -> {agents_dest}"
            )
            print(f"[cov_init]   源 agent md 文件列表: {', '.join(src_names)}")
        except Exception as e:
            print(f"[cov_init] ERROR: 拷贝 agents 失败: {e}")
            return 1
    else:
        print(f"[cov_init] agents: 源 agents/ 为空，跳过: {src_agents}")

    if clean:
        print(f"[cov_init] 合计源侧待同步文件 {total_src_files} 个（已删除目标中同名技能/agent，其余内容保留）")
    else:
        print(f"[cov_init] 合计源侧待同步文件 {total_src_files} 个（同名覆盖、未清空目标、未动其他文件）")
    if dry_run:
        print("[cov_init] DRY-RUN: 仅预览，没有执行任何文件拷贝")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "把 third_party/OperatorTestCoverage 子模块分类同步到 .opencode： "
            "skills/ -> .opencode/skills/，agents/ -> .opencode/agents/；"
            "默认同名覆盖、不清空目标、不动目标其他文件（--clean 仅删除目标中同名"
            "技能/agent 后再拷贝，其余内容保留）。Python >=3.8"
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
        "--clean",
        action="store_true",
        help="拷贝前删除目标中与源侧同名的技能/agent 条目（其余内容保留），避免残留过期文件",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式：只打印将要执行操作，不实际拷贝文件",
    )
    args = parser.parse_args()
    return sync(
        Path(args.source).resolve(),
        Path(args.skills_dest).resolve(),
        Path(args.agents_dest).resolve(),
        clean=args.clean,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
