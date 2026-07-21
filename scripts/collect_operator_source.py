#!/usr/bin/env python3
"""收集某算子在 ``operators-src`` 树内的全量源码，产快照 + manifest + 报告。

四阶段（全程 ``seen`` 去重、防环）：

0. 定位算子目录：复用 :mod:`locate_operator_source.locate`（op_api_list.md
   表格映射，退化命名 glob）。
1. SEED 全收算子目录：对每个 ``operator_dir`` 按后缀/文件名白名单 rglob 全
   ``op_api/op_host/op_kernel/op_graph/config/docs``，不再限固定位置——后缀
   变体 ``_def``/``_tiling_arch35`` 自然覆盖。
2. stem + 后缀变体 ``_*`` × canndev 多层闭包：把 ``canndev/ops/built-in``
   下 ``aicpu/impl/kernels``/``op_tiling``/``op_proto``/``op_host``/
   ``fusion_pass``/``kernel/binary_config``/``op_api/inc/{level0,aclnn_kernels}``
   与 ``auto_schedule/python/tbe/dsl``（``--with-tbe-py``）的同 stem/后缀变体
   拉进来；并收各 ``ops-*/common/{inc,include}/external/aclnn_kernels/<stem>.h``
   声明头副本。
3. include 不动点闭包 + L0 反查：对工作集里所有源码头递归解析 ``#include "..."``
   （引号 include），在 include 搜索根集合里找命中文件，加入集合并继续递归，
   直到不动点。当命中的是 ``aclnn_kernels/<x>.h``（声明 ``namespace l0op``），
   用 ``<x>`` 的归一化 stem（去下划线）反查全树实现体，**只拉该 .cpp/.cc 本身 +
   其 include 闭包**，不整目录拉（避免误入同目录无关的别的 aclnn 接口层）。
   归一化是为桥接 CANN 同算子跨层 stem 不一致：L0 头/aicpu ``transdata``(无下划线)
   vs op_tiling/op_proto ``trans_data``(带下划线)，否则精确 stem 会漏 tiling 实现。
   这条把 ``npu_format_cast`` → ``transdata``(含 ``trans_data`` tiling)/``contiguous``
   /``reshape``/``transpose``/``view_copy`` 跨目录 L0 实现闭环。
4. 落盘：复制成快照目录（保相对路径），可直接喂
   ``extract_source_constraints.py --snapshot``；写 ``manifest.json``（每文件
   ``{rel_path, layer, strategy, included_by[]}``）+ ``closure_report.md``
   （每层命中数 + external/missing 清单）。

外部 SDK 头（``opdev/``/``graph/``/``securec.h`` 等）记 ``external``/``missing``，
不复制、不递归——诚实标注不假装拉到预编译部分。

只读 ``--src-tree``，只写 ``--out``。供 ``collect-operator-source`` skill 与
``source-analyst`` 衔接。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC_TREE = ROOT / "operators-src"
sys.path.insert(0, str(ROOT / "scripts"))
from locate_operator_source import locate  # noqa: E402

# --- 后缀 / 文件名白名单 ---------------------------------------------------
SOURCE_EXTS = (".cpp", ".cc", ".h", ".hpp", ".c")
EXTRA_EXTS = (".py", ".json", ".ini", ".cmake", ".cmake.in", ".tc")
KEEP_FILENAMES = {"CMakeLists.txt", "README.md"}
DOC_EXTS = (".md",)

# --- include 解析 ----------------------------------------------------------
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.MULTILINE)
# 命中 namespace l0op 的头视为 L0 声明头，触发实现反查。
L0_NAMESPACE_RE = re.compile(r"\bnamespace\s+l0op\b")


def _norm_stem(stem: str) -> str:
    """stem 归一化：去下划线 + 小写。

    CANN 同一算子跨层文件名 stem 不一致——L0 声明头/aicpu/ops-math 用
    ``transdata``(无下划线)，op_tiling/op_proto 实现用 ``trans_data``(带下划线)。
    L0 反查按精确 stem 匹配会漏掉 tiling 实现；归一化后两者同 key 闭环。
    """
    return stem.replace("_", "").lower()

# --- 外部 SDK 头判定（记清单，不复制不递归） --------------------------------
# include 路径前缀属外部框架/SDK。
EXTERNAL_PREFIXES = (
    "opdev/", "graph/", "metadef/", "aclnn/", "register/", "optime/",
    "op_log/", "exe_graph/", "gkernel/", "tiling/", "secable/",
    "op_meta/", "common_stash/", "tuning/",
)
# include 无前缀的裸外部头文件名。
EXTERNAL_FILES = {
    "securec.h", "aclnn_base.h", "aclnn_util.h", "aclnn_op.h",
    "aclnn_op_compiler.h", "aclnn_kernels.h",
}

# --- canndev 多层子树（阶段2 扫这些根下同 stem/后缀变体） --------------------
CANDEV_LAYER_ROOTS = (
    "canndev/ops/built-in/aicpu/impl/kernels",
    "canndev/ops/built-in/op_tiling",
    "canndev/ops/built-in/op_proto",
    "canndev/ops/built-in/op_host",
    "canndev/ops/built-in/fusion_pass",
    "canndev/ops/built-in/kernel/binary_config",
    "canndev/ops/built-in/op_api/inc",
)
TBE_DSL_ROOT = "canndev/auto_schedule/python/tbe/dsl"

# --- layer 判定（manifest 用） ---------------------------------------------
def classify_layer(rel: str, strategy: str) -> str:
    p = rel.replace("\\", "/")
    if "/aicpu/" in p:
        return "aicpu"
    if "fusion_pass" in p:
        return "fusion"
    if "/op_tiling/" in p or "/op_proto/" in p:
        return "tiling"
    if "binary_config" in p:
        return "binary_config"
    if "tbe/dsl" in p or "auto_schedule" in p:
        return "tbe_dsl"
    if "/op_kernel/" in p:
        return "op_kernel"
    if "/op_graph/" in p:
        return "op_graph"
    if "/op_host/" in p:
        return "op_host"
    if "/op_api/" in p:
        # 算子目录内 aclnn_*.cpp 才算 aclnn 接口层；transdata.cpp 这类裸名算 L0 实现
        base = Path(p).name
        if base.startswith("aclnn_"):
            return "aclnn_api"
        return "l0_impl"
    if "aclnn_kernels/" in p or "/level0/" in p:
        return "header_decl"
    return "other"


def _want_file(p: Path, with_tbe_py: bool, with_tests: bool) -> bool:
    """算子目录 SEED 白名单：源码 + 配置 + 文档 + CMake/README。"""
    name = p.name
    if name in KEEP_FILENAMES:
        return True
    ext = p.suffix.lower()
    if ext in SOURCE_EXTS or ext in EXTRA_EXTS or ext in DOC_EXTS:
        if ext == ".py" and not with_tbe_py and "tbe" not in p.as_posix():
            # 算子目录内 .py 默认不收（TBE 才收，由 --with-tbe-py 统一控制）
            return False
        return True
    return False


# --- 阶段 1：SEED 全收算子目录 ---------------------------------------------
def collect_seed(operator_dirs: list[Path], with_tbe_py: bool, with_tests: bool) -> set[Path]:
    out: set[Path] = set()
    for d in operator_dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            if not with_tests and ("tests" in p.parts or "examples" in p.parts or "ut" in p.parts):
                continue
            if _want_file(p, with_tbe_py, with_tests):
                out.add(p.resolve())
    return out


# --- 阶段 2：stem + 后缀变体 × canndev 多层闭包 -----------------------------
def collect_stem_closure(
    src_tree: Path, stems: list[str], with_tbe_py: bool
) -> tuple[set[Path], dict[str, list[Path]]]:
    """返回 (命中文件集, stem_index[stem]->[全树 .cpp/.cc/.h 路径])。

    stem_index 供阶段3 L0 反查 O(1) 查 <x>.cpp 实现体。
    """
    out: set[Path] = set()
    # 后缀变体 patterns：{stem} 和 {stem}_*
    patterns: list[str] = []
    for s in stems:
        if not s:
            continue
        for ext in SOURCE_EXTS:
            patterns.append(f"{s}{ext}")
            patterns.append(f"{s}_*{ext}")
    # canndev 多层 + 各 ops-* 声明头副本
    roots: list[Path] = []
    for rel in CANDEV_LAYER_ROOTS:
        roots.append(src_tree / rel)
    if with_tbe_py:
        roots.append(src_tree / TBE_DSL_ROOT)
    # 不兜底全树：ops-* 算子目录实现由 SEED(阶段1)+L0反查+extra_stem 覆盖，
    # 声明头 aclnn_kernels/*.h 由 include 闭包(阶段3)覆盖。canndev 各层 rglob
    # 递归扫子树，能拉 aicpu/tiling/fusion 深层 .cc（非递归 glob 只命中根层）。

    # 命中收集
    for r in roots:
        if not r.is_dir():
            continue
        for pat in patterns:
            for hit in r.rglob(pat):
                if hit.is_file():
                    out.add(hit.resolve())

    # 预建 stem_index：全树 .cpp/.cc/.h 按归一化 stem(去下划线)分组，供 L0 反查
    # 与 --extra-stem 查询。归一化桥接 CANN 同算子跨层 stem 不一致(transdata↔trans_data)，
    # 否则精确 stem 会让 L0 头 transdata 漏掉 op_tiling/op_proto 的 trans_data 实现。
    stem_index: dict[str, list[Path]] = defaultdict(list)
    for ext in SOURCE_EXTS:
        for p in src_tree.rglob(f"*{ext}"):
            stem_index[_norm_stem(p.stem)].append(p.resolve())
    return out, stem_index


# --- 阶段 3：include 不动点闭包 + L0 反查 -----------------------------------
def _build_include_roots(src_tree: Path, operator_dirs: list[Path]) -> list[Path]:
    """构造 include 搜索根：各 ops-*/common/{inc,include,stub} +
    ops-* 包根（解析 "conversion/xxx/op_api/xxx.h" 这类相对包根的 include）+
    canndev/ops/built-in/op_api/inc + 算子目录及其 op_api/op_host/op_kernel。"""
    roots: set[Path] = set()
    for pat in ("*/common/inc", "*/common/include", "*/common/stub"):
        for r in src_tree.glob(pat):
            if r.is_dir():
                roots.add(r.resolve())
    # ops-* 包根：解析 "conversion/view_copy/op_api/view_copy.h" 这类相对于
    # ops-* 根而非 include 标准根的 include 路径。
    for r in src_tree.glob("ops-*"):
        if r.is_dir():
            roots.add(r.resolve())
    candev_inc = src_tree / "canndev/ops/built-in/op_api/inc"
    if candev_inc.is_dir():
        roots.add(candev_inc.resolve())
    for d in operator_dirs:
        if not d.is_dir():
            continue
        roots.add(d.resolve())
        for sub in ("op_api", "op_host", "op_kernel", "op_graph"):
            s = d / sub
            if s.is_dir():
                roots.add(s.resolve())
    return sorted(roots)


def _is_external_include(inc: str) -> bool:
    if inc in EXTERNAL_FILES:
        return True
    return inc.startswith(EXTERNAL_PREFIXES)


def _find_include(inc: str, roots: list[Path]) -> Path | None:
    """在搜索根集合里找 include 路径命中的文件（先精确拼接，再 case-insensitive 兜底）。"""
    inc_posix = inc.replace("\\", "/")
    for r in roots:
        cand = (r / inc_posix)
        if cand.is_file():
            return cand.resolve()
    # 兜底：文件名直接在根下找（如 "aclnn_npu_format_cast.h" 无前缀）
    name = Path(inc_posix).name
    for r in roots:
        cand = r / name
        if cand.is_file():
            return cand.resolve()
    return None


def include_closure(
    workset: set[Path],
    src_tree: Path,
    roots: list[Path],
    stem_index: dict[str, list[Path]],
) -> tuple[set[Path], set[str], set[str], dict[Path, set[Path]]]:
    """不动点闭包。返回 (collected, external_inc_strings, missing_inc_strings,
    included_by, l0_backref_files)。

    collected 含 workset + 闭包新增的项目内头/实现。external/missing 记 include
    字符串清单（不复制）。l0_backref_files = 经 L0 反查加入的实现 .cpp/.cc
    （供 strategy 标注，避免依赖尚未构建的 rel_map）。
    """
    collected: set[Path] = set(workset)
    l0_backref_files: set[Path] = set()
    external: set[str] = set()
    missing: set[str] = set()
    included_by: dict[Path, set[Path]] = defaultdict(set)
    resolve_cache: dict[str, str] = {}  # inc -> "external"|"missing"|<resolved path str>

    queue: list[Path] = [p for p in workset if p.suffix.lower() in SOURCE_EXTS]
    queued: set[Path] = set(queue)

    while queue:
        f = queue.pop()
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        is_l0_namespace = bool(L0_NAMESPACE_RE.search(text))
        for inc in INCLUDE_RE.findall(text):
            if inc in resolve_cache:
                key = resolve_cache[inc]
            elif _is_external_include(inc):
                resolve_cache[inc] = "external"
                key = "external"
                external.add(inc)
                continue
            else:
                found = _find_include(inc, roots)
                if found is None:
                    resolve_cache[inc] = "missing"
                    missing.add(inc)
                    continue
                resolve_cache[inc] = str(found)
                key = str(found)
            if key in ("external", "missing"):
                continue
            found = Path(key)
            included_by[found].add(f)
            if found not in collected:
                collected.add(found)
                if found.suffix.lower() in SOURCE_EXTS and found not in queued:
                    queued.add(found)
                    queue.append(found)
                # L0 反查：命中 aclnn_kernels/<x>.h（namespace l0op）-> 反查 <x> 实现体。
                # stem 归一化(去下划线)以拉到与声明头 stem 不一致的同算子实现：
                # transdata.h -> op_tiling/op_proto 的 trans_data.cc（精确 stem 会漏）。
                if is_l0_namespace or "aclnn_kernels" in inc.replace("\\", "/"):
                    impl_stem = found.stem
                    for impl in stem_index.get(_norm_stem(impl_stem), []):
                        # 只拉实现体本身（.cpp/.cc，非 .h；非 stub/ut），不整目录拉
                        if impl.suffix.lower() not in (".cpp", ".cc"):
                            continue
                        pposix = impl.as_posix()
                        if "/stub/" in pposix or "/tests/" in pposix or "/ut/" in pposix:
                            continue
                        # .cpp 只收算子仓 op_api 层（排除 xla-npu/sip/ge 同名 .cpp）；
                        # .cc 收 canndev 各层（aicpu/tiling/fusion，排除 ge 的 .cc）。
                        if impl.suffix.lower() == ".cpp" and "/op_api/" not in pposix:
                            continue
                        if impl.suffix.lower() == ".cc" and "/canndev/" not in pposix and "/op_api/" not in pposix:
                            continue
                        if impl not in collected:
                            collected.add(impl)
                            l0_backref_files.add(impl)
                            included_by[impl].add(found)
                            if impl not in queued:
                                queued.add(impl)
                                queue.append(impl)
    return collected, external, missing, included_by, l0_backref_files


# --- 阶段 4：落盘 ----------------------------------------------------------
def copy_snapshot(files: set[Path], src_tree: Path, out: Path) -> dict[Path, str]:
    """复制文件到 out 保持相对路径；返回 {abs_path: rel_posix}。"""
    rel_map: dict[Path, str] = {}
    for f in files:
        try:
            rel = f.relative_to(src_tree)
        except ValueError:
            # 算子目录可能在 src_tree 之外（locate 给的是 src_tree/<ops-*>/<...>，
            # 一般都在树内；兜底用文件名）
            rel = Path(f.name)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(f, dest)
        except OSError:
            pass
        rel_map[f] = rel.as_posix()
    return rel_map


def write_manifest(
    out: Path,
    aclnn: str,
    src_tree: Path,
    operator_dirs: list[Path],
    collected: set[Path],
    rel_map: dict[Path, str],
    strategy_of: dict[Path, str],
    included_by: dict[Path, set[Path]],
    external: set[str],
    missing: set[str],
    stats: dict,
) -> None:
    files = []
    for f in sorted(collected, key=lambda p: rel_map.get(p, p.name)):
        rel = rel_map.get(f, f.name)
        strat = strategy_of.get(f, "include_closure")
        files.append({
            "rel_path": rel,
            "layer": classify_layer(rel, strat),
            "strategy": strat,
            "included_by": sorted(rel_map.get(inc, str(inc)) for inc in included_by.get(f, set())),
        })
    manifest = {
        "aclnn": aclnn,
        "src_tree": str(src_tree),
        "operator_dirs": [str(d) for d in operator_dirs],
        "stats": stats,
        "files": files,
        "external": sorted(external),
        "missing": sorted(missing),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_report(out: Path, stats: dict, layer_counts: dict, external: set[str], missing: set[str]) -> None:
    lines = ["# 算子源码收集报告", ""]
    lines.append("## 统计")
    lines.append("")
    lines.append("| 阶段/类别 | 数量 |")
    lines.append("|---|---|")
    for k, v in stats.items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## 分层命中")
    lines.append("")
    lines.append("| layer | 文件数 |")
    lines.append("|---|---|")
    for layer, n in sorted(layer_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {layer} | {n} |")
    lines.append("")
    if external:
        lines.append("## external（外部 SDK 头，未复制实现）")
        lines.append("")
        for e in sorted(external):
            lines.append(f"- `{e}`")
        lines.append("")
    if missing:
        lines.append("## missing（include 在项目内未找到）")
        lines.append("")
        for m in sorted(missing):
            lines.append(f"- `{m}`")
        lines.append("")
    (out / "closure_report.md").write_text("\n".join(lines), encoding="utf-8")


# --- 主流程 ----------------------------------------------------------------
def collect(
    aclnn: str, src_tree: Path, out: Path,
    with_tbe_py: bool, with_tests: bool, with_external_stub: bool,
    extra_stems: list[str] | None = None,
) -> dict:
    if not aclnn.startswith("aclnn"):
        aclnn = "aclnn" + aclnn

    # 阶段 0：定位算子目录
    loc = locate(aclnn, src_tree)
    operator_dirs = [Path(d) for d in loc.get("operator_dirs", [])]
    if not operator_dirs:
        return {"ok": False, "requires_user_action": True, "code": "OP_NOT_FOUND",
                "message": f"未在 {src_tree} 定位到 {aclnn} 的算子目录。", "aclnn": aclnn}

    stems = sorted({d.name for d in operator_dirs} | {aclnn[5:].lower()} | {aclnn[5:]})
    # aclnn 短名转 snake（如 NpuFormatCast -> npu_format_cast）作候选 stem
    short = aclnn[5:]
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", short).lower()
    stems = sorted(set(stems) | {snake, short.lower()} | set(extra_stems or []))

    # 阶段 1：SEED 全收算子目录
    seed = collect_seed(operator_dirs, with_tbe_py, with_tests)
    # 阶段 2：stem + canndev 多层闭包
    stem_hits, stem_index = collect_stem_closure(src_tree, stems, with_tbe_py)

    # 工作集 = SEED ∪ stem 闭包（去重）
    workset: set[Path] = set(seed) | set(stem_hits)
    strategy_of: dict[Path, str] = {}
    for f in seed:
        strategy_of[f] = "seed"
    for f in stem_hits:
        strategy_of.setdefault(f, "stem_closure")

    # --extra-stem: 共声明实现（如 ViewCopy 在 view_copy.cpp）绕过 include 触发，
    # 直接用 stem_index 拉同名 .cpp/.cc（op_api 层、非 stub）。
    for es in (extra_stems or []):
        for impl in stem_index.get(_norm_stem(es), []):
            pposix = impl.as_posix()
            if impl.suffix.lower() not in (".cpp", ".cc"):
                continue
            if "/stub/" in pposix or "/tests/" in pposix or "/ut/" in pposix:
                continue
            if impl.suffix.lower() == ".cpp" and "/op_api/" not in pposix:
                continue
            if impl.suffix.lower() == ".cc" and "/canndev/" not in pposix and "/op_api/" not in pposix:
                continue
            if impl not in workset:
                workset.add(impl)
                strategy_of[impl] = "l0_backref"

    # 阶段 3：include 不动点闭包 + L0 反查
    roots = _build_include_roots(src_tree, operator_dirs)
    collected, external, missing, included_by, l0_backref_files = include_closure(
        workset, src_tree, roots, stem_index
    )
    # 闭包新增的标 strategy：L0 反查命中的标 l0_backref，其余 include_closure
    for f in (collected - workset):
        strategy_of[f] = "l0_backref" if f in l0_backref_files else "include_closure"

    # external stub 副本：默认不复制 external 头；--with-external-stub 时把项目内
    # 找到的 external 副本头也加入 collected（供查看声明）。
    if with_external_stub:
        for inc in external:
            found = _find_include(inc, roots)
            if found and found not in collected:
                collected.add(found)
                strategy_of[found] = "external_stub"

    out.mkdir(parents=True, exist_ok=True)
    rel_map = copy_snapshot(collected, src_tree, out)

    # 分层计数
    layer_counts: dict[str, int] = defaultdict(int)
    for f in collected:
        rel = rel_map.get(f, f.name)
        layer_counts[classify_layer(rel, strategy_of.get(f, "include_closure"))] += 1

    stats = {
        "seed": len(seed),
        "stem_closure": len(stem_hits - seed),
        "include_closure": sum(1 for f in (collected - workset) if strategy_of.get(f) == "include_closure"),
        "l0_backref": sum(1 for f in (collected - workset) if strategy_of.get(f) == "l0_backref"),
        "external_stub": sum(1 for f in collected if strategy_of.get(f) == "external_stub"),
        "external": len(external),
        "missing": len(missing),
        "total_copied": len(collected),
    }
    write_manifest(out, aclnn, src_tree, operator_dirs, collected, rel_map,
                   strategy_of, included_by, external, missing, stats)
    write_report(out, stats, layer_counts, external, missing)
    return {"ok": True, "aclnn": aclnn, "operator_dirs": [str(d) for d in operator_dirs],
            "stems": stems, "out": str(out), "stats": stats, "layer_counts": dict(layer_counts)}


def main() -> int:
    ap = argparse.ArgumentParser(description="收集某算子在 operators-src 树内的全量源码。")
    ap.add_argument("--aclnn-name", required=True, help="aclnn 接口名，如 aclnnNpuFormatCast。")
    ap.add_argument("--src-tree", default=str(DEFAULT_SRC_TREE),
                    help=f"源码树根目录（默认 {DEFAULT_SRC_TREE}）。")
    ap.add_argument("--out", required=True, help="输出目录（快照+manifest+report）。")
    ap.add_argument("--with-tests", action="store_true", help="收 tests/examples/ut。")
    ap.add_argument("--with-tbe-py", action="store_true", help="收 canndev TBE DSL Python。")
    ap.add_argument("--with-external-stub", action="store_true",
                    help="把项目内 external/stub 副本头也复制（默认只记清单不复制）。")
    ap.add_argument("--extra-stem", nargs="*", default=[],
                    help="额外 stem（如 view_copy），补 L0 反查漏的共声明实现。"
                        "当某 l0op 函数与声明头不同 stem（如 ViewCopy 声明在 "
                        "aclnn_kernels/contiguous.h、实现在 view_copy.cpp）时用。")
    args = ap.parse_args()
    src_tree = Path(args.src_tree).resolve()
    if not src_tree.is_dir():
        print(json.dumps({"ok": False, "code": "SRC_TREE_NOT_FOUND",
                          "message": f"源码树不存在: {src_tree}", "src_tree": str(src_tree)},
                         ensure_ascii=False, indent=2))
        return 2
    result = collect(args.aclnn_name, src_tree, Path(args.out),
                     args.with_tbe_py, args.with_tests, args.with_external_stub,
                     args.extra_stem)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
