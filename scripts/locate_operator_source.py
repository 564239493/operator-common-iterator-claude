#!/usr/bin/env python3
"""根据 aclnn 接口名定位算子源码目录。

主路径: 解析 ops-transformer/docs/zh/op_api_list.md 的接口表,链接路径即
aclnn 名 -> 算子目录的权威映射(处理一对多: 一个算子目录可承载多个 aclnn 接口,
如 apply_rotary_pos_emb 同时承载 aclnnApplyRotaryPosEmb 与 aclnnApplyRotaryPosEmbV2)。

退化路径: 当 op_api_list.md 不可用时,按命名规则 glob(去 aclnn 前缀, PascalCase
转 snake_case, 分别尝试保留与剥离版本后缀 Vn/VarLen/WeightNz/Quant)。命名规则
存在歧义(如 moe_init_routing_v2 是独立目录, 而 apply_rotary_pos_emb_v2 共用
apply_rotary_pos_emb 目录), 故退化结果仅作候选, 以 op_api_list.md 为准。

输出 JSON, 供 /iterate-operator 在调 init_run --source-root 前定位源码目录。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 默认指向项目内 operators-src(含多个 ops-* 子树的大根); 也可传单 ops-* 子树。
DEFAULT_SRC_TREE = str(_PROJECT_ROOT / "operators-src")

# op_api_list.md 表格行: |[aclnnXxx](../../<class>/<op_dir>/docs/aclnnXxx.md)|...
ROW_RE = re.compile(r"\[(aclnn\w+)\]\(\.\./\.\./([\w/]+)/docs/aclnn\w+\.md\)")
# 版本/变体后缀(同一算子目录的接口变体)。Grad 是反向算子(独立目录), 不剥。
VERSION_SUFFIX = re.compile(r"(?:V\d+|VarLen|WeightNz|Quant)+$")


def _to_snake(pascal: str) -> str:
    """PascalCase -> snake_case。ApplyRotaryPosEmb -> apply_rotary_pos_emb。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", pascal).lower()


def candidate_dir_names(aclnn: str) -> list[str]:
    """从 aclnn 名生成候选算子目录名(保留版本后缀 + 剥离版本后缀两种)。"""
    if aclnn.startswith("aclnn"):
        aclnn = aclnn[5:]
    full = _to_snake(aclnn)  # 保留 V2 等, 如 apply_rotary_pos_emb_v2
    stripped = VERSION_SUFFIX.sub("", aclnn)  # 剥版本后缀
    stripped = _to_snake(stripped)  # 如 apply_rotary_pos_emb
    names = []
    if full and full not in names:
        names.append(full)
    if stripped and stripped not in names:
        names.append(stripped)
    return names


def load_api_list_mapping(src_tree: Path) -> dict[str, list[str]]:
    """从 docs/zh/op_api_list.md 建 aclnn 名 -> 算子目录相对路径列表。"""
    api_list = src_tree / "docs" / "zh" / "op_api_list.md"
    if not api_list.is_file():
        return {}
    mapping: dict[str, list[str]] = {}
    for line in api_list.read_text(encoding="utf-8", errors="replace").splitlines():
        m = ROW_RE.search(line)
        if m:
            aclnn, rel = m.group(1), m.group(2)
            if rel not in mapping.setdefault(aclnn, []):
                mapping[aclnn].append(rel)
    return mapping


def locate(aclnn: str, src_tree: Path) -> dict:
    if not aclnn.startswith("aclnn"):
        aclnn = "aclnn" + aclnn
    mapping = load_api_list_mapping(src_tree)
    dirs: list[str] = []
    source = ""
    if aclnn in mapping:
        source = "op_api_list"
        for rel in mapping[aclnn]:
            d = src_tree / rel
            if d.is_dir() and str(d) not in dirs:
                dirs.append(str(d))
    if not dirs:
        source = "naming_glob"
        for name in candidate_dir_names(aclnn):
            # rglob 兼容不同分类深度: ops-transformer 算子在 <src_tree>/<class>/<op>,
            # ops-math 在 <src_tree>/<class>/<sub>/<op>。仅作候选, 最终以 op_api_list 为准。
            for d in src_tree.rglob(name):
                if d.is_dir() and str(d) not in dirs:
                    dirs.append(str(d))
    return {
        "ok": bool(dirs),
        "aclnn": aclnn,
        "operator_dirs": dirs,
        "source": source,
        "src_tree": str(src_tree),
    }


def locate_in_tree(aclnn: str, tree_root: Path) -> dict:
    """跨 tree_root 下 ops-* 子树定位算子目录。

    项目内 operators-src 是含多个 ops-* 子树(ops-transformer/ops-math/ops-nn ...)的大根,
    每个子树有自己的 docs/zh/op_api_list.md 与算子目录。本函数遍历名字以 ops- 开头的
    子目录(排序保确定序), 对每个复用 locate(aclnn, ops_subdir), 首个命中即返回
    (result 增补 subtree 字段标明命中子树); 全未命中返回 ok=False + 已搜索子树清单。
    命名规则歧义与一对多仍由 locate 内部处理, 本函数只做子树编排。
    """
    if not aclnn.startswith("aclnn"):
        aclnn = "aclnn" + aclnn
    subtrees: list[Path] = []
    if tree_root.is_dir():
        subtrees = sorted(
            p for p in tree_root.iterdir() if p.is_dir() and p.name.startswith("ops-")
        )
    for sub in subtrees:
        r = locate(aclnn, sub)
        if r["ok"]:
            r["subtree"] = sub.name
            return r
    return {
        "ok": False,
        "aclnn": aclnn,
        "operator_dirs": [],
        "source": "in_tree_miss",
        "src_tree": str(tree_root),
        "subtrees_searched": [s.name for s in subtrees],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="根据 aclnn 接口名定位算子源码目录(供 init_run --source-root 使用)。"
    )
    parser.add_argument(
        "--aclnn-name",
        required=True,
        help="aclnn 接口名, 如 aclnnApplyRotaryPosEmb(不带 aclnn 前缀亦可)。",
    )
    parser.add_argument(
        "--src-tree",
        default=DEFAULT_SRC_TREE,
        help=(
            f"算子源码树根目录(默认项目内 {DEFAULT_SRC_TREE}, 含多个 ops-* 子树)。"
            "指向单 ops-* 子树(如 ops-transformer)时只在该子树内定位; 指向含多个 "
            "ops-* 的根(如 operators-src)时跨子树搜索。init_run --src-tree 内部调用 "
            "本函数, 独立运行时用本 CLI。"
        ),
    )
    args = parser.parse_args()
    src_tree = Path(args.src_tree)
    if not src_tree.is_dir():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "SRC_TREE_NOT_FOUND",
                "message": "ops-transformer 源码树不存在,请用 --src-tree 指定,或省略 --source-root 跳过源码分析。",
                "src_tree": str(src_tree),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 2
    if src_tree.name.startswith("ops-"):
        # 单 ops-* 子树: 直接在该子树内定位
        result = locate(args.aclnn_name, src_tree)
    else:
        # 含多个 ops-* 子树的根(如项目内 operators-src): 跨子树搜索
        result = locate_in_tree(args.aclnn_name, src_tree)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
