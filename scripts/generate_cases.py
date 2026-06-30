#!/usr/bin/env python3
"""Deterministically call the retained business case generator.

Writes:
- ``<output>``             — cases.json (JSON array; contract artifact)
- ``<output_dir>/generation_summary.json`` — per-platform case counts +
  coverage metrics
- ``<iter_dir>/generation.log`` — ``--iter-dir`` 传入时额外写一份可追踪日志

调用流程：
1. 读取 ``constraints.json``，传给 ``generators.facade.TestCaseGenerator``。
2. facade 内部按平台调用 ``single_operator_handle`` 走 formal 算子生成
   代码，产出原始 ``CaseConfig`` 列表。
3. ``generators.nz_postprocess.fix_and_serialize`` 对原始结果做确定性
   后处理：
     - mat2 强制 5 维 NZ，且 mat2.shape[3] == 16、mat2.shape[4] == 16、
       mat2.shape[1] != 1、mat2.shape[2] != 1；
     - self.shape = (B, M, k1*16)、out.shape = (B, M, n1*16)；
     - self/mat2/out dtype 一致（BFLOAT16 或 FLOAT16）；
     - cubeMathType ∈ {0, 2}，并随 dtype 互斥；
     - id 改写为 (platform, idx) 复合形式，并注入 platform 字段。
4. 把结果落盘并写 summary。

修复目的：避免上一轮 (aclnnBatchMatMulWeightNz-20260630-150530-734399)
的 generator_bug —— id 重复 0-9 + mat2 shape 不满足 NZ 约束，导致
30→10 覆盖缺口与 8/10 FAILED。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("generate_cases")


def serializable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    return value


def _setup_iter_log(iter_dir: Path) -> Path | None:
    """Mirror the script's run into ``<iter_dir>/generation.log``."""
    try:
        iter_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    log_path = iter_dir / "generation.log"
    try:
        handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        return log_path
    except OSError as exc:
        print(f"[generate_cases] warning: cannot open {log_path}: {exc}", file=sys.stderr)
        return None


def _check_nz_shape(shape: Any) -> bool:
    """返回 True 表示该 mat2.shape 不满足 NZ 硬约束。

    NZ (non-transposed) layout: (b, n1, k1, k0, n0) where k0=n0=16，
    且 n1 != 1、k1 != 1。
    """
    if not isinstance(shape, list) or len(shape) != 5:
        return True
    b, n1, k1, k0, n0 = shape
    if b < 1 or n1 < 1 or k1 < 1:
        return True
    if k0 != 16 or n0 != 16:
        return True
    if n1 == 1 or k1 == 1:
        return True
    return False


def _compute_coverage(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """统计每条 case 的关键字段，计算覆盖率指标。"""
    dtypes_seen: set[str] = set()
    math_types_seen: set[int] = set()
    nz_shape_violations = 0
    per_platform: dict[str, int] = {}
    id_collisions: set[str] = set()
    seen_ids: set[str] = set()
    for case in cases:
        plat = case.get("platform", "<unknown>")
        per_platform[plat] = per_platform.get(plat, 0) + 1
        cid = case.get("id")
        if cid in seen_ids:
            id_collisions.add(str(cid))
        seen_ids.add(str(cid))
        mat2 = next(
            (inp for inp in case.get("inputs", []) if inp.get("name") == "mat2"),
            None,
        )
        for inp in case.get("inputs", []):
            name = inp.get("name")
            dtype = inp.get("dtype")
            if name in ("self", "mat2", "out") and dtype:
                dtypes_seen.add(dtype)
            if name == "cubeMathType":
                rv = inp.get("range_values")
                if rv is not None:
                    try:
                        math_types_seen.add(int(rv))
                    except (TypeError, ValueError):
                        pass
        if mat2 is not None and _check_nz_shape(mat2.get("shape")):
            nz_shape_violations += 1
    return {
        "dtypes_seen": sorted(dtypes_seen),
        "cube_math_types_seen": sorted(math_types_seen),
        "nz_shape_violations": nz_shape_violations,
        "unique_platforms": len(per_platform),
        "unique_ids": len(seen_ids),
        "id_collisions": sorted(id_collisions),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--constraints", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--iter-dir",
        default=None,
        help="可选: 迭代目录 (如 runs/<run>/iter_001)。传入后, 生成过程日志会写到 <iter-dir>/generation.log。",
    )
    args = parser.parse_args()
    if args.count < 1:
        raise SystemExit("count must be positive")

    iter_dir = Path(args.iter_dir) if args.iter_dir else None
    iter_log_path = _setup_iter_log(iter_dir) if iter_dir else None

    started = time.monotonic()
    logger.info(
        "start: constraints=%s output=%s count=%d seed=%d iter_dir=%s",
        args.constraints,
        args.output,
        args.count,
        args.seed,
        iter_dir or "(none)",
    )

    constraints_path = Path(args.constraints)
    output_path = Path(args.output)
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    logger.info(
        "constraints loaded: operator=%s, product_support=%d 项",
        constraints.get("operator_name", "<unknown>"),
        len(constraints.get("product_support", [])),
    )

    from generators.facade import TestCaseGenerator
    from generators.nz_postprocess import fix_and_serialize

    generator = TestCaseGenerator(constraints, seed=args.seed)
    by_platform = generator.generate_by_platform(args.count)
    logger.info(
        "raw generator output per platform: %s",
        {k: len(v) for k, v in by_platform.items()},
    )

    # 修正 NZ 形状 / dtype / cubeMathType 并把 id 改写为 (platform, idx) 复合形式
    cases = fix_and_serialize(by_platform, args.count)
    if not cases:
        logger.error("generator produced no cases")
        raise SystemExit("generator produced no cases")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    coverage = _compute_coverage(cases)
    per_platform: dict[str, int] = {}
    for case in cases:
        plat = case.get("platform", "<unknown>")
        per_platform[plat] = per_platform.get(plat, 0) + 1

    summary = {
        "operator_name": generator.operator_name,
        "requested_per_platform": args.count,
        "platforms": per_platform,
        "total": len(cases),
        "seed": args.seed,
        "generator_version": (
            "facade.TestCaseGenerator(single_operator_handle) + "
            "generators.nz_postprocess.fix_and_serialize"
        ),
        "id_format": "(platform, idx) composite string id, 例 'Atlas 350 加速卡::000'",
        "coverage": coverage,
    }
    (output_path.parent / "generation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    elapsed = time.monotonic() - started
    logger.info(
        "done: wrote %d cases (%s) to %s in %.2fs",
        len(cases),
        ", ".join(f"{k}={v}" for k, v in per_platform.items()),
        output_path,
        elapsed,
    )
    logger.info("coverage: %s", coverage)
    if iter_log_path is not None:
        logger.info("generation log: %s", iter_log_path)

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
