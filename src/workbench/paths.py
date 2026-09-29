"""项目根定位与 run 根内路径安全解析（白名单 + realpath 防逃逸）。"""
import re
from pathlib import Path, PurePosixPath

# \w 含 Unicode：中文算子名的 run_id（init_run 以文档名生成）也能访问详情；
# 结构风险（路径分隔符/控制字符/glob 元字符）由显式字符类排除，另见 .. 检查。
RUN_ID_RE = re.compile(r"^[\w.\-]+$")
ITER_DIR_RE = re.compile(r"^iter_(\d{3,})$")

USER_SKILL_DIRS = (".config/opencode/skills", ".agents/skills", ".claude/skills")


def resolve_asset(base, relative):
    """Resolve only within a fixed asset collection, including its root symlink."""
    base = Path(base).absolute()
    if base.resolve() != base:
        raise PathEscapeError("资产目录指向其他位置")
    return resolve_within(base, relative)


def resolve_user_skill(home, directory, name):
    """Independent user-level allowlist; never accepts arbitrary client paths."""
    if directory not in USER_SKILL_DIRS or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise PathEscapeError("不支持的用户技能位置")
    return resolve_asset(Path(home).resolve() / directory, name + "/SKILL.md")


class PathEscapeError(ValueError):
    """路径试图逃逸出允许的根目录。"""


def project_root(explicit=None):
    # type: (str) -> Path
    if explicit:
        return Path(explicit).resolve()
    # 本文件位于 <root>/src/workbench/paths.py
    return Path(__file__).resolve().parents[2]


def runs_dir(root):
    # type: (Path) -> Path
    return root / "runs"


def resolve_run(root, run_id):
    # type: (Path, str) -> Path
    """把 run_id 解析为 runs/ 下的真实目录；不合法或不存在抛异常。"""
    if not run_id or not RUN_ID_RE.match(run_id) or ".." in run_id:
        raise PathEscapeError("非法 run_id: %r" % run_id)
    base = runs_dir(root).resolve()
    candidate = (base / run_id).resolve()
    if not _is_within(candidate, base):
        raise PathEscapeError("run_id 逃逸: %r" % run_id)
    if not candidate.is_dir():
        raise FileNotFoundError("run 不存在: %s" % run_id)
    return candidate


def iter_dirs(run_root):
    # type: (Path) -> list
    """返回 [(n, Path), ...]，按轮次升序。"""
    found = []
    if run_root.is_dir():
        for child in run_root.iterdir():
            m = ITER_DIR_RE.match(child.name)
            if m and child.is_dir():
                found.append((int(m.group(1)), child))
    found.sort(key=lambda item: item[0])
    return found


def iter_dir_for(run_root, n):
    # type: (Path, int) -> Path
    for num, path in iter_dirs(run_root):
        if num == n:
            return path
    raise FileNotFoundError("iter_%03d 不存在" % n)


def resolve_within(run_root, rel_path):
    # type: (Path, str) -> Path
    """把相对路径解析到 run 根内；拒绝绝对路径、..、符号链接逃逸。"""
    if not rel_path:
        raise PathEscapeError("空路径")
    pure = PurePosixPath(rel_path.replace("\\", "/"))
    if pure.is_absolute():
        raise PathEscapeError("拒绝绝对路径: %r" % rel_path)
    if any(part == ".." for part in pure.parts):
        raise PathEscapeError("拒绝 .. 路径: %r" % rel_path)
    base = run_root.resolve()
    candidate = (base / str(pure)).resolve()
    if not _is_within(candidate, base):
        raise PathEscapeError("路径逃逸 run 根: %r" % rel_path)
    if not candidate.exists():
        raise FileNotFoundError("文件不存在: %s" % rel_path)
    return candidate


def rel_to(path, root):
    # type: (Path, Path) -> str
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _is_within(candidate, base):
    # type: (Path, Path) -> bool
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False
