#!/usr/bin/env python3
"""把 OperatorTestCoverage 子模块内容同步到 .opencode/skills 目录。

脚本读取根目录下的 ``third_party/OperatorTestCoverage``（git 子模块），
若目录非空则把其中全部内容拷贝到 ``.opencode/skills/``，使 opencode 能按
``<name>/SKILL.md`` 发现技能。子模块缺失（尚未 init）或为空时不做拷贝。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = PROJECT_ROOT / "third_party" / "OperatorTestCoverage"
DEFAULT_DEST = PROJECT_ROOT / ".opencode" / "skills"

IGNORED_NAMES = {".git", "__pycache__", ".DS_Store", "README.md"}


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORED_NAMES}


def _has_content(root: Path) -> bool:
    return any(child.name not in IGNORED_NAMES for child in root.iterdir())


def _count_payload_files(root: Path) -> int:
    total = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_NAMES]
        total += len([name for name in filenames if name not in IGNORED_NAMES])
    return total


def _list_skill_names(root: Path) -> list[str]:
    return sorted(
        path.parent.name
        for path in root.rglob("SKILL.md")
        if not any(part in IGNORED_NAMES for part in path.parts)
    )


def sync(source: Path, dest: Path, clean: bool) -> int:
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
    if not _has_content(source):
        print(f"[cov_init] 子模块目录为空，跳过拷贝: {source}")
        return 0

    if clean and dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, dirs_exist_ok=True, ignore=_ignore)

    copied = _count_payload_files(source)
    skills = _list_skill_names(dest)
    print(f"[cov_init] 已拷贝 {copied} 个文件: {source} -> {dest}")
    if skills:
        print(f"[cov_init] 发现 {len(skills)} 个技能: {', '.join(skills)}")
    else:
        print("[cov_init] 警告: 目标目录下未发现 SKILL.md，opencode 无法加载技能")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 third_party/OperatorTestCoverage 子模块内容同步到 .opencode/skills。"
    )
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help="子模块源目录（默认 third_party/OperatorTestCoverage）",
    )
    parser.add_argument(
        "--dest",
        default=str(DEFAULT_DEST),
        help="目标技能目录（默认 .opencode/skills）",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="拷贝前先清空目标目录，避免残留过期技能",
    )
    args = parser.parse_args()
    return sync(Path(args.source).resolve(), Path(args.dest).resolve(), args.clean)


if __name__ == "__main__":
    sys.exit(main())
