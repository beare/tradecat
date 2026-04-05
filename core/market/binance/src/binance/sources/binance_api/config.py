"""LF 共享配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from binance.common.env import build_layout, load_env_defaults
from assets.common.contracts.db_contracts import market_schema as _market_schema


STACK_ROOT = Path(__file__).resolve().parents[4]
SERVICE_ROOT = STACK_ROOT / "var" / "lf"
LAYOUT = build_layout(STACK_ROOT)
PROJECT_ROOT = LAYOUT.project_root
load_env_defaults(LAYOUT.env_file)

_DATABASE_URL_PRESET = bool(os.environ.get("DATABASE_URL"))


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    """LF 服务配置。"""

    database_url: str = field(
        default_factory=lambda: (
            os.getenv("DATABASE_URL", "")
            if _DATABASE_URL_PRESET
            else (os.getenv("DATA_SERVICE_DATABASE_URL") or os.getenv("DATABASE_URL", ""))
        )
    )
    http_proxy: str | None = field(default_factory=lambda: os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY"))
    binance_fapi_base: str = field(
        default_factory=lambda: (
            os.getenv("DATA_SERVICE_BINANCE_FAPI_BASE") or os.getenv("BINANCE_FAPI_BASE") or ""
        ).rstrip("/")
    )
    binance_data_base: str = field(
        default_factory=lambda: (
            os.getenv("DATA_SERVICE_BINANCE_DATA_BASE") or os.getenv("BINANCE_DATA_BASE") or ""
        ).rstrip("/")
    )
    binance_alpha_url: str = field(
        default_factory=lambda: os.getenv("DATA_SERVICE_BINANCE_ALPHA_URL") or os.getenv("BINANCE_ALPHA_URL") or ""
    )
    log_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_SERVICE_LOG_DIR", str(SERVICE_ROOT / "logs"))))
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("DATA_SERVICE_DATA_DIR", str(PROJECT_ROOT / "assets" / "database" / "csv"))
        )
    )
    ws_gap_interval: int = field(default_factory=lambda: _int_env("BINANCE_WS_GAP_INTERVAL", 600))
    ws_gap_lookback: int = field(default_factory=lambda: _int_env("BINANCE_WS_GAP_LOOKBACK", 10080))
    ws_source: str = field(default_factory=lambda: os.getenv("BINANCE_WS_SOURCE", "binance_ws"))
    db_schema: str = field(default_factory=lambda: (os.getenv("KLINE_DB_SCHEMA") or _market_schema()).strip())
    db_exchange: str = field(default_factory=lambda: os.getenv("BINANCE_WS_DB_EXCHANGE", "binance_futures_um"))
    ccxt_exchange: str = field(default_factory=lambda: os.getenv("BINANCE_WS_CCXT_EXCHANGE", "binance"))

    def __post_init__(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()


INTERVAL_TO_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
    "1M": 2_592_000_000,
}


@dataclass(slots=True)
class GapTask:
    """缺口任务。"""

    symbol: str
    gap_start: datetime
    gap_end: datetime


def normalize_interval(interval: str) -> str:
    interval = interval.strip()
    if interval == "1M":
        return "1M"
    normalized = interval.lower()
    if normalized not in INTERVAL_TO_MS:
        raise ValueError(f"不支持的周期: {interval}")
    return normalized
