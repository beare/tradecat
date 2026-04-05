"""
配置管理

环境变量:
    FACTS_DATABASE_URL: facts_data 连接串（推荐）
    DERIVED_DATABASE_URL: derived_data 连接串（推荐）
    DATABASE_URL: facts_data 兼容连接串（旧名）
    INDICATOR_DATABASE_URL: derived_data 兼容连接串（旧名）
    INDICATOR_PG_SCHEMA: PG 指标 schema（默认 indicator_snapshots）
    MAX_WORKERS: 并行计算线程数
    KLINE_INTERVALS: K线指标计算周期
    FUTURES_INTERVALS: 期货情绪计算周期
"""
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List
from urllib.parse import urlsplit, urlunsplit


def _find_project_root(start: Path) -> Path:
    current = start.resolve()
    for path in [current] + list(current.parents):
        if not (path / "assets").is_dir():
            continue
        has_env_example = (path / "assets" / "config" / ".env.example").exists() or (path / "config" / ".env.example").exists()
        has_layout = (path / "core").is_dir() or (path / "plugins").is_dir()
        if has_env_example and has_layout:
            return path
    return current.parents[4]


def _resolve_env_file(project_root: Path) -> Path:
    candidates = [
        project_root / "assets" / "config" / ".env",
        project_root / "config" / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


SERVICE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = _find_project_root(Path(__file__))
ENV_FILE = _resolve_env_file(PROJECT_ROOT)
LOG_DIR = SERVICE_ROOT / "logs"
DATA_DIR = SERVICE_ROOT / "data"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from assets.common.contracts.db_contracts import (  # noqa: E402
    indicator_pg_layout as _indicator_pg_layout,
    indicator_schema as _indicator_schema,
    indicator_snapshots_table_name as _indicator_snapshots_table_name,
    market_candles_table as _market_candles_table,
    market_futures_metrics_table as _market_futures_metrics_table,
    market_ingest_offsets_table as _market_ingest_offsets_table,
)


@lru_cache(maxsize=1)
def load_shared_env() -> Path:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    return ENV_FILE


load_shared_env()


def _parse_intervals(env_key: str, default: str) -> List[str]:
    return [x.strip() for x in os.getenv(env_key, default).split(",") if x.strip()]


def _replace_database_name(dsn: str, db_name: str) -> str:
    raw = (dsn or "").strip()
    if not raw or "://" not in raw:
        return ""
    try:
        parsed = urlsplit(raw)
        return urlunsplit((parsed.scheme, parsed.netloc, f"/{db_name}", parsed.query, parsed.fragment))
    except Exception:
        return ""


@dataclass
class Config:
    # facts_data（读取 K 线 / 行情事实）
    facts_db_url: str = field(
        default_factory=lambda: (
            os.getenv("FACTS_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or "postgresql://postgres:postgres@localhost:5433/facts_data"
        ).strip()
    )

    # derived_data（写入 indicator_snapshots / signal_runtime）
    derived_db_url: str = field(
        default_factory=lambda: (
            os.getenv("DERIVED_DATABASE_URL")
            or os.getenv("INDICATOR_DATABASE_URL")
            or _replace_database_name(os.getenv("DATABASE_URL", ""), "derived_data")
            or "postgresql://postgres:postgres@localhost:5433/derived_data"
        ).strip()
    )

    # PG 指标 schema（表名严格对齐历史指标表名）
    indicator_pg_schema: str = field(
        default_factory=lambda: _indicator_schema()
    )

    # PG 指标表布局（indicator_snapshots 治理开关）
    indicator_pg_layout: str = field(
        default_factory=lambda: _indicator_pg_layout()
    )

    # 统一快照表（单表+jsonb payload）
    indicator_snapshots_table_name: str = field(
        default_factory=lambda: _indicator_snapshots_table_name()
    )

    # 计算参数
    default_lookback: int = 300
    max_workers: int = field(default_factory=lambda: int(os.getenv("MAX_WORKERS", "6")))
    exchange: str = "binance_futures_um"
    # 计算后端: thread | process | hybrid（IO用线程，CPU用进程）
    compute_backend: str = field(default_factory=lambda: os.getenv("COMPUTE_BACKEND", "thread").lower())

    # IO/CPU 拆分执行器配置
    max_io_workers: int = field(default_factory=lambda: int(os.getenv("MAX_IO_WORKERS", "8")))
    max_cpu_workers: int = field(default_factory=lambda: int(os.getenv("MAX_CPU_WORKERS", "4")))

    # K线指标周期
    kline_intervals: List[str] = field(default_factory=lambda: _parse_intervals(
        "KLINE_INTERVALS", "1m,5m,15m,1h,4h,1d,1w"
    ))

    # 期货情绪周期
    futures_intervals: List[str] = field(default_factory=lambda: _parse_intervals(
        "FUTURES_INTERVALS", "5m,15m,1h,4h,1d,1w"
    ))

    # 兼容旧代码
    @property
    def db_url(self) -> str:
        return self.facts_db_url

    @property
    def intervals(self) -> List[str]:
        return self.kline_intervals


config = Config()


def get_market_candles_table(interval: str) -> str:
    return _market_candles_table(interval)


def get_market_futures_metrics_table(interval: str) -> str:
    return _market_futures_metrics_table(interval)


def get_market_ingest_offsets_table() -> str:
    return _market_ingest_offsets_table()
