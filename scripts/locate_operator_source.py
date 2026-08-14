#!/usr/bin/env python3
"""根据 aclnn 接口名定位算子源码目录（供 init_run --src 使用）。

主路径：扫源码树下所有 ``docs/zh/op_api_list.md`` 表格行，链接路径即
aclnn 名 -> 算子目录的权威映射。处理一对多（一个算子目录可承载多个 aclnn
接口，如 ``aclnnAcos&aclnnInplaceAcos`` 共用 acos 目录）——按 ``&`` 拆分链接
文本，每个接口名都映射到同一目录。

退化路径：op_api_list.md 不可用时，按命名规则 glob（去 aclnn 前缀，
PascalCase 转 snake_case，分别尝试保留与剥离版本后缀 Vn/VarLen/WeightNz/Quant）。
命名规则存在歧义（如 moe_init_routing_v2 是独立目录，而 apply_rotary_pos_emb_v2
共用 apply_rotary_pos_emb 目录），故退化结果仅作候选，以 op_api_list.md 为准。

输出 JSON，供 /iterate-operator 在调 init_run --src 前定位源码目录。仅扫
项目内 operators-src 树，不触外部。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC_TREE = ROOT / "operators-src"

# op_api_list.md 表格行: |[aclnnXxx](../../<class>/<op_dir>/docs/aclnnXxx.md)|...
# 链接文本可能含 & (多接口共用目录): aclnnAcos&aclnnInplaceAcos。
# 组1=接口名文本(含&), 组2=算子目录相对路径(<class>/<op_dir>)。
ROW_RE = re.compile(r"\[(aclnn[A-Za-z0-9_&]+)\]\(\.\./\.\./([\w/]+)/docs/[^)]+\.md\)")
# 版本/变体后缀（同一算子目录的接口变体）。Grad 是反向算子（独立目录），不剥。
VERSION_SUFFIX = re.compile(r"(?:V\d+|VarLen|WeightNz|Quant)+$")


def _to_snake(pascal: str) -> str:
    """PascalCase -> snake_case。ApplyRotaryPosEmb -> apply_rotary_pos_emb。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", pascal).lower()


def candidate_dir_names(aclnn: str) -> list[str]:
    """从 aclnn 名生成候选算子目录名（保留版本后缀 + 剥离版本后缀两种）。"""
    if aclnn.startswith("aclnn"):
        aclnn = aclnn[5:]
    full = _to_snake(aclnn)  # 保留 V2 等, 如 apply_rotary_pos_emb_v2
    stripped = VERSION_SUFFIX.sub("", aclnn)  # 剥版本后缀
    stripped = _to_snake(stripped)  # 如 apply_rotary_pos_emb
    names: list[str] = []
    if full and full not in names:
        names.append(full)
    if stripped and stripped not in names:
        names.append(stripped)
    return names


def _find_api_lists(src_tree: Path) -> list[Path]:
    """找源码树下所有 docs/zh/op_api_list.md（每个 ops-* 子树一个）。"""
    if not src_tree.is_dir():
        return []
    return sorted(src_tree.glob("*/docs/zh/op_api_list.md"))


def load_api_list_mapping(src_tree: Path) -> dict[str, list[str]]:
    """从所有 op_api_list.md 建 aclnn 名 -> 算子目录绝对路径列表。

    链接 ``../../<class>/<op_dir>`` 相对于 ``docs/zh/``，向上两级到 ``<ops-*>``，
    故完整路径 = ``<src_tree>/<ops-*>/<class>/<op_dir>``。
    """
    mapping: dict[str, list[str]] = {}
    for api_list in _find_api_lists(src_tree):
        # api_list = <src_tree>/<ops-*>/docs/zh/op_api_list.md
        # 向上三级到 <ops-*>，再拼链接里的 <class>/<op_dir>。
        ops_subtree = api_list.parent.parent.parent
        text = api_list.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            m = ROW_RE.search(line)
            if not m:
                continue
            iface_text, rel = m.group(1), m.group(2)
            full_dir = ops_subtree / rel
            dir_str = str(full_dir)
            # 一个链接文本可能含多个接口名（& 分隔），每个都映射到同一目录。
            for aclnn in iface_text.split("&"):
                aclnn = aclnn.strip()
                if not aclnn:
                    continue
                bucket = mapping.setdefault(aclnn, [])
                if dir_str not in bucket:
                    bucket.append(dir_str)
    return mapping


def locate(aclnn: str, src_tree: Path) -> dict:
    if not aclnn.startswith("aclnn"):
        aclnn = "aclnn" + aclnn
    mapping = load_api_list_mapping(src_tree)
    dirs: list[str] = []
    source = ""
    if aclnn in mapping:
        source = "op_api_list"
        for d in mapping[aclnn]:
            if Path(d).is_dir() and d not in dirs:
                dirs.append(d)
    if not dirs:
        source = "naming_glob"
        for name in candidate_dir_names(aclnn):
            for d in src_tree.glob(f"*/{name}"):
                if d.is_dir() and str(d) not in dirs:
                    dirs.append(str(d))
    return {
        "ok": bool(dirs),
        "aclnn": aclnn,
        "operator_dirs": dirs,
        "source": source,
        "src_tree": str(src_tree),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="根据 aclnn 接口名定位算子源码目录（供 init_run --src 使用）。"
    )
    parser.add_argument(
        "--aclnn-name",
        required=True,
        help="aclnn 接口名，如 aclnnNpuFormatCast（不带 aclnn 前缀亦可）。",
    )
    parser.add_argument(
        "--src-tree",
        default=str(DEFAULT_SRC_TREE),
        help=f"源码树根目录（默认 {DEFAULT_SRC_TREE}）。",
    )
    args = parser.parse_args()
    src_tree = Path(args.src_tree)
    if not src_tree.is_dir():
        print(json.dumps(
            {
                "ok": False,
                "requires_user_action": True,
                "code": "SRC_TREE_NOT_FOUND",
                "message": "源码树不存在，请用 --src-tree 指定，或省略 --src 跳过源码分析。",
                "src_tree": str(src_tree),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 2
    result = locate(args.aclnn_name, src_tree)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
