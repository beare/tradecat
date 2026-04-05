"""
Signal Service 配置
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for path in [current] + list(current.parents):
        has_assets = (path / "assets").is_dir()
        has_env_example = (path / "assets" / "config" / ".env.example").exists() or (path / "config" / ".env.example").exists()
        has_layout = (path / "core").is_dir() or (path / "plugins").is_dir()
        if has_assets and has_env_example and has_layout:
            return path
    return start.resolve().parents[2]


# 路径
SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
REPO_ROOT = _find_repo_root(PROJECT_ROOT)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from assets.common.contracts.db_contracts import (  # noqa: E402
    market_candles_table as _market_candles_table,
    market_futures_metrics_table as _market_futures_metrics_table,
    signal_schema as _signal_schema,
    signal_table_name as _signal_table_name,
)


# 数据库配置
def _read_env_file_value(target_key: str) -> str | None:
    env_file = REPO_ROOT / "assets" / "config" / ".env"
    if not env_file.exists():
        env_file = REPO_ROOT / "config" / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == target_key:
            return v.strip().strip("\"'")
    return None


def _replace_database_name(dsn: str, db_name: str) -> str:
    raw = (dsn or "").strip()
    if not raw or "://" not in raw:
        return ""
    try:
        parsed = urlsplit(raw)
        return urlunsplit((parsed.scheme, parsed.netloc, f"/{db_name}", parsed.query, parsed.fragment))
    except Exception:
        return ""


def get_facts_database_url() -> str:
    """获取 facts_data 连接 URL"""
    url = os.environ.get("FACTS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        return url
    return _read_env_file_value("FACTS_DATABASE_URL") or _read_env_file_value("DATABASE_URL") or "postgresql://postgres:postgres@localhost:5433/facts_data"


def get_derived_database_url() -> str:
    """获取 derived_data 连接 URL"""
    url = os.environ.get("DERIVED_DATABASE_URL")
    if url:
        return url
    return (
        _read_env_file_value("DERIVED_DATABASE_URL")
        or _replace_database_name(os.environ.get("DATABASE_URL", ""), "derived_data")
        or _replace_database_name(_read_env_file_value("DATABASE_URL") or "", "derived_data")
        or "postgresql://postgres:postgres@localhost:5433/derived_data"
    )


def get_database_url() -> str:
    """兼容旧代码：默认返回 facts_data 连接 URL"""
    return get_facts_database_url()


# 信号检测配置
DEFAULT_TIMEFRAMES = ["1h", "4h", "1d"]
DEFAULT_MIN_VOLUME = 100000
DEFAULT_CHECK_INTERVAL = 60  # 秒
COOLDOWN_SECONDS = 300  # 同一信号冷却时间
# 数据新鲜度阈值（秒），超过则视为陈旧数据不参与信号计算
DATA_MAX_AGE_SECONDS = int(os.environ.get("SIGNAL_DATA_MAX_AGE", "600"))

# 历史记录配置
MAX_RETENTION_DAYS = int(os.environ.get("SIGNAL_HISTORY_RETENTION_DAYS", "30"))


def get_market_candles_table(interval: str) -> str:
    return _market_candles_table(interval)


def get_market_futures_metrics_table(interval: str) -> str:
    return _market_futures_metrics_table(interval)


def get_signal_schema_name() -> str:
    return _signal_schema()


def get_signal_table_name(key: str) -> str:
    return _signal_table_name(key)
