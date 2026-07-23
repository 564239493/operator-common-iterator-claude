#!/usr/bin/env python3
"""顶层 runner：让 `python scripts/cca_translate_cli.py <subcmd> ...` 直接可用。

把 `scripts/` 加入 sys.path，使 `cca_translate` 作为包被导入（包内相对导入生效）。
仅 Python 标准库。路径全部由子命令参数给出，无写死路径。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# scripts/ 目录
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cca_translate.cli import main  # noqa: E402  (sys.path 已就绪)

if __name__ == "__main__":
    raise SystemExit(main())
