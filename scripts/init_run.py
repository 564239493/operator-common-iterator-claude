#!/usr/bin/env python3
"""Create a run directory and initial workflow state."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from runtime_config import (
    ROOT,
    config_error_payload,
    find_latest_operator_prompt,
    resolve_input_path,
    validate_server_config,
)
from select_prompt import assemble
from locate_operator_source import locate_in_tree
from snapshot_source import snapshot_operator_source, _ops_subtree_root


def _derive_aclnn_name(doc: Path) -> str:
    """从算子文档文件名派生 aclnn 接口名: aclnnXxx.md -> aclnnXxx。

    不以 aclnn 开头返回空串(调用方需显式传 --aclnn-name 或退回无源码)。
    """
    stem = doc.stem
    return stem if stem.startswith("aclnn") else ""


def _snapshot_operator_source(
    src_root: Path, dest: Path, backend_trees: list[Path] | None = None
) -> int:
    """只读复制算子源码到快照(种子 + #include 闭包 + R1/S1/S2 legacy tiling 拉取)。

    实现已移至 scripts/snapshot_source.py:snapshot_operator_source(种子 glob +
    调用链共享头 #include 闭包 + _closure/MANIFEST.json + backend_trees 驱动的
    op-type 名注册/头声明/CMake 共属三机制拉 canndev op_tiling legacy tiling)。
    本函数保留为薄包装, 供 --source-root 单目录模式调用(tree_root=None, 算子目录外
    共享头不可达 → 记 unresolved_includes, source-analyst 标 missing_evidence;
    backend_trees 非空时仍可拉 canndev legacy tiling)。
    """
    count, _manifest = snapshot_operator_source(
        src_root, dest, tree_root=None, backend_trees=backend_trees
    )
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "创建 run 目录并初始化 workflow 状态。算子文档路径既可作为"
            "位置参数传入，也可通过 --doc 指定。"
        )
    )
    # 算子文档路径两种写法都接受:
    #   1) 位置参数:        init_run.py <doc>
    #   2) 显式 --doc flag:  init_run.py --doc <doc>
    # 显式 --doc 优先, 位置参数作为回退; 都不给则报错。
    parser.add_argument(
        "doc_pos",
        nargs="?",
        default=None,
        help="算子文档路径 (与 --doc 等价, 留空则必须用 --doc)",
    )
    parser.add_argument(
        "--doc",
        dest="doc",
        default=None,
        help="算子文档路径 (项目内或外部绝对路径)",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help=(
            "约束提取提示词路径；省略时自动选择 "
            "prompts/operator_constraints_extract_vN.md 中数值版本最大的文件"
        ),
    )
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--case-count", type=int, default=10)
    parser.add_argument("--mode", choices=("mock", "real"), default="real")
    parser.add_argument("--server-config", default="servers.json")
    parser.add_argument(
        "--source-root",
        dest="source_root",
        default=None,
        help=(
            "算子源码目录绝对路径(可选)。提供时把关键源码只读复制到 run/inputs/src_snapshot/,"
            "供 source-analyst 在每轮 EXTRACT 后校验约束类型/范围/表达式。不提供或为空则跳过"
            "源码分析,退回纯文档驱动。算子源码典型结构: op_host/<op>_def.cpp|_tiling*.cpp|"
            "op_api/aclnn_<op>.cpp|config/<platform>/<op>_binary.json 与 docs/aclnn<OpName>.md。"
            "局限: 仅复制该目录内文件 + 同目录可达的 #include; 算子目录外的共享头"
            "(如 common/inc/error_util.h、op_host/tiling_base.h)不可达, 闭包记为"
            " unresolved_includes, source-analyst 标 missing_evidence。需完整调用链源码"
            "(尤其 norm/conv/mc2 族共享 tiling helper)请改用 --src-tree。"
        ),
    )
    parser.add_argument(
        "--src-tree",
        dest="src_tree",
        default=None,
        help=(
            "算子源码树根目录(可选, 与 --source-root 二选一)。指向含多个 ops-* 子树的根"
            "(如项目内 operators-src) 时, 内部调 locate_operator_source.locate_in_tree 跨"
            "子树按 aclnn 名自动定位算子目录, 命中则快照, 未命中不阻断(回退纯文档驱动)。"
            "aclnn 名默认从 doc 文件名派生(aclnnXxx.md -> aclnnXxx), 不以 aclnn 开头时需"
            "显式传 --aclnn-name。--source-root 直传目录优先于本项。"
        ),
    )
    parser.add_argument(
        "--aclnn-name",
        dest="aclnn_name",
        default=None,
        help=(
            "aclnn 接口名(可选, 仅 --src-tree 模式生效)。覆盖从 doc 文件名派生的默认值,"
            "如 aclnnApplyRotaryPosEmb(不带 aclnn 前缀亦可)。"
        ),
    )
    parser.add_argument(
        "--backend-tree",
        dest="backend_trees",
        action="append",
        default=None,
        help=(
            "额外的非 ops-* 包根(如 operators-src/canndev), 可重复, 供 R1/S1/S2 拉取"
            "canndev op_tiling 的 legacy 图模式 tiling(IMPL_OP_OPTILING_LEGACY 等, 含 OP_TILING_CHECK)。"
            "省略时默认取 <src-tree>/canndev(--src-tree 模式且存在时); --source-root 模式需显式传。"
            "不进 #include 闭包, 不拉 opbase(CommonOpExecutorRun)。"
        ),
    )
    args = parser.parse_args()

    if args.doc is None:
        args.doc = args.doc_pos
    if not args.doc:
        parser.error(
            "必须提供算子文档路径: 位置参数 doc_pos 或 --doc 二选一。"
        )

    doc = resolve_input_path(args.doc)
    if args.prompt:
        prompt = resolve_input_path(args.prompt)
        explicit_prompt = True
    else:
        prompt = find_latest_operator_prompt()
        explicit_prompt = False
    if not doc.is_file():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "OPERATOR_DOC_NOT_FOUND",
                "message": "算子文档不存在，请提供绝对路径、项目相对路径或包含 .. 的相对路径。",
                "operator_doc": str(doc),
            },
            ensure_ascii=False,
        ))
        return 2
    if prompt is None or not prompt.is_file():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "PROMPT_NOT_FOUND",
                "message": (
                    "约束提取提示词不存在。请通过 --prompt 指定文件，或在 prompts "
                    "目录提供 operator_constraints_extract_vN.md。"
                ),
                "prompt": str(prompt) if prompt else "",
            },
            ensure_ascii=False,
        ))
        return 2
    if args.max_iterations < 1 or args.case_count < 1:
        raise SystemExit("max-iterations and case-count must be positive")

    server_config: Path | None = None
    if args.mode == "real":
        server_config, config_errors = validate_server_config(args.server_config)
        if config_errors:
            print(json.dumps(
                config_error_payload(server_config, config_errors),
                ensure_ascii=False,
            ))
            return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_id = f"{doc.stem}-{stamp}"
    run_dir = ROOT / "runs" / run_id
    input_dir = run_dir / "inputs"
    (run_dir / "iter_001").mkdir(parents=True, exist_ok=False)
    input_dir.mkdir(parents=True, exist_ok=False)

    # External inputs are read-only. All Agents consume immutable snapshots
    # inside this project so they never edit the user's original document.
    doc_snapshot = input_dir / doc.name
    prompt_snapshot = input_dir / "prompt_v1.md"
    shutil.copy2(doc, doc_snapshot)
    if explicit_prompt:
        # --prompt 逃生口：原样复制指定文件，不装配模块（用于固定版本/外部提示词）
        shutil.copy2(prompt, prompt_snapshot)
        loaded_modules = []
    else:
        # 默认：按算子特征装配 base + 命中模块 -> prompt_snapshot
        loaded_modules = assemble(prompt, doc_snapshot, prompt_snapshot)

    # Optional read-only snapshot of operator source code. Empty when neither
    # --source-root nor --src-tree is provided; downstream source-analyst is
    # then skipped and the flow falls back to pure document-driven mode
    # (EXTRACT->GENERATE->EXECUTE->GATE, no source artifacts).
    operator_src_source = ""
    operator_src_snapshot = ""
    operator_src_tree = ""
    operator_backend_trees: list[str] = []  # R1/S1/S2 拉取的非 ops-* 包根(canndev 等)
    operator_src_note = ""  # 定位提示(未命中/无 aclnn 名), 空表示无异常
    # backend_trees: R1 op-type 名注册驱动拉取的非 ops-* 包根(如 canndev op_tiling 的
    # legacy 图模式 tiling, 含 OP_TILING_CHECK)。默认取 <src-tree>/canndev(--src-tree 模式
    # 且存在时); 显式 --backend-tree 优先; --source-root 模式需显式传。
    backend_tree_paths: list[Path] = []
    if args.backend_trees:
        backend_tree_paths = [resolve_input_path(bt) for bt in args.backend_trees]
    elif args.src_tree:
        cand = resolve_input_path(args.src_tree) / "canndev"
        if cand.is_dir():
            backend_tree_paths = [cand]
    operator_backend_trees = [str(p) for p in backend_tree_paths]
    if args.source_root:
        # 直传算子目录模式: 跳过 locate 直接快照
        src_root = resolve_input_path(args.source_root)
        if not src_root.is_dir():
            print(json.dumps(
                {
                    "ok": False,
                    "requires_user_action": True,
                    "code": "OPERATOR_SRC_NOT_FOUND",
                    "message": (
                        "算子源码目录不存在。请提供有效的 --source-root 绝对路径,"
                        "或省略以跳过源码分析退回纯文档驱动。"
                    ),
                    "operator_src_source": str(args.source_root),
                },
                ensure_ascii=False,
            ))
            return 2
        src_snapshot = input_dir / "src_snapshot"
        src_snapshot.mkdir(parents=True, exist_ok=False)
        _snapshot_operator_source(
            src_root, src_snapshot, backend_trees=backend_tree_paths or None
        )
        operator_src_source = str(src_root)
        operator_src_snapshot = str(src_snapshot)
    elif args.src_tree:
        # 自动定位模式: 跨 ops-* 子树 locate 算子目录, 命中则快照, 未命中不阻断
        tree_root = resolve_input_path(args.src_tree)
        operator_src_tree = str(tree_root)
        if not tree_root.is_dir():
            print(json.dumps(
                {
                    "ok": False,
                    "requires_user_action": True,
                    "code": "OPERATOR_SRC_TREE_NOT_FOUND",
                    "message": (
                        "算子源码树根目录不存在。请提供有效的 --src-tree 路径,"
                        "或省略以跳过源码分析退回纯文档驱动。"
                    ),
                    "operator_src_tree": str(args.src_tree),
                },
                ensure_ascii=False,
            ))
            return 2
        aclnn = args.aclnn_name or _derive_aclnn_name(doc)
        if not aclnn:
            operator_src_note = (
                "OPERATOR_SRC_NOT_LOCATED: 无法从 doc 文件名派生 aclnn 名且未传 "
                "--aclnn-name, 跳过源码分析, 退回纯文档驱动。"
            )
        else:
            loc = locate_in_tree(aclnn, tree_root)
            if loc["ok"]:
                src_root = Path(loc["operator_dirs"][0])
                src_snapshot = input_dir / "src_snapshot"
                src_snapshot.mkdir(parents=True, exist_ok=False)
                # 收紧闭包搜索范围到算子所属 ops-* 子树(大根模式下避免全树 rglob
                # 与跨子树 basename 歧义), 拉取算子目录外共享头(error_util.h /
                # op_host/tiling_base.h / norm_tiling_check_common.h 等)到 _closure/。
                closure_tree = _ops_subtree_root(src_root, tree_root)
                snapshot_operator_source(
                    src_root, src_snapshot, tree_root=closure_tree,
                    backend_trees=backend_tree_paths or None,
                )
                operator_src_source = str(src_root)
                operator_src_snapshot = str(src_snapshot)
            else:
                operator_src_note = (
                    f"OPERATOR_SRC_NOT_LOCATED: 在 {tree_root} 的 ops-* 子树"
                    f"({loc.get('subtrees_searched', [])})中未定位到 {aclnn} 的源码目录,"
                    "跳过源码分析, 退回纯文档驱动。"
                )

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "run_id": run_id,
        "operator_doc_source": str(doc),
        "operator_doc": str(doc_snapshot),
        "operator_src_source": operator_src_source,
        "operator_src_snapshot": operator_src_snapshot,
        "operator_src_tree": operator_src_tree,
        "operator_backend_trees": operator_backend_trees,
        "current_prompt_source": str(prompt),
        "current_prompt": str(prompt_snapshot),
        "current_prompt_modules": loaded_modules,
        "mode": args.mode,
        "server_config": str(server_config) if server_config else "",
        "max_iterations": args.max_iterations,
        "case_count": args.case_count,
        "current_iteration": 1,
        "state": "PLAN",
        "history": [{"state": "PLAN", "at": now}],
        "created_at": now,
        "updated_at": now,
    }
    (run_dir / "run_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(
        {
            "ok": True,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "operator_doc_source": str(doc),
            "operator_doc_snapshot": str(doc_snapshot),
            "operator_src_source": operator_src_source,
            "operator_src_snapshot": operator_src_snapshot,
            "operator_src_tree": operator_src_tree,
            "operator_backend_trees": operator_backend_trees,
            "operator_src_note": operator_src_note,
            "prompt_snapshot": str(prompt_snapshot),
            "prompt_modules": loaded_modules,
            "mode": args.mode,
            "server_config": str(server_config) if server_config else "",
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
