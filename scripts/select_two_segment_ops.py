#!/usr/bin/env python3
"""从 9.0 源树每个子目录随机抽取 N 个「两段式 + 非量化 + 非融合」算子文档，
复制到 two-operators/。

判定（每文档）:
  is_op_doc   : 标题以 `# aclnn` 开头（排除 op_api_list.md / graph.md 等索引）
  two_segment : 正文含 "GetWorkspaceSize"（两段式接口标志；一段式算子无此串）
  has_quant   : 正文命中 量化|dequant|伪量化|fakequant|quant (i)
                （无确定性量化场景探测器；文档不出现量化字样 ⇒ 算子无量化概念。
                 保守：出现即排除。）
  has_fusion  : --fusion-mode classify（默认）= 项目 classify_operator 判通算融合
                (fusion_comm_compute)，即 pipeline 真正的"融合算子"判据；
                aclnnConfusionTranspose 文档虽写"融合reshape和transpose"（组合义）
                但非通算融合 → 判 default → 保留。
                --fusion-mode literal = 字面含 融合/\\bfusion\\b/\\bfused\\b（保守排除）
  qualifies   : is_op_doc and two_segment and not has_quant and not has_fusion

每子目录从 qualifies 集合随机抽 N（默认 10，seed=42 可复现），复制到 two-operators/。
产 two-operators/_manifest.json（每目录: 总数/qualify 数/排除原因/选中列表/seed）。

Usage:
    python scripts/select_two_segment_ops.py \\
        --src D:/project/operator-job/operators/9.0 \\
        --dest two-operators --per-folder 10 --seed 42 --fusion-mode classify
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import classify_operator  # noqa: E402  项目通算融合判别器

QUANT_RE = re.compile(r"量化|dequant|伪量化|fakequant|quant", re.IGNORECASE)
# literal 融合模式用（词边界避开 "Confusion" 子串；但 ConfusionTranspose 正文有"融合"组合义仍会被 literal 排除）
FUSION_LITERAL_RE = re.compile(r"融合|\bfusion\b|\bfused\b", re.IGNORECASE)
TITLE_RE = re.compile(r"^#\s*(aclnn[A-Za-z0-9_&]+)", re.MULTILINE)


def classify(doc_path: Path, doc_text: str, fusion_mode: str) -> dict:
    """返回该文档的判定结果。fusion_mode: classify | literal。"""
    title_m = TITLE_RE.search(doc_text)
    is_op_doc = title_m is not None
    two_segment = "GetWorkspaceSize" in doc_text
    has_amp = "&" in doc_path.name  # 成对文档(一文档双算子)按用户要求排除
    has_quant = bool(QUANT_RE.search(doc_text))
    if fusion_mode == "literal":
        has_fusion = bool(FUSION_LITERAL_RE.search(doc_text))
    else:  # classify（默认）— 项目通算融合判据
        has_fusion = classify_operator.classify(doc_path).get("operator_category") == "fusion_comm_compute"
    qualifies = (is_op_doc and two_segment and not has_amp
                 and not has_quant and not has_fusion)
    return {
        "title": title_m.group(1) if title_m else "",
        "is_op_doc": is_op_doc,
        "two_segment": two_segment,
        "has_amp": has_amp,
        "has_quant": has_quant,
        "has_fusion": has_fusion,
        "qualifies": qualifies,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--src", default="D:/project/operator-job/operators/9.0", help="9.0 源树根目录")
    p.add_argument("--dest", default="two-operators", help="输出目录（项目根下）")
    p.add_argument("--per-folder", type=int, default=10, help="每子目录抽取数")
    p.add_argument("--seed", type=int, default=42, help="随机种子（可复现）")
    p.add_argument("--fusion-mode", choices=("classify", "literal"), default="classify",
                   help="融合判据: classify=项目通算融合器(默认, ConfusionTranspose保留); literal=字面含融合即排除")
    p.add_argument("--folders", default="", help="只处理指定子目录（逗号分隔），默认全部")
    p.add_argument("--dry-run", action="store_true", help="只判定不复制")
    args = p.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    src = Path(args.src).resolve()
    dest = (ROOT / args.dest).resolve() if not Path(args.dest).is_absolute() else Path(args.dest).resolve()
    if not src.is_dir():
        print(json.dumps({"ok": False, "message": f"src 不存在: {src}"}, ensure_ascii=False))
        return 2

    folders = sorted(d.name for d in src.iterdir() if d.is_dir())
    if args.folders:
        want = {x.strip() for x in args.folders.split(",") if x.strip()}
        folders = [f for f in folders if f in want]

    rng = random.Random(args.seed)
    manifest = {
        "src": str(src), "dest": str(dest),
        "per_folder": args.per_folder, "seed": args.seed,
        "fusion_mode": args.fusion_mode, "folders": {}, "copied": [],
    }

    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        # 清掉旧产物 *.md（本脚本是幂等再生成器；_manifest.json 随后重写）
        for old in dest.glob("*.md"):
            try:
                old.unlink()
            except OSError:
                pass

    for folder in folders:
        fdir = src / folder
        results = []
        for md in sorted(fdir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            r = classify(md, text, args.fusion_mode)
            r["path"] = str(md); r["name"] = md.name
            results.append(r)

        qualifying = [r for r in results if r["qualifies"]]
        n_pick = min(args.per_folder, len(qualifying))
        picked = rng.sample(qualifying, n_pick) if n_pick else []

        excl = {"not_op_doc": 0, "not_two_segment": 0, "amp_in_name": 0,
                "quant": 0, "fusion": 0, "quant+fusion": 0, "other": 0}
        for r in results:
            if r["qualifies"]:
                continue
            if not r["is_op_doc"]:
                excl["not_op_doc"] += 1
            elif not r["two_segment"]:
                excl["not_two_segment"] += 1
            elif r["has_amp"]:
                excl["amp_in_name"] += 1
            elif r["has_quant"] and r["has_fusion"]:
                excl["quant+fusion"] += 1
            elif r["has_quant"]:
                excl["quant"] += 1
            elif r["has_fusion"]:
                excl["fusion"] += 1
            else:
                excl["other"] += 1

        manifest["folders"][folder] = {
            "total_md": len(results), "qualifying": len(qualifying),
            "picked": n_pick, "excluded_breakdown": excl,
            "shortfall": max(0, args.per_folder - len(qualifying)),
            "selected": [],
        }
        for r in picked:
            src_path = Path(r["path"])
            dest_path = dest / src_path.name
            if dest_path.exists() and src_path.name != dest_path.name:
                dest_path = dest / f"{folder}_{src_path.name}"
            manifest["folders"][folder]["selected"].append(
                {"name": src_path.name, "title": r["title"], "src": str(src_path)})
            if not args.dry_run:
                shutil.copy2(src_path, dest_path)
                manifest["copied"].append(str(dest_path))

        print(f"[{folder}] total={len(results)} qualifying={len(qualifying)} "
              f"picked={n_pick} shortfall={manifest['folders'][folder]['shortfall']} "
              f"excluded={excl}")

    if not args.dry_run:
        (dest / "_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nmanifest -> {dest / '_manifest.json'}")
        print(f"copied {len(manifest['copied'])} docs -> {dest}  (fusion_mode={args.fusion_mode})")
    else:
        print(f"\n[dry-run] 未复制。将抽 {sum(f['picked'] for f in manifest['folders'].values())} docs (fusion_mode={args.fusion_mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
