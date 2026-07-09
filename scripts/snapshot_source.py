#!/usr/bin/env python3
"""只读复制算子源码到快照目录, 含 #include 闭包拉取调用链共享头。

种子: op_host/ + op_api/ 的 .cpp/.cc/.h/.hpp/.json + docs/aclnn*.md(含 config)。
闭包: 从种子 .cpp/.cc/.h/.hpp 抓 #include "..."(跳 <...> 系统头), 按多根顺序解析,
把算子目录外的共享头(如 common/inc/error_util.h、common/inc/op_host/tiling_base.h、
norm/norm_common/.../norm_tiling_check_common.h)复制到 dest/_closure/<相对 tree_root 路径>/。

tree_root 为 ops-* 子树根; 传 None(--source-root 单目录模式)时仅"文件目录/算子根"
两步可达, 算子目录外共享头不可达 → 记入 unresolved_includes(source-analyst 标
missing_evidence)。需完整调用链源码(尤其 norm/conv/mc2 族共享 tiling helper)须用
--src-tree, 由 init_run 把 tree_root 收紧到算子所属 ops-* 子树。

解析顺序: 1.含文件目录 2.算子根(src_root) 3.tree_root+include_str 4.tree_root 下
共享 inc 目录族(common/inc,common/include,inc)+include_str 5.路径后缀兜底(endswith
over 一次性建好的子树文件列表; 多命中取最浅且路径含 common/inc/include, 记歧义)。
visited-set 防环, ~300 文件上限兜底。

只读外部源码树, 只写项目内快照目录, 与"项目内快照为唯一真相源"一致。
产 _closure/MANIFEST.json(copied_count/unresolved_includes/ambiguous_resolutions)。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from _cpp_parse import (
    _CONTROL_FLOW,
    _FUNC_DECL_RE,
    _func_starts_loose,
    _is_definition,
    _matching_brace_end,
)

INCLUDE_RE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.MULTILINE)

# 种子 glob: 算子自身目录文件(op_host 本地 helper 体在此, op_api aclnn 包装, docs, config)。
SEED_PATTERNS = (
    "op_host/**/*.cpp",
    "op_host/**/*.cc",
    "op_host/**/*.h",
    "op_host/**/*.hpp",
    "op_host/**/*.json",
    # op_api 平级形态: ops-math 的 op_api 与 op_host 同级; ops-transformer 嵌套在 op_host/op_api/。
    "op_api/**/*.cpp",
    "op_api/**/*.cc",
    "op_api/**/*.h",
    "op_api/**/*.hpp",
    "docs/aclnn*.md",
)

_SOURCE_SUFFIXES = {".cpp", ".cc", ".h", ".hpp"}
_MAX_CLOSURE_FILES = 300
_MAX_ROUNDS = 60

# 闭包跳过的测试/桩目录段: 这些是 UT 桩/测试双打, 不携带真实算子约束,
# 扫描会注入伪 OP_CHECK 噪音。种子(算子自身 op_host/op_api)不受此过滤。
_TEST_SEGMENTS = {"tests", "test", "stubs", "stub", "ut", "unittest",
                   "unit_test", "unittests", "fuzz"}


def _is_test_stub_path(p: Path) -> bool:
    """路径任一段为 tests/stub/ut 等 → 视为测试/桩代码, 闭包跳过。"""
    return any(seg in _TEST_SEGMENTS for seg in p.parts)


def _ops_subtree_root(operator_dir: Path, tree_root: Path) -> Path:
    """从算子目录向上找最近的 ops-* 子树根; 找不到回退 tree_root。

    --src-tree 指向含多 ops-* 的大根(如 operators-src)时, 把搜索范围从全树收紧到
    算子所属子树(如 ops-nn), 避免跨子树 basename 歧义与全树 rglob 开销。
    """
    p = operator_dir
    try:
        p = p.resolve()
    except Exception:
        pass
    root = tree_root
    while p is not None and p != p.parent:
        if p == root:
            break
        if p.name.startswith("ops-"):
            return p
        p = p.parent
    return root


def _resolve_include(
    inc: str,
    including_file: Path,
    src_root: Path,
    tree_root: Path | None,
    subtree_files: list[Path],
    ambiguous: list[dict],
) -> Path | None:
    """按多根顺序解析 #include "..."。命中返回绝对路径, 未命中返回 None。

    步骤 1/2 总尝试; 3/4/5 仅 tree_root 非 None 时。路径后缀兜底多命中时取最浅+
    路径含 common/inc/include 者, 并记歧义到 ambiguous。
    """
    candidates: list[Path] = []

    def try_path(base: Path) -> None:
        c = (base / inc).resolve()
        if c.is_file() and c not in candidates:
            candidates.append(c)

    # 1. 含文件自身目录(本地 include, 多已在种子内)
    try_path(including_file.parent)
    # 2. 算子根
    try_path(src_root)

    if tree_root is not None:
        # 3. tree_root + include_str(catch "norm/norm_common/..." 这类子树相对路径)
        try_path(tree_root)
        # 4. 共享 inc 目录族 + include_str(catch "op_host/tiling_base.h"->common/inc/op_host/...)
        for d in ("common/inc", "common/include", "inc"):
            try_path(tree_root / d)
        # 5. 路径后缀兜底(裸 basename 如 "error_util.h" 跨子树多命中时歧义)
        if not candidates:
            inc_posix = inc.replace("\\", "/")
            matches = sorted({p for p in subtree_files if p.as_posix().endswith(inc_posix)})
            if len(matches) == 1:
                candidates.append(matches[0])
            elif len(matches) > 1:
                def score(p: Path) -> tuple:
                    ps = p.as_posix()
                    has_inc = 0 if ("common/" in ps or "/inc/" in ps or "/include/" in ps) else 1
                    return (ps.count("/"), has_inc, ps)
                matches.sort(key=score)
                ambiguous.append({
                    "include": inc,
                    "candidates": [m.as_posix() for m in matches],
                    "chosen": matches[0].as_posix(),
                })
                candidates.append(matches[0])

    return candidates[0] if candidates else None


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


_NAMESPACE_L0OP_RE = re.compile(r'\bnamespace\s+l0op\b')


def _collect_l0op_symbols(header_text: str) -> set[str]:
    """从头文本收集 `namespace l0op { ... }` 体内函数声明 symbol 名(后跟 ';')。

    排除 inline 定义(后跟 '{', 已在快照内)。不按返回类型过滤:CanOptimizeContiguous 等
    bool 内部接口也收集——与算子委派内核同文件,零额外文件开销,且 source-analyst 跟
    callee 时同文件可得反而有益。typedef struct 不被 FUNC_RE 命中,无需特判。
    """
    symbols: set[str] = set()
    for ns in _NAMESPACE_L0OP_RE.finditer(header_text):
        brace = header_text.find("{", ns.end())
        if brace < 0:
            continue
        body_end = _matching_brace_end(header_text, brace)
        if body_end is None:
            continue
        body = header_text[brace:body_end]
        for m in _FUNC_DECL_RE.finditer(body):
            name = m.group(1)
            if name in _CONTROL_FLOW:
                continue
            # 声明(后跟 ';')收集; inline 定义(后跟 '{')跳过
            if not _is_definition(body, m.end() - 1):
                symbols.add(name)
    return symbols


def _build_impl_index(subtree_files: list[Path]) -> dict[str, list[Path]]:
    """建 tree_root 内 .cpp/.cc 的函数定义索引: name(last_seg) -> [定义所在文件]。

    跳 test/stub 路径。供声明头驱动按 symbol O(1) 查 impl 文件,避免每个 symbol 全树扫。
    仅扫 .cpp/.cc(l0op 内核 impl 不在 .h);用 _func_starts 只取定义(后跟 '{')。
    """
    index: dict[str, list[Path]] = {}
    for f in subtree_files:
        if f.suffix not in (".cpp", ".cc") or _is_test_stub_path(f):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for _start, name in _func_starts_loose(text):
            last_seg = name.rsplit("::", 1)[-1]
            bucket = index.setdefault(last_seg, [])
            if f not in bucket:
                bucket.append(f)
    return index


def _pull_impls_for_header(
    header_text: str,
    tree_root: Path,
    impl_index: dict[str, list[Path]],
    dest: Path,
    copied: dict[Path, str],
    visited: set[Path],
    worklist: list[Path],
    resolved_symbols: dict[str, list[str]],
    unresolved_impls: list[str],
    ambiguous: list[dict],
) -> None:
    """声明头驱动: 收集 header 内 l0op 函数声明 symbol, 按 impl_index 定位 impl .cpp
    整文件复制到 _closure/delegated/<相对 tree_root 路径>, 并 append worklist 让主闭包
    追逐其 #include(信任 visited/_MAX_CLOSURE_FILES 上限)。

    多定义(重载)全复制, 记歧义到 ambiguous(kind:"impl")。就地更新 copied/visited/worklist/
    resolved_symbols/unresolved_impls/ambiguous。basename 匹配(name.rsplit('::',1)[-1])
    兼容裸名 ViewCopy 与限定 l0op::ViewCopy 两种定义形态。
    """
    for sym in sorted(_collect_l0op_symbols(header_text)):
        defs = impl_index.get(sym, [])
        if not defs:
            if sym not in unresolved_impls:
                unresolved_impls.append(sym)
            continue
        if len(defs) > 1:
            ambiguous.append({
                "kind": "impl",
                "symbol": sym,
                "files": [d.relative_to(tree_root).as_posix() for d in defs],
                "note": "多定义(多为重载),全复制",
            })
        for d in defs:
            canon = d.resolve()
            if canon in copied or canon in visited:
                existing_rel = copied.get(canon)
                if existing_rel is not None:
                    lst = resolved_symbols.setdefault(sym, [])
                    if existing_rel not in lst:
                        lst.append(existing_rel)
                continue
            visited.add(canon)
            try:
                drel = "_closure/delegated/" + d.relative_to(tree_root).as_posix()
            except ValueError:
                drel = "_closure/delegated/" + d.name
            _copy_file(d, dest / drel)
            copied[canon] = drel
            # impl .cpp 不 append worklist: 切断 "impl #include 别的 aclnn_kernels 头 →
            # 触发更多 impl 拉取" 的连锁雪崩(如 contiguous.cpp include cast.h → 拉 Cast 的 18
            # 个定义文件)。impl 的 OP_CHECK 在 .cpp 本身被 extract 扫到;impl 调的别的 l0op
            # 内核(如 l0op::Cast)经 extract.delegated_kernels 标 missing_evidence(其 impl 未拉)。
            lst = resolved_symbols.setdefault(sym, [])
            if drel not in lst:
                lst.append(drel)


# --- R1: op-type 名注册驱动拉取(canndev/opbase 等非 ops-* 包的 legacy tiling) ---------
#
# 动机: 算子 L0 impl(如 transdata.cpp) 经 OP_TYPE_REGISTER(<Op>)/INFER_SHAPE(<Op>,..)/
# ADD_TO_LAUNCHER_LIST_AICORE(<Op>,..)/l0op::<Symbol>( 发射 op-type 名, 对端在 canndev 的
# op_tiling/ 用 IMPL_OP_OPTILING_LEGACY(<Op>,..)/IMPL_OP(<Op>,..) 登记 legacy 图模式 tiling
# (trans_data.cc 的 17× OP_TILING_CHECK 即在此)。这条链接既非 #include 也非 l0op 声明头
# symbol, 现有 seed/闭包/l0op-pull 三机制都到不了(见记忆 source-extraction-canndev-gap)。
# R1 按 op-type 名在 backend_trees 建注册点索引, 把命中 .cc 整文件拉到
# _closure/legacy_optiling/<相对 backend_tree 路径>/, 并以 backend_tree 为根追其 #include
# (让 sibling 头如 trans_data_fz2fzg.h 进快照, 供 S1 全局找其 impl .cc)。一跳: 拉入文件
# 不再触发 R1(其内部 IMPL_OP 不递归拉)。相关性判读交 source-analyst(图模式, 可能非 aclnn 路径)。

_OPNAME_EMIT_RE = re.compile(
    r'\b(?:OP_TYPE_REGISTER|INFER_SHAPE|ADD_TO_LAUNCHER_LIST_AICORE)\s*\(\s*(\w+)'
)
_L0OP_CALL_RE = re.compile(r'\bl0op::([A-Za-z_]\w*)\s*\(')
# IMPL_OP / IMPL_OP_OPTILING / IMPL_OP_OPTILING_LEGACY / IMPL_OP_COMPUTE / IMPL_OP_INFER_SHAPE ...
# 首参恒为 op-type 名。行首锚定 + MULTILINE 兼容注册宏独占行; 跳 .h(注册点在 .cc)。
_OPNAME_REG_RE = re.compile(r'^\s*IMPL_OP[A-Z_]*\s*\(\s*(\w+)', re.MULTILINE)

_MAX_LEGACY_OPTILING_FILES = 60


def _rel_to_any_bt(p: Path, backend_trees: list[Path]) -> str:
    """p 相对任一 backend_tree 的 posix 相对路径; 都不在则回退 basename。"""
    pr = p.resolve() if p.is_absolute() else p
    for bt in backend_trees:
        try:
            return pr.resolve().relative_to(bt.resolve()).as_posix()
        except (ValueError, OSError):
            continue
    return p.name


def _norm_stem(filename: str) -> str:
    """文件名(去扩展名)归一化: 小写 + 去 '_'。trans_data_fz2fzg.cc -> transdatafz2fzg。"""
    return filename.rsplit('.', 1)[0].replace('_', '').lower()


def _op_stem(header_name: str) -> str:
    """从头文件名取 op stem(前两段下划线段, 归一), 供 S1 同目录索引前缀过滤。

    trans_data_fz2fzg.h -> transdata(前两段 trans_data); trans_data.h -> transdata;
    不足两段取全部。作用: S1 只把同目录 basename 归一后以此开头的 .cc 纳入索引,
    排除 op_tiling/ 扁平目录里 norm.cc/hash.cc 等无关 .cc(即便其定义了同名函数)。"""
    base = header_name.rsplit('.', 1)[0]
    segs = base.split('_')
    stem = '_'.join(segs[:2]) if len(segs) >= 2 else base
    return stem.replace('_', '').lower()


def _backend_subtree_files(backend_trees: list[Path]) -> list[Path]:
    """一次性建 backend_trees 下 .h/.hpp/.cpp/.cc 列表(legacy #include 闭包兜底用)。

    rglob 加 try/except OSError 兜底 Windows 长路径(记忆 cann-operators-src-layout)。
    """
    files: list[Path] = []
    for bt in backend_trees:
        for ext in ("*.h", "*.hpp", "*.cpp", "*.cc"):
            try:
                files.extend(bt.rglob(ext))
            except OSError:
                continue
    return sorted(set(files))


def _backend_resolve_include(
    inc: str, including_file: Path, backend_trees: list[Path],
    backend_files: list[Path], ambiguous: list[dict],
) -> Path | None:
    """以 backend_trees 依次为 tree_root 解析 #include(复用 _resolve_include 的 5 步)。

    step5 路径后缀兜底搜 backend_files 全集(跨 backend_tree), 命中 trans_data_fz2fzg.h 这类
    basename include。src_root 传含文件自身目录(step1/2 本地 include)。"""
    for bt in backend_trees:
        r = _resolve_include(
            inc, including_file, including_file.parent, bt, backend_files, ambiguous
        )
        if r is not None:
            return r
    return None


def _collect_emitted_opnames(snapshot_src_files: list[Path]) -> set[str]:
    """扫快照内所有源文件, 收算子发射的 op-type 名(OP_TYPE_REGISTER/INFER_SHAPE/
    ADD_TO_LAUNCHER_LIST_AICORE 首参 + l0op::<Symbol>)。供 R1 查 backend_trees 注册点。"""
    names: set[str] = set()
    for f in snapshot_src_files:
        if f.suffix not in _SOURCE_SUFFIXES:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _OPNAME_EMIT_RE.finditer(text):
            names.add(m.group(1))
        for m in _L0OP_CALL_RE.finditer(text):
            names.add(m.group(1))
    names.discard("")
    return names


def _build_reg_index(backend_trees: list[Path]) -> dict[str, list[Path]]:
    """建 backend_trees 内 .cc/.cpp 的 op-type 注册点索引: op_name -> [注册所在文件]。

    跳 test/stub。供 R1 按 emitted op_name O(1) 查 legacy tiling 注册 .cc(IMPL_OP* 首参)。"""
    index: dict[str, list[Path]] = {}
    for bt in backend_trees:
        for f in bt.rglob("*"):
            if not f.is_file() or f.suffix not in (".cc", ".cpp"):
                continue
            if _is_test_stub_path(f):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _OPNAME_REG_RE.finditer(text):
                op = m.group(1)
                bucket = index.setdefault(op, [])
                if f not in bucket:
                    bucket.append(f)
    return index


def _pull_legacy_optiling(
    op_names: set[str],
    backend_trees: list[Path],
    reg_index: dict[str, list[Path]],
    backend_files: list[Path],
    dest: Path,
    copied: dict[Path, str],
    visited: set[Path],
    ambiguous: list[dict],
    unresolved_opnames: list[str],
) -> tuple[list[str], bool]:
    """R1: 按 op_names 查 reg_index, 把命中的注册 .cc 整文件复制到
    _closure/legacy_optiling/<相对 backend_tree 路径>/, 并以 backend_tree 为根追其 #include
    (sibling 头进快照供 S1)。一跳: 拉入文件不再触发 R1。返回 (resolved_opnames, truncated)。

    多注册点(重载/多平台)全复制, 记歧义(kind:"legacy_optiling")。就地更新 copied/visited/
    ambiguous/unresolved_opnames。独立上限 _MAX_LEGACY_OPTILING_FILES, 超限标 truncated(不静默)。
    """
    resolved: list[str] = []
    truncated = False
    worklist: list[Path] = []
    # op-stem 集(TransData->transdata): R1 #include 闭包只追 op 簇头(trans_data*.h),
    # 跳 vector_tiling.h/auto_tiling.h 等深依赖(真实调用链但混其他算子约束 + S1 放大);
    # 深 callee 由 source-analyst 按 unresolved_calls 跟进。
    op_stems = {op.lower().replace('_', '') for op in op_names}
    for op in sorted(op_names):
        sites = reg_index.get(op, [])
        if not sites:
            if op not in unresolved_opnames:
                unresolved_opnames.append(op)
            continue
        resolved.append(op)
        if len(sites) > 1:
            ambiguous.append({
                "kind": "legacy_optiling",
                "symbol": op,
                "files": [_rel_to_any_bt(s, backend_trees) for s in sites],
                "note": "多注册点(重载/多平台),全复制",
            })
        for s in sites:
            canon = s.resolve()
            if canon in copied or canon in visited:
                continue
            visited.add(canon)
            drel = "_closure/legacy_optiling/" + _rel_to_any_bt(s, backend_trees)
            _copy_file(s, dest / drel)
            copied[canon] = drel
            if s.suffix in _SOURCE_SUFFIXES:
                worklist.append(s)
            if len(copied) >= _MAX_LEGACY_OPTILING_FILES:
                truncated = True
                break
        if truncated:
            break

    # backend #include 闭包: 让 trans_data_fz2fzg.h 等 sibling 头进快照(供 S1 全局找 impl)
    rounds = 0
    while worklist and rounds < _MAX_ROUNDS and len(copied) < _MAX_LEGACY_OPTILING_FILES:
        rounds += 1
        f = worklist.pop()
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for inc in INCLUDE_RE.findall(text):
            resolved_inc = _backend_resolve_include(
                inc, f, backend_trees, backend_files, ambiguous
            )
            if resolved_inc is None:
                continue
            # stem-gate: 只追 op 簇 #include(trans_data*.h), 跳深依赖(vector_tiling.h 等)
            inc_stem = _norm_stem(resolved_inc.name)
            if not any(inc_stem.startswith(s) for s in op_stems):
                continue
            canon = resolved_inc.resolve()
            if canon in copied or canon in visited:
                continue
            if _is_test_stub_path(canon):
                visited.add(canon)
                continue
            visited.add(canon)
            rel = "_closure/legacy_optiling/" + _rel_to_any_bt(resolved_inc, backend_trees)
            _copy_file(resolved_inc, dest / rel)
            copied[canon] = rel
            # 声明头驱动(S1)在主流程对 legacy 头另行触发; 这里只追 #include 不递归 R1
            if resolved_inc.suffix in _SOURCE_SUFFIXES:
                worklist.append(resolved_inc)
            if len(copied) >= _MAX_LEGACY_OPTILING_FILES:
                truncated = True
                break
    return resolved, truncated


# --- S1: 头声明函数全局找(用户建议①, 泛化 l0op-pull 到任意 legacy 头) ----------------
#
# R1 拉入的 legacy tiling .cc(如 trans_data.cc)经 #include 闭包带进 sibling 头
# (trans_data_fz2fzg.h 等), 这些头声明了 trans_data.cc 调用的 tiling helper
# (如 CreateFz2FzgTiling @ trans_data_fz2fzg.h:162), 其 impl 在同族 sibling .cc。
# S1 对 _closure/legacy_optiling/ 下已复制头, 收其声明的所有函数名, 在 backend_trees
# 建 impl 定义索引(_build_impl_index 复用, last_seg 名匹配)找 impl .cc 整文件拉入。
# 一跳: 仅对 R1 拉入的 legacy 头触发, S1 拉入的 impl 不再触发 S1(防雪崩);
# 不做 S1 impl 的 #include 闭包(其 OP_CHECK 已能扫; callee 未解析由 source-analyst 处理)。

_MAX_S1_FILES = 60
# 声明名在同目录命中 >N 个 impl 视为通用名(DoTiling/CalcTiling 等 op_tiling/ 每 .cc 都有)跳过,
# 避免拉整库。≤N 才拉(如 CreateFz2FzgTiling 唯一命中 trans_data_fz2fzg.cc)。N=3 兼容合法重载
# (ops-math MANIFEST 中 Contiguous=2/ViewCopy=3 均为同族 sibling)。
_S1_MAX_COLLISION = 3


def _collect_all_func_decls(header_text: str) -> set[str]:
    """泛化 _collect_l0op_symbols: 扫整头 _FUNC_DECL_RE, 收声明(后跟 ';' 非定义)的
    函数名(排除控制流)。供 S1 在 backend_trees 全局找 impl 定义。"""
    names: set[str] = set()
    for m in _FUNC_DECL_RE.finditer(header_text):
        name = m.group(1)
        if name in _CONTROL_FLOW:
            continue
        # 声明(后跟 ';')收集; inline 定义(后跟 '{')跳过(已在快照内)
        if not _is_definition(header_text, m.end() - 1):
            names.add(name)
    return names


def _pull_s1_for_legacy_headers(
    dest: Path,
    copied: dict[Path, str],
    visited: set[Path],
    ambiguous: list[dict],
    generic_skipped: list[str],
) -> tuple[list[str], bool]:
    """S1: 对 _closure/legacy_optiling/ 下已复制头, 在其**同目录**找声明函数的 impl .cc
    (co-located sibling impl, 高精度), 整文件拉到 _closure/legacy_optiling/<同名目录相对路径>/。

    不做全局搜: legacy 头声明 DoTiling/Compare/BroadcastTo 等通用名, 全局 last_seg 匹配会撞
    aicpu/test/fallback 等无关 impl 爆炸。同目录是 tiling helper impl 的真实分布
    (trans_data_fz2fzg.h → 同目录 trans_data_fz2fzg.cc)。**碰撞阈值**: 声明名在同目录命中
    >_S1_MAX_COLLISION 个 impl 视为通用名(DoTiling 在 op_tiling/ 每 .cc 都有)跳过入 generic_skipped,
    ≤N 才拉(CreateFz2FzgTiling 唯一命中)。一跳: S1 拉入的 .cc 不再触发 S1(.cc 非 .h)。
    返回 (s1_files, truncated), 超 _MAX_S1_FILES 标 truncated。
    """
    s1_files: list[str] = []
    truncated = False
    dir_index_cache: dict[tuple[Path, str], dict[str, list[Path]]] = {}
    legacy_headers = [
        p for p, rel in copied.items()
        if rel.startswith("_closure/legacy_optiling/") and p.suffix in (".h", ".hpp")
    ]
    for h in legacy_headers:
        try:
            htext = h.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        decls = _collect_all_func_decls(htext)
        if not decls:
            continue
        d = h.parent
        stem = _op_stem(h.name)
        cache_key = (d, stem)
        if cache_key not in dir_index_cache:
            # 同目录(非递归) .cc/.cpp, 跳 test/stub, 且 basename 归一后以 op-stem 开头
            # (排除 op_tiling/ 扁平目录里 norm.cc/hash.cc 等无关 .cc)
            try:
                siblings = [
                    f for f in d.iterdir()
                    if f.is_file() and f.suffix in (".cpp", ".cc")
                    and not _is_test_stub_path(f)
                    and _norm_stem(f.name).startswith(stem)
                ]
            except OSError:
                siblings = []
            dir_index_cache[cache_key] = _build_impl_index(siblings)
        idx = dir_index_cache[cache_key]
        for name in sorted(decls):
            defs = idx.get(name, [])
            if not defs:
                continue
            if len(defs) > _S1_MAX_COLLISION:
                # 通用名(DoTiling/CalcTiling 等在 op_tiling/ 每 .cc 都有)跳过, 避免拉整库
                if name not in generic_skipped:
                    generic_skipped.append(name)
                continue
            if len(defs) > 1:
                ambiguous.append({
                    "kind": "s1_impl",
                    "symbol": name,
                    "files": [df.name for df in defs],
                    "note": "同目录多定义(多为重载),全复制",
                })
            for df in defs:
                canon = df.resolve()
                if canon in copied or canon in visited:
                    continue
                visited.add(canon)
                # df 与 h 同目录, dest rel 由 h 的 rel 替换 basename 得到(无需 backend_trees)
                h_rel = copied[h]
                drel = h_rel.rsplit('/', 1)[0] + '/' + df.name
                _copy_file(df, dest / drel)
                copied[canon] = drel
                s1_files.append(drel)
                if len(s1_files) >= _MAX_S1_FILES:
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            break
    return s1_files, truncated


# --- S2: CMake 同前缀共属兜底(用户建议②) -----------------------------------------
#
# canndev op_tiling/CMakeLists.txt 用 file(GLOB_RECURSE *.cc) 把同族 .cc 编进 optiling 库
# (非显式源列表, trans_data.cc 经 GLOB 进库)。S1 按"头声明函数名"找 sibling .cc, 但
# transdata_dsl_general.cc 这类只含 static/匿名 helper(无头声明)的 sibling 会被 S1 漏。
# S2 按 op-stem 在 backend_trees 找 basename 归一后以 stem 开头的 .cc/.h(同族 co-member,
# 编进同一 CMake GLOB target), 复制到 _closure/comake/ 兜底。变量 GLOB 无法静态求值, 此处
# 用 stem 前缀近似 GLOB 的"同族"语义(高精度: transdata 前缀仅匹配 TransData 簇)。

_MAX_COMAKE_FILES = 30


def _pull_comake_siblings(
    op_names: set[str],
    backend_trees: list[Path],
    backend_files: list[Path],
    dest: Path,
    copied: dict[Path, str],
    visited: set[Path],
) -> tuple[list[str], bool]:
    """S2: 按 op-stem 在 backend_files 找同前缀 .cc/.h(同族 CMake co-member), 复制到
    _closure/comake/<相对 backend_tree 路径>/。补 S1 漏的 static/匿名 helper 所在 .cc。

    跳 test/stub, dedup via copied/visited。返回 (comake_files, truncated), 超 _MAX_COMAKE_FILES 标 truncated。
    """
    comake_files: list[str] = []
    truncated = False
    stems = {op.lower().replace('_', '') for op in op_names}
    for f in backend_files:
        if _is_test_stub_path(f):
            continue
        # 范围限定 op_tiling/ (= optiling CMake target 的 GLOB 范围, "共属"语义);
        # 排除 aicpu/fusion_pass/framework 下的同名 transdata.cc(不同 CMake target, 非本算子 legacy tiling)
        if "op_tiling" not in f.parts:
            continue
        if not f.is_file():
            continue
        fn = _norm_stem(f.name)
        if not any(fn.startswith(s) for s in stems):
            continue
        canon = f.resolve()
        if canon in copied or canon in visited:
            continue
        visited.add(canon)
        drel = "_closure/comake/" + _rel_to_any_bt(f, backend_trees)
        try:
            _copy_file(f, dest / drel)
        except OSError:
            # Windows 长路径/符号链接等拷贝失败 → 跳过(不阻断, 不计入 comake_files)
            visited.discard(canon)
            continue
        copied[canon] = drel
        comake_files.append(drel)
        if len(comake_files) >= _MAX_COMAKE_FILES:
            truncated = True
            break
    return comake_files, truncated


def snapshot_operator_source(
    src_root: Path, dest: Path, *, tree_root: Path | None = None,
    backend_trees: list[Path] | None = None,
) -> tuple[int, dict]:
    """种子复制 + #include 闭包, 返回 (copied_count, manifest)。

    src_root: 算子目录(种子来源); tree_root: ops-* 子树根(闭包搜索范围, None 则仅
    1/2 步可达); dest: 项目内快照目录。只读 src_root/tree_root/backend_trees, 只写 dest。
    backend_trees: 额外的非 ops-* 包根(如 canndev), 仅供 R1 op-type 名注册驱动拉取——
    按算子发射的 op-type 名(OP_TYPE_REGISTER/INFER_SHAPE/ADD_TO_LAUNCHER_LIST_AICORE/l0op::)
    在 backend_trees 的 .cc 里查 IMPL_OP* 注册点, 整文件拉到 _closure/legacy_optiling/ +
    追其 #include。不进 #include 闭包(闭包仍限 tree_root), 不拉 opbase(CommonOpExecutorRun)。
    """
    src_root = src_root.resolve()
    dest = Path(dest)
    if tree_root is not None:
        tree_root = tree_root.resolve()

    copied: dict[Path, str] = {}  # resolved abs path -> dest rel path
    unresolved_includes: list[str] = []
    ambiguous: list[dict] = []

    # 1. 种子复制(算子自身文件, 保持相对 src_root 路径)
    for pat in SEED_PATTERNS:
        for src in src_root.glob(pat):
            if not src.is_file():
                continue
            rel = src.relative_to(src_root).as_posix()
            _copy_file(src, dest / rel)
            copied[src.resolve()] = rel

    # 2. 一次性建子树文件列表(路径后缀兜底用)
    subtree_files: list[Path] = []
    if tree_root is not None:
        for ext in ("*.h", "*.hpp", "*.cpp", "*.cc"):
            subtree_files.extend(tree_root.rglob(ext))
        subtree_files = sorted(set(subtree_files))

    # 2.5 声明头驱动 impl 拉取的累积容器 + tree_root 内 .cpp/.cc 定义索引(仅 --src-tree 模式)
    resolved_symbols: dict[str, list[str]] = {}
    unresolved_impls: list[str] = []
    impl_index: dict[str, list[Path]] = _build_impl_index(subtree_files) if tree_root is not None else {}

    # 3. include 闭包: worklist 从种子源文件起, 逐文件抓 include 解析复制
    worklist: list[Path] = [p for p in copied.keys() if p.suffix in _SOURCE_SUFFIXES]
    visited: set[Path] = set()
    skipped_test_stub = 0
    rounds = 0
    while worklist and rounds < _MAX_ROUNDS and len(copied) < _MAX_CLOSURE_FILES:
        rounds += 1
        f = worklist.pop()
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for inc in INCLUDE_RE.findall(text):
            if inc in unresolved_includes:
                continue
            resolved = _resolve_include(inc, f, src_root, tree_root, subtree_files, ambiguous)
            if resolved is None:
                unresolved_includes.append(inc)
                continue
            canon = resolved.resolve()
            if canon in copied or canon in visited:
                continue
            if _is_test_stub_path(canon):
                # 测试/桩代码不携带真实约束, 跳过复制与继续追逐
                visited.add(canon)
                skipped_test_stub += 1
                continue
            visited.add(canon)
            base = tree_root if tree_root is not None else src_root
            try:
                rel = "_closure/" + resolved.relative_to(base).as_posix()
            except ValueError:
                rel = "_closure/" + resolved.name
            _copy_file(resolved, dest / rel)
            copied[canon] = rel
            # 声明头驱动: 复制的头若含 namespace l0op, 定位其 l0op 内核 impl .cpp
            # 整文件拉到 _closure/delegated/ 并 append worklist 追逐其 #include。
            if tree_root is not None and resolved.suffix in (".h", ".hpp"):
                try:
                    htext = resolved.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    htext = ""
                if "aclnn_kernels/" in resolved.as_posix() or _NAMESPACE_L0OP_RE.search(htext):
                    _pull_impls_for_header(
                        htext, tree_root, impl_index, dest,
                        copied, visited, worklist,
                        resolved_symbols, unresolved_impls, ambiguous,
                    )
            if resolved.suffix in _SOURCE_SUFFIXES:
                worklist.append(resolved)
            if len(copied) >= _MAX_CLOSURE_FILES:
                break

    # 4. R1+S1: op-type 名注册驱动拉取 + 头声明函数全局找(backend_trees 非空时)。
    #    在 seed+闭包+l0op-pull 之后: R1 扫已收录文件收 emitted op-type 名, 在 backend_trees
    #    建 IMPL_OP* 注册点索引, 把命中 .cc(如 canndev/.../op_tiling/runtime/trans_data.cc) 拉到
    #    _closure/legacy_optiling/ + 追其 #include(sibling 头进快照); S1 对拉入的 legacy 头收其
    #    声明函数名, 在 backend_trees 全局找 impl sibling .cc(如 trans_data_fz2fzg.cc) 拉入。
    #    均一跳, 相关性交 source-analyst 判(图模式 legacy, 可能非 aclnn 路径)。
    legacy_resolved: list[str] = []
    legacy_unresolved: list[str] = []
    legacy_truncated = False
    s1_files: list[str] = []
    s1_truncated = False
    s1_generic_skipped: list[str] = []
    comake_files: list[str] = []
    comake_truncated = False
    if backend_trees:
        backend_trees = [bt.resolve() for bt in backend_trees]
        backend_files = _backend_subtree_files(backend_trees)
        snap_src_files = [p for p in copied.keys() if p.suffix in _SOURCE_SUFFIXES]
        op_names = _collect_emitted_opnames(snap_src_files)
        if op_names:
            reg_index = _build_reg_index(backend_trees)
            legacy_resolved, legacy_truncated = _pull_legacy_optiling(
                op_names, backend_trees, reg_index, backend_files,
                dest, copied, visited, ambiguous, legacy_unresolved,
            )
        # S1: legacy 头声明的 tiling helper impl 在同目录 sibling .cc(co-located, 高精度)
        if legacy_resolved:
            s1_files, s1_truncated = _pull_s1_for_legacy_headers(
                dest, copied, visited, ambiguous, s1_generic_skipped
            )
        # S2: CMake 同前缀共属兜底(补 S1 漏的 static/匿名 helper sibling)。只用 R1 实际
        # 解析的 legacy op_names(TransData 等), 不用全部 emitted(L0 内核名 Cast/Reshape/
        # ViewCopy 等无 canndev legacy IMPL_OP 注册, stem-match 会拉其 aicpu/fusion_pass 簇)
        if legacy_resolved:
            comake_files, comake_truncated = _pull_comake_siblings(
                set(legacy_resolved), backend_trees, backend_files, dest, copied, visited
            )

    manifest = {
        "copied_count": len(copied),
        "unresolved_includes": sorted(unresolved_includes),
        "ambiguous_resolutions": ambiguous,
        "skipped_test_stub_count": skipped_test_stub,
        "delegated_impl_count": len({p for v in resolved_symbols.values() for p in v}),
        "delegated_symbols_resolved": {k: sorted(set(v)) for k, v in resolved_symbols.items()},
        "delegated_symbols_unresolved": sorted(set(unresolved_impls)),
        "legacy_optiling_opnames": legacy_resolved,
        "legacy_optiling_unresolved": sorted(set(legacy_unresolved)),
        "legacy_optiling_truncated": legacy_truncated,
        "s1_files": s1_files,
        "s1_truncated": s1_truncated,
        "s1_generic_skipped": s1_generic_skipped,
        "comake_files": comake_files,
        "comake_truncated": comake_truncated,
        "backend_trees": [str(bt) for bt in backend_trees] if backend_trees else [],
        "src_root": str(src_root),
        "tree_root": str(tree_root) if tree_root is not None else "",
    }
    manifest_path = dest / "_closure" / "MANIFEST.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(copied), manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只读复制算子源码到快照(种子+#include 闭包), 供 source-analyst。"
    )
    parser.add_argument("--src-root", required=True, help="算子源码目录(种子来源)。")
    parser.add_argument("--dest", required=True, help="快照输出目录(项目内)。")
    parser.add_argument(
        "--tree-root",
        default=None,
        help="ops-* 子树根(闭包搜索范围, 省略则仅算子目录内可达)。"
             " --src-tree 大根模式下应传算子所属 ops-* 子树。",
    )
    parser.add_argument(
        "--backend-tree",
        dest="backend_trees",
        action="append",
        default=None,
        help="额外的非 ops-* 包根(如 operators-src/canndev), 可重复。仅供 R1 op-type 名"
             "注册驱动拉取: 按算子发射的 op-type 名在这些包的 .cc 查 IMPL_OP* 注册点,"
             "整文件拉到 _closure/legacy_optiling/(抓 canndev op_tiling 的 OP_TILING_CHECK)。"
             "不进 #include 闭包, 不拉 opbase(CommonOpExecutorRun)。",
    )
    args = parser.parse_args()
    count, manifest = snapshot_operator_source(
        Path(args.src_root), Path(args.dest),
        tree_root=Path(args.tree_root) if args.tree_root else None,
        backend_trees=[Path(bt) for bt in args.backend_trees] if args.backend_trees else None,
    )
    print(json.dumps({"ok": True, "copied_count": count, **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
