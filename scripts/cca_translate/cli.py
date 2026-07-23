"""cca-translate 命令行入口（纯标准库，路径全部由参数给出，无写死路径）。

子命令：
- locate      : 从 manifest.json 找某算子入口/子函数的 fn-*.md 路径
- parse       : 解析 fn-*.md「## 行为划分」→ 分支树 IR(JSON)
- reconcile   : 文档 constraints.json(某产品) vs fn-*.md 行为划分 → 参数共现缺口
- check       : 对翻译批次做 ast.parse 语法自检（非 stub / 非 TODO 的 expr 必过）
- verify-coverage : 前提保留自检(advisory)——guard 值比较前提是否被 code-expr 保留
- build-final : 原 constraints.json + 翻译批次 → 最终 constraints.json(保持原格式)

所有子命令均不假设固定路径，路径由 --cca-dir / --fn-md / --doc-constraints /
--batch / --original / --output 等参数显式传入。

用法示例见 .claude/skills/cca-translate/SKILL.md。
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import sys
from pathlib import Path

from .cca_parse import count_branches, parse_behavior_partition
from .reconcile import reconcile
from .verify_coverage import verify as verify_coverage

logger = logging.getLogger("cca_translate.cli")


def _configure_logging(debug: bool = False) -> None:
    """沿用本仓库 logging 约定（见 scripts/generate_cases.py）。幂等。"""
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(handler)
    root.setLevel(level)


# ============================ 公共工具 ============================

def _read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _load_json(path: str | Path) -> dict | list:
    return json.loads(_read_text(path))


def _dump_json(obj, out: str | Path | None) -> None:
    if not out:
        json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("写出: %s", p)


def _doc_conditions(data: dict, product: str | None) -> list[dict]:
    """从 constraints.json 取某产品的 conditions 列表。

    支持两种 constraints_in_parameters 形态：
    - dict(按平台分桶): {product: [条件...]}，需 --product
    - list(扁平): [条件...]，--product 可省
    """
    cip = data.get("constraints_in_parameters", [])
    if isinstance(cip, dict):
        if not product:
            raise SystemExit(
                "constraints_in_parameters 为按平台分桶(dict)，必须提供 --product；"
                f"可选产品: {list(cip)}"
            )
        if product not in cip:
            raise SystemExit(
                f"产品 {product!r} 不在 constraints_in_parameters；可选: {list(cip)}"
            )
        return cip[product]
    if isinstance(cip, list):
        return cip
    raise SystemExit(f"constraints_in_parameters 既非 dict 也非 list: {type(cip).__name__}")


def _iter_batch_items(batch: dict):
    """统一遍历批次里的条目（已分桶 + 带 category 的 items）。

    yield (bucket, item) — bucket 为该条目应落入的桶名。
    """
    BUCKETS = ("constraints_in_parameters", "error_branches", "ub_branches",
               "normalize_rules", "unreachable")
    for b in BUCKETS:
        for item in batch.get(b, []):
            yield b, dict(item)
    for item in batch.get("items", []):
        yield item.get("category"), dict(item)


# ============================ locate ============================

def _cmd_locate(args: argparse.Namespace) -> int:
    cca_dir = Path(args.cca_dir)
    manifest_path = cca_dir / "manifest.json"
    if not manifest_path.exists():
        # 部分发布把 manifest 放在 _cca_analysis_result/ 子目录
        alt = cca_dir / "_cca_analysis_result" / "manifest.json"
        if alt.exists():
            manifest_path = alt
        else:
            raise SystemExit(f"找不到 manifest.json: {manifest_path}")
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, list):
        raise SystemExit(f"manifest.json 期望为 list，实际 {type(manifest).__name__}")

    entries = manifest
    # 过滤
    if args.entry_suffix:
        entries = [e for e in entries if str(e.get("id", "")).endswith(args.entry_suffix)]
    elif args.operator:
        entries = [
            e for e in entries
            if args.operator in str(e.get("id", ""))
            or args.operator in str(e.get("analysis_file", ""))
            or args.operator in str(e.get("function", ""))
        ]
    # manifest 里 analysis_file 可能相对 cca-dir，也可能绝对；统一解析可定位
    base_dir = manifest_path.parent
    for e in entries:
        af = e.get("analysis_file") or e.get("file") or ""
        if not af:
            continue
        p = Path(af)
        resolved = p if p.is_absolute() else (base_dir / p)
        # 若 base_dir 下找不到，回退到 cca-dir 根
        if not resolved.exists() and not p.is_absolute():
            alt2 = cca_dir / p
            if alt2.exists():
                resolved = alt2
        print(json.dumps({
            "id": e.get("id"),
            "analysis_file": str(resolved),
            "exists": resolved.exists(),
        }, ensure_ascii=False))
    logger.info("locate: 命中 %d 条 (manifest=%s)", len(entries), manifest_path)
    return 0


# ============================ parse ============================

def _cmd_parse(args: argparse.Namespace) -> int:
    md = _read_text(args.fn_md)
    roots = parse_behavior_partition(md)
    out = {
        "fn_md": str(Path(args.fn_md).resolve()),
        "branch_count": count_branches(roots),
        "roots": [b.to_dict() for b in roots],
    }
    _dump_json(out, args.out)
    return 0


# ============================ reconcile ============================

def _cmd_reconcile(args: argparse.Namespace) -> int:
    data = _load_json(args.doc_constraints)
    conditions = _doc_conditions(data, args.product)
    md = _read_text(args.fn_md)
    roots = parse_behavior_partition(md)
    result = reconcile(conditions, roots)
    result["operator_name"] = data.get("operator_name")
    result["product"] = args.product
    result["fn_md"] = str(Path(args.fn_md).resolve())
    _dump_json(result, args.out)
    return 0


# ============================ check ============================

def _cmd_check(args: argparse.Namespace) -> int:
    failures = []
    checked = 0
    for batch_path in args.batch:
        batch = _load_json(batch_path)
        for bucket, item in _iter_batch_items(batch):
            expr = item.get("expr", "")
            if item.get("stub") or not expr or expr.startswith("TODO"):
                continue
            checked += 1
            try:
                ast.parse(expr, mode="eval")
            except SyntaxError as e:
                failures.append({
                    "batch": str(batch_path),
                    "bucket": bucket,
                    "branch_ref": item.get("branch_ref"),
                    "expr": expr,
                    "error": f"{e.msg} (line {e.lineno} col {e.offset})",
                })
    summary = {"checked": checked, "failed": len(failures), "failures": failures}
    if args.out:
        _dump_json(summary, args.out)
    else:
        for f in failures:
            logger.error("语法自检失败: %s\n  expr: %s\n  err: %s",
                         f.get("branch_ref"), f.get("expr"), f.get("error"))
        logger.info("check: %d 条已检，%d 条失败", checked, len(failures))
    return 1 if failures else 0


# ============================ verify-coverage ============================

def _cmd_verify_coverage(args: argparse.Namespace) -> int:
    """前提保留自检（advisory）。

    对每条 IR 分支的「入参值比较」前提参数，检查是否被链到该分支的 code-expr
    保留；列出未保留项供人复核。advisory：始终 exit 0，不阻断。
    """
    parse_ir = _load_json(args.parse_ir)
    batches = [_load_json(b) for b in args.batch]
    doc = _load_json(args.doc_constraints) if args.doc_constraints else None
    report = verify_coverage(parse_ir, batches, doc, args.product)
    if args.out:
        _dump_json(report, args.out)
    else:
        fs = report["findings"]
        logger.info("verify-coverage: %d 条带前提分支，%d 条需复核 (advisory)",
                    report["branches_with_premises"], len(fs))
        for f in fs:
            logger.warning(
                "⚠ 前提未保留 [branch %s callee=%s outcome=%s] premise=%s "
                "uncovered_in_code_expr=%s\n  guard: %s\n  %s",
                f["branch_path"], f.get("callee"), f.get("outcome_kind"),
                f["premise_params"],
                [(a["param"], "doc-covered" if a["doc_covered"] else "NOT-in-doc")
                 for a in f["uncovered_in_code_expr"]],
                f["guard_excerpt"], f["hint"],
            )
    return 0


# ============================ build-final ============================

def _cmd_build_final(args: argparse.Namespace) -> int:
    from .build_final import build_final
    final, code_side = build_final(args.original, args.batch, args.product)
    _dump_json(final, args.output)
    # 代码侧 sidecar：UB/error/normalize/unreachable/stubs/对账日志。
    # OperatorRule extra:forbid，塞进主文件会破坏与文档 constraints.json 的格式一致性，
    # 故单独成文件保留（不丢信息）。默认写到 <output>.code_side.json。
    sidecar = args.code_side_output or (str(args.output) + ".code_side.json")
    _dump_json(code_side, sidecar)
    return 0


# ============================ main ============================

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cca_translate",
        description="cca 行为划分 → 补充约束 翻译/对账/合并（纯标准库，路径全参数化）",
    )
    p.add_argument("--debug", action="store_true", help="开启 DEBUG 日志")
    sub = p.add_subparsers(dest="cmd", required=True)

    # locate
    pl = sub.add_parser("locate", help="从 manifest.json 找算子入口/子函数的 fn-*.md")
    pl.add_argument("--cca-dir", required=True, help="cca 分析树根目录（含 manifest.json + fn-*.md）")
    pl.add_argument("--operator", default=None, help="算子名片段（如 aclnnGroupedMatmulV5）")
    pl.add_argument("--entry-suffix", default=None, help="入口 id 后缀（如 ::aclnnGroupedMatmulV5GetWorkspaceSize）")
    pl.set_defaults(func=_cmd_locate)

    # parse
    pp = sub.add_parser("parse", help="解析 fn-*.md「## 行为划分」→ 分支树 IR(JSON)")
    pp.add_argument("--fn-md", required=True, help="fn-*.md 路径")
    pp.add_argument("--out", default=None, help="输出 JSON 路径（缺省打印到 stdout）")
    pp.set_defaults(func=_cmd_parse)

    # reconcile
    pr = sub.add_parser("reconcile", help="文档 constraints vs fn-*.md → 参数共现缺口")
    pr.add_argument("--doc-constraints", required=True, help="文档分析的 constraints.json")
    pr.add_argument("--fn-md", required=True, help="fn-*.md 路径")
    pr.add_argument("--product", default=None, help="产品键（constraints_in_parameters 为 dict 时必填）")
    pr.add_argument("--out", default=None, help="输出 JSON 路径（缺省打印到 stdout）")
    pr.set_defaults(func=_cmd_reconcile)

    # check
    pc = sub.add_parser("check", help="对翻译批次做 ast.parse 语法自检")
    pc.add_argument("--batch", required=True, action="append", help="翻译批次 JSON（可多次）")
    pc.add_argument("--out", default=None, help="自检报告 JSON 路径（缺省只打日志）")
    pc.set_defaults(func=_cmd_check)

    # verify-coverage
    pv = sub.add_parser("verify-coverage",
                       help="前提保留自检（advisory）：guard 值比较前提是否被 code-expr 保留")
    pv.add_argument("--parse-ir", required=True, help="parse 子命令产出的 IR JSON")
    pv.add_argument("--batch", required=True, action="append", help="翻译批次 JSON（可多次）")
    pv.add_argument("--doc-constraints", default=None,
                    help="文档 constraints.json（用于取入参名 + 标注 doc 覆盖；缺省则仅按 IR 兜底）")
    pv.add_argument("--product", default=None,
                    help="产品键（doc-constraints 为按平台分桶时必填，用于 doc 覆盖标注）")
    pv.add_argument("--out", default=None, help="自检报告 JSON 路径（缺省只打日志）")
    pv.set_defaults(func=_cmd_verify_coverage)

    # build-final
    pb = sub.add_parser("build-final", help="原 constraints.json + 批次 → 最终 constraints.json")
    pb.add_argument("--original", required=True, help="文档分析的 constraints.json（输出保持其格式）")
    pb.add_argument("--batch", required=True, action="append", help="翻译批次 JSON（可多次）")
    pb.add_argument("--product", default=None, help="并入的产品键（constraints_in_parameters 为 dict 时必填）")
    pb.add_argument("--output", required=True, help="最终 constraints.json 输出路径（与文档 constraints.json 同格式，OperatorRule 兼容）")
    pb.add_argument("--code-side-output", default=None, dest="code_side_output",
                    help="代码侧 sidecar 输出路径（UB/error/normalize/unreachable/stubs）；缺省为 <output>.code_side.json")
    pb.set_defaults(func=_cmd_build_final)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging(debug=args.debug)
    try:
        return args.func(args)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001 — CLI 顶层兜底，打印后非零退出
        logger.exception("子命令 %s 执行失败: %s", args.cmd, e)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
