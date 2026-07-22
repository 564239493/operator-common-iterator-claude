#!/usr/bin/env python3
"""确定性算子分类脚本 (通算融合识别).

输入算子文档快照, 按 scheme.html 1.2 的规则判定是否为通算融合算子,
输出 JSON 到 stdout (不落盘).  orchestrator 在 EXTRACT 后调用, 读 stdout
回写 run_state.execution_strategy + operator_category + evidence.

判定逻辑 (S1 为唯一可靠强规则, 单独覆盖 7/7; S2/S3/W1 为佐证):
  - S1 命名匹配 → fusion
  - S1 未命中时 S2/S3/W1 任一命中亦判 fusion
  - 全不命中 → default

不依赖 constraint-extractor 的自由文本输出, 不落盘到 constraints.json,
与约束语义彻底解耦.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# S1: 命名匹配 (大小写不敏感). 单独覆盖 7/7 通算融合算子.
_S1_PATTERNS = [
    re.compile(
        r"^aclnn(AllToAll|AlltoAll|AlltoAllv|AllGather|ReduceScatter)"
        r".*(Matmul|MatMul|BatchMatMul|GroupedMatMul)$",
        re.IGNORECASE,
    ),
    # 反序形态: aclnnBatchMatMulReduceScatterAlltoAll
    re.compile(r"^aclnnBatchMatMul.*(ReduceScatter|AllToAll).*$", re.IGNORECASE),
]

# S3: 显式字样 (功能说明 / 约束说明出现).
_S3_PHRASES = (
    "通算融合",
    "通算融合MC2算子",
    "通算融合算子不支持并发调用",
    "是通算融合算子",
)

# W1: 特征组合 (通信原语 + 计算原语 + "融合" + 通信域参数 + worldSize 参数).
_W1_COMM_PRIMITIVES = ("AllToAll", "AlltoAll", "AlltoAllv", "AllGather", "ReduceScatter")
_W1_COMPUTE_PRIMITIVES = ("Matmul", "MatMul", "BatchMatMul", "GroupedMatMul")
_W1_GROUP_PARAMS = ("group", "groupEp", "groupTp")
_W1_WORLDSIZE_PARAMS = ("rankSize", "epWorldSize", "tpWorldSize", "rank_size", "worldSize")


def _match_s1(operator_name: str) -> bool:
    return any(p.match(operator_name) for p in _S1_PATTERNS)


def _match_s2(text: str) -> bool:
    """S2 仅作佐证: 库内快照副本可能不含 gitcode/mc2 链接."""
    return "gitcode.com" in text and "/mc2/" in text


def _match_s3(text: str) -> bool:
    return any(phrase in text for phrase in _S3_PHRASES)


def _match_w1(text: str, operator_name: str) -> bool:
    has_comm = any(p in operator_name for p in _W1_COMM_PRIMITIVES)
    has_compute = any(p in operator_name for p in _W1_COMPUTE_PRIMITIVES)
    has_fusion = "融合" in text
    has_group = any(g in text for g in _W1_GROUP_PARAMS)
    has_worldsize = any(w in text for w in _W1_WORLDSIZE_PARAMS)
    return has_comm and has_compute and has_fusion and has_group and has_worldsize


def classify(doc_path: Path) -> dict:
    """Return {'operator_category', 'evidence'} for the operator doc."""
    operator_name = doc_path.stem  # e.g. aclnnAlltoAllMatmul
    try:
        text = doc_path.read_text(encoding="utf-8")
    except Exception as exc:
        return {"operator_category": "default", "evidence": [f"文档读取失败: {exc}"]}

    evidence: list[str] = []
    s1 = _match_s1(operator_name)
    if s1:
        evidence.append(f"S1 命名匹配: {operator_name}")
    if _match_s2(text):
        evidence.append("S2 mc2 源码路径: 文档含 gitcode.com/.../mc2/ 链接")
    if _match_s3(text):
        evidence.append("S3 显式字样: 文档出现通算融合关键词")
    if _match_w1(text, operator_name):
        evidence.append("W1 特征组合: 通信原语+计算原语+融合+group+worldSize")

    # S1 命中 → fusion; S1 未命中时 S2/S3/W1 任一命中亦判 fusion; 全不命中 → default
    is_fusion = s1 or len(evidence) > 0
    category = "fusion_comm_compute" if is_fusion else "default"
    return {"operator_category": category, "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="确定性算子分类 (通算融合识别), 输出 JSON 到 stdout, 不落盘."
    )
    parser.add_argument(
        "--doc",
        required=True,
        help="算子文档快照路径 (runs/<run-id>/inputs/<doc>.md).",
    )
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover — best effort
        pass
    doc_path = Path(args.doc).expanduser().resolve()
    if not doc_path.is_file():
        print(
            json.dumps(
                {"operator_category": "default", "evidence": [f"文档不存在: {doc_path}"]},
                ensure_ascii=False,
            )
        )
        return 1
    result = classify(doc_path)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
