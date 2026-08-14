"""Pytest 公共配置：把项目根加入 sys.path，支持 agent/、scripts/、executer/ 包导入。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
