#!/usr/bin/env python3
"""SoC token -> product_support 产品名 匹配 CLI。

供 source-analyst（LLM agent）用 Bash 调用:把 raw_check.soc_scope 里的 SoC token
（aclrtGetSocName 字面量 / SocVersion 枚举名）映射到 constraints.json 的 product_support
产品名,输出 {matched, unknown}。matched 为命中的 product_support 原字符串(作 patch 的
target_platform),unknown 为不在映射表的 SoC token(写 source_evidence.unknown_socnames
供用户补表)。确定性薄包装,逻辑在 agent.generators.data_definition.soc_product_matrix。

用法:
  python scripts/match_soc_platform.py \
    --soc ASCEND910B,ASCEND310P \
    --constraints runs/<run>/iter_N/constraints.json
  python scripts/match_soc_platform.py \
    --soc Ascend950PR \
    --product-support-json '["Atlas 350 加速卡","Atlas A2 训练系列产品/Atlas A2 推理系列产品"]'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.generators.data_definition.soc_product_matrix import match_product_support


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SoC token -> product_support 产品名匹配(供 source-analyst 设 target_platform)。"
    )
    parser.add_argument(
        "--soc",
        required=True,
        help="逗号分隔的 SoC token 列表(来自 raw_check.soc_scope,如 ASCEND910B,Ascend950PR)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--constraints",
        help="constraints.json 路径,从中读 product_support",
    )
    group.add_argument(
        "--product-support-json",
        help="product_support JSON 数组字符串(逃生口)",
    )
    args = parser.parse_args()

    soc_tokens = [t.strip() for t in args.soc.split(",") if t.strip()]
    if args.constraints:
        try:
            data = json.loads(Path(args.constraints).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": f"读 constraints 失败: {exc}"},
                             ensure_ascii=False))
            return 2
        product_support = data.get("product_support", [])
        if not isinstance(product_support, list):
            print(json.dumps({"ok": False, "error": "constraints.product_support 非数组"},
                             ensure_ascii=False))
            return 2
    else:
        try:
            product_support = json.loads(args.product_support_json)
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": f"--product-support-json 解析失败: {exc}"},
                             ensure_ascii=False))
            return 2

    matched, unknown = match_product_support(soc_tokens, product_support)
    print(json.dumps(
        {"ok": True, "matched": matched, "unknown": unknown,
         "soc_tokens": soc_tokens, "product_support": product_support},
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
