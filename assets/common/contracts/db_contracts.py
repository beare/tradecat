from __future__ import annotations

"""数据库对象命名契约读取器。

说明：
- `assets/contracts/resources.v1.yaml` 是中央对象命名真相源。
- 本模块只负责把中央契约投影成运行时代码可调用的 helper。
- `.env` 只保留连接、开关与未迁入中央契约的少量动态对象名。
"""

import os
from pathlib import Path

from assets.common.contracts.semantic_contracts import get_contract_physical


def _env_str(key: str, default: str) -> str:
    value = (os.getenv(key) or "").strip()
    return value or default


def _contract_relation(resource_id: str) -> tuple[str, str] | None:
    physical = get_contract_physical(resource_id)
    if not isinstance(physical, dict):
        return None
    kind = str(physical.get("kind") or "").strip()
    if kind not in {"postgres_table", "postgres_view"}:
        return None
    schema = str(physical.get("schema") or "").strip()
    object_name = str(physical.get("object_name") or "").strip()
    if not schema or not object_name:
        return None
    return schema, object_name


def _require_contract_relation(resource_id: str) -> tuple[str, str]:
    relation = _contract_relation(resource_id)
    if relation is None:
        raise RuntimeError(f"missing_contract_relation:{resource_id}")
    return relation


def _contract_qualified_name(resource_id: str) -> str:
    schema, object_name = _require_contract_relation(resource_id)
    return qualified_name(schema, object_name)


def qualified_name(schema: str, object_name: str) -> str:
    return f"{schema}.{object_name}"


def facts_db_name() -> str:
    return _env_str("DB_NAME_FACTS", "facts_data")


def derived_db_name() -> str:
    return _env_str("DB_NAME_DERIVED", "derived_data")


def market_schema() -> str:
    return _env_str("DB_SCHEMA_MARKET", "market")


def alternative_schema() -> str:
    return _env_str("DB_SCHEMA_ALTERNATIVE", "alternative")


def indicator_schema() -> str:
    legacy = (os.getenv("INDICATOR_PG_SCHEMA") or "").strip()
    return _env_str("DB_SCHEMA_INDICATOR", legacy or "indicator_snapshots")


def signal_schema() -> str:
    legacy = (os.getenv("SIGNAL_PG_SCHEMA") or "").strip()
    return _env_str("DB_SCHEMA_SIGNAL", legacy or "signal_runtime")


def governance_schema() -> str:
    return _env_str("DB_SCHEMA_GOVERNANCE", "governance")


def governance_lineage_events_table_name() -> str:
    return _require_contract_relation("facts/governance/lineage_events")[1]


def governance_lineage_events_table() -> str:
    return _contract_qualified_name("facts/governance/lineage_events")


_CANDLE_TABLE_ENV_BY_INTERVAL: dict[str, str] = {
    "3m": "DB_TABLE_MARKET_CANDLES_3M",
    "5m": "DB_TABLE_MARKET_CANDLES_5M",
    "15m": "DB_TABLE_MARKET_CANDLES_15M",
    "30m": "DB_TABLE_MARKET_CANDLES_30M",
    "1h": "DB_TABLE_MARKET_CANDLES_1H",
    "2h": "DB_TABLE_MARKET_CANDLES_2H",
    "4h": "DB_TABLE_MARKET_CANDLES_4H",
    "6h": "DB_TABLE_MARKET_CANDLES_6H",
    "12h": "DB_TABLE_MARKET_CANDLES_12H",
    "1d": "DB_TABLE_MARKET_CANDLES_1D",
    "1w": "DB_TABLE_MARKET_CANDLES_1W",
}

_FUTURES_METRICS_TABLE_ENV_BY_INTERVAL: dict[str, str] = {
    "15m": "DB_TABLE_MARKET_FUTURES_METRICS_15M_LAST",
    "1h": "DB_TABLE_MARKET_FUTURES_METRICS_1H_LAST",
    "4h": "DB_TABLE_MARKET_FUTURES_METRICS_4H_LAST",
    "1d": "DB_TABLE_MARKET_FUTURES_METRICS_1D_LAST",
    "1w": "DB_TABLE_MARKET_FUTURES_METRICS_1W_LAST",
}

_ALTERNATIVE_RESOURCE_ID_BY_KEY: dict[str, str] = {
    "news": "facts/alternative/news",
    "telegram": "facts/alternative/telegram",
    "discord": "facts/alternative/discord",
    "x": "facts/alternative/x",
    "investing_calendar_snapshots": "facts/alternative/investing_calendar_snapshots",
    "hyperliquid_address_snapshots": "facts/alternative/hyperliquid_address_snapshots",
}

_SIGNAL_RESOURCE_ID_BY_KEY: dict[str, str] = {
    "cooldown": "derived/signal_runtime/cooldown",
    "history": "derived/signal_runtime/signal_history",
    "subs": "derived/signal_runtime/signal_subs",
}


def market_candles_table_name(interval: str) -> str:
    normalized = (interval or "").strip().lower()
    if normalized == "1m":
        return _require_contract_relation("facts/market/futures_um_candles_1m")[1]
    env_key = _CANDLE_TABLE_ENV_BY_INTERVAL.get(normalized)
    if not env_key:
        raise ValueError(f"unsupported_candles_interval:{interval}")
    return _env_str(env_key, f"binance_candles_{normalized}")


def market_candles_table(interval: str) -> str:
    normalized = (interval or "").strip().lower()
    if normalized == "1m":
        return _contract_qualified_name("facts/market/futures_um_candles_1m")
    return qualified_name(market_schema(), market_candles_table_name(interval))


def market_futures_um_candles_1m_table_name() -> str:
    return _require_contract_relation("facts/market/futures_um_candles_1m")[1]


def market_futures_um_candles_1m_table() -> str:
    return _contract_qualified_name("facts/market/futures_um_candles_1m")


def market_spot_candles_1m_table_name() -> str:
    return _require_contract_relation("facts/market/spot_candles_1m")[1]


def market_spot_candles_1m_table() -> str:
    return _contract_qualified_name("facts/market/spot_candles_1m")


def market_futures_metrics_table_name(interval: str) -> str:
    normalized = (interval or "").strip().lower()
    if normalized == "5m":
        return _require_contract_relation("facts/market/futures_um_metrics_snapshot_5m")[1]
    env_key = _FUTURES_METRICS_TABLE_ENV_BY_INTERVAL.get(normalized)
    if not env_key:
        raise ValueError(f"unsupported_metrics_interval:{interval}")
    default = f"binance_futures_metrics_{normalized}_last"
    return _env_str(env_key, default)


def market_futures_metrics_table(interval: str) -> str:
    normalized = (interval or "").strip().lower()
    if normalized == "5m":
        return _contract_qualified_name("facts/market/futures_um_metrics_snapshot_5m")
    return qualified_name(market_schema(), market_futures_metrics_table_name(interval))


def market_futures_um_metrics_snapshot_5m_table_name() -> str:
    return _require_contract_relation("facts/market/futures_um_metrics_snapshot_5m")[1]


def market_futures_um_metrics_snapshot_5m_table() -> str:
    return _contract_qualified_name("facts/market/futures_um_metrics_snapshot_5m")


def market_ingest_offsets_table() -> str:
    return _contract_qualified_name("facts/market/binance_ingest_offsets")


def market_ingest_runs_table() -> str:
    return _contract_qualified_name("facts/market/binance_ingest_runs")


def market_ingest_watermark_table() -> str:
    return _contract_qualified_name("facts/market/binance_ingest_watermark")


def market_ingest_gaps_table() -> str:
    return _contract_qualified_name("facts/market/binance_ingest_gaps")


def market_missing_intervals_table() -> str:
    return _contract_qualified_name("facts/market/binance_missing_intervals")


def market_shared_files_table() -> str:
    return _contract_qualified_name("facts/market/shared_files")


def market_shared_file_revisions_table() -> str:
    return _contract_qualified_name("facts/market/shared_file_revisions")


def market_shared_import_batches_table() -> str:
    return _contract_qualified_name("facts/market/shared_import_batches")


def market_shared_import_errors_table() -> str:
    return _contract_qualified_name("facts/market/shared_import_errors")


def market_shared_instrument_table() -> str:
    return _contract_qualified_name("facts/market/shared_instrument")


def market_shared_symbol_map_table() -> str:
    return _contract_qualified_name("facts/market/shared_symbol_map")


def market_shared_venue_table() -> str:
    return _contract_qualified_name("facts/market/shared_venue")


def market_futures_um_trades_table() -> str:
    return _contract_qualified_name("facts/market/futures_um_trades")


def market_futures_um_book_ticker_table() -> str:
    return _contract_qualified_name("facts/market/futures_um_book_ticker")


def market_futures_um_book_depth_table() -> str:
    return _contract_qualified_name("facts/market/futures_um_book_depth")


def market_futures_um_metrics_atomic_table() -> str:
    return qualified_name(
        market_schema(),
        _env_str("DB_TABLE_MARKET_FUTURES_UM_METRICS_ATOMIC", "binance_futures_um_metrics_atomic"),
    )


def market_spot_trades_table() -> str:
    return _contract_qualified_name("facts/market/spot_trades")


def alternative_table_name(key: str) -> str:
    resource_id = _ALTERNATIVE_RESOURCE_ID_BY_KEY.get(key)
    if not resource_id:
        raise ValueError(f"unsupported_alternative_key:{key}")
    return _require_contract_relation(resource_id)[1]


def alternative_table(key: str) -> str:
    resource_id = _ALTERNATIVE_RESOURCE_ID_BY_KEY.get(key)
    if not resource_id:
        raise ValueError(f"unsupported_alternative_key:{key}")
    return _contract_qualified_name(resource_id)


def alternative_unified_events_view_name() -> str:
    return _require_contract_relation("facts/alternative/unified_events_view")[1]


def alternative_unified_events_view() -> str:
    return _contract_qualified_name("facts/alternative/unified_events_view")


def indicator_base_table_name() -> str:
    return _env_str("DB_TABLE_INDICATOR_BASE", "基础数据同步器.py")


def indicator_base_table() -> str:
    return qualified_name(indicator_schema(), indicator_base_table_name())

def indicator_pg_layout() -> str:
    """PG 指标表布局开关。

    取值：
    - legacy：历史布局（每指标一表）
    - dual：双写（legacy 表 + snapshots 表）
    - snapshot：读 snapshot 表（仍建议保留 legacy 双写用于回滚/对照）
    """
    raw = _env_str("INDICATOR_PG_LAYOUT", "legacy").strip().lower()
    if raw in {"legacy", "dual", "snapshot"}:
        return raw
    # 容错：避免拼写错误导致行为分裂
    return "legacy"


def indicator_snapshots_table_name() -> str:
    """统一快照表名（单表+jsonb payload）。"""
    return _require_contract_relation("derived/indicator_snapshots/snapshots")[1]


def indicator_snapshots_table() -> str:
    return _contract_qualified_name("derived/indicator_snapshots/snapshots")


def signal_table_name(key: str) -> str:
    resource_id = _SIGNAL_RESOURCE_ID_BY_KEY.get(key)
    if not resource_id:
        raise ValueError(f"unsupported_signal_key:{key}")
    return _require_contract_relation(resource_id)[1]


def signal_table(key: str) -> str:
    resource_id = _SIGNAL_RESOURCE_ID_BY_KEY.get(key)
    if not resource_id:
        raise ValueError(f"unsupported_signal_key:{key}")
    return _contract_qualified_name(resource_id)


def x_group_state_relative_path() -> str:
    return _env_str("FILE_X_GROUP_STATE", "core/alternative/x/state/group_state.json")


def resolve_repo_path(repo_root: Path, env_key: str, default: str) -> Path:
    raw = _env_str(env_key, default)
    path = Path(raw)
    return path if path.is_absolute() else (repo_root / path)
