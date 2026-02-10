"""配置管理"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SERVICE_ROOT = Path(__file__).parent.parent
PROJECT_ROOT = SERVICE_ROOT.parent.parent

# 加载 config/.env
_env_file = PROJECT_ROOT / "config" / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

def _apply_proxy_defaults() -> None:
    default_proxy = os.getenv("MARKETS_SERVICE_DEFAULT_PROXY") or os.getenv("DEFAULT_PROXY_URL")
    if not default_proxy:
        return
    os.environ.setdefault("http_proxy", default_proxy)
    os.environ.setdefault("https_proxy", default_proxy)
    os.environ.setdefault("HTTP_PROXY", default_proxy)
    os.environ.setdefault("HTTPS_PROXY", default_proxy)


_apply_proxy_defaults()


@dataclass
class Settings:
    """服务配置"""
    # 数据库
    database_url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL", ""
    ))
    db_schema: str = field(default_factory=lambda: os.getenv("MARKET_DB_SCHEMA", "market_data"))
    raw_schema: str = field(default_factory=lambda: os.getenv("RAW_DB_SCHEMA", "raw"))
    quality_schema: str = field(default_factory=lambda: os.getenv("QUALITY_DB_SCHEMA", "quality"))

    # 代理
    http_proxy: Optional[str] = field(default_factory=lambda: os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY"))
    binance_fapi_base: str = field(default_factory=lambda: (
        os.getenv("MARKETS_SERVICE_BINANCE_FAPI_BASE")
        or os.getenv("BINANCE_FAPI_BASE")
        or ""
    ).rstrip("/"))
    binance_data_base: str = field(default_factory=lambda: (
        os.getenv("MARKETS_SERVICE_BINANCE_DATA_BASE")
        or os.getenv("BINANCE_DATA_BASE")
        or ""
    ).rstrip("/"))

    # 目录
    log_dir: Path = field(default_factory=lambda: SERVICE_ROOT / "logs")

    def __post_init__(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
