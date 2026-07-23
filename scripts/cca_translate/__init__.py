"""cca 行为划分 → 补充约束 翻译/对账/合并工具包。

仅依赖 Python 标准库。入口见 `cli.py`（或顶层 runner `scripts/cca_translate_cli.py`）。

子模块：
- cca_parse: 把 fn-*.md 的「## 行为划分」解析成分支树 IR
- reconcile: 文档约束 vs cca 分支 参数共现对账（捞缺口）
- merge_supplement: 合并多个补充批次
- build_final: 原 constraints.json + 批次 → 最终 constraints.json
- cli: 命令行子命令（locate/parse/reconcile/check/build-final）

日志：沿用本仓库惯例，每个模块 `logging.getLogger("cca_translate.<mod>")`，
由 cli 的 `_configure_logging` 统一装配 handler。
"""

__all__ = ["cca_parse", "reconcile", "merge_supplement", "build_final", "cli"]
