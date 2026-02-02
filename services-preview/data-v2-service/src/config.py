"""配置管理（从项目根目录 config/.env 读取，优先环境变量）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SERVICE_ROOT = Path(__file__).parent.parent  # src/ -> data-v2-service/
PROJECT_ROOT = SERVICE_ROOT.parent.parent    # tradecat/

_env_file = PROJECT_ROOT / "config" / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


@dataclass
class Settings:
    """服务配置"""

    http_proxy: Optional[str] = field(default_factory=lambda: os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY"))
    exchange: str = field(default_factory=lambda: os.getenv("DATA_V2_EXCHANGE", "binanceusdm"))
    symbol: str = field(default_factory=lambda: os.getenv("DATA_V2_SYMBOL", "BTC/USDT:USDT"))

    def proxies(self) -> dict[str, str] | None:
        if not self.http_proxy:
            return None
        return {"http": self.http_proxy, "https": self.http_proxy}


settings = Settings()

