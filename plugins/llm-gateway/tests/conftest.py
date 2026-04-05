"""pytest 配置：确保本服务根目录在 sys.path 内（兼容 pytest import-mode=importlib）。"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

