from datetime import date

import pytest

from assets.common.contracts.db_contracts import (
    alternative_table,
    governance_lineage_events_table,
    market_candles_table,
    market_futures_metrics_table,
)
from binance.sources.binance_api.backfill_shared import GapScanner


def test_market_contracts_ignore_object_naming_env_for_central_surfaces(monkeypatch) -> None:
    monkeypatch.setenv("DB_SCHEMA_MARKET", "market")
    monkeypatch.setenv("DB_TABLE_MARKET_CANDLES_1M", "binance_candles_1m")
    monkeypatch.setenv("DB_TABLE_MARKET_FUTURES_METRICS_5M", "binance_futures_metrics_5m")
    monkeypatch.setenv("DB_TABLE_MARKET_FUTURES_UM_CANDLES_1M", "custom_um_candles_1m")
    monkeypatch.setenv("DB_TABLE_MARKET_FUTURES_UM_METRICS_SNAPSHOT_5M", "custom_um_metrics_snapshot_5m")

    assert market_candles_table("1m") == "market.binance_futures_um_candles_1m"
    assert market_futures_metrics_table("5m") == "market.binance_futures_um_metrics_snapshot_5m"


def test_market_contracts_fail_fast_when_central_relation_missing(monkeypatch) -> None:
    from assets.common.contracts import db_contracts as dbc

    monkeypatch.setattr(dbc, "get_contract_physical", lambda _rid: None)

    with pytest.raises(RuntimeError, match="missing_contract_relation:facts/market/futures_um_candles_1m"):
        market_candles_table("1m")
    with pytest.raises(RuntimeError, match="missing_contract_relation:facts/market/futures_um_metrics_snapshot_5m"):
        market_futures_metrics_table("5m")


def test_gap_scanner_uses_market_contract_surface_for_1m(monkeypatch) -> None:
    executed: list[str] = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            executed.append(sql)

        def fetchall(self):
            return []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    class _Ts:
        schema = "market"

        def connection(self):
            return _Conn()

    GapScanner(_Ts()).scan_klines(["BTCUSDT"], date(2026, 4, 1), date(2026, 4, 1))

    assert executed
    assert "market.binance_futures_um_candles_1m" in executed[0]
    assert "market.binance_candles_1m" not in executed[0]


def test_alternative_contracts_prefer_central_resources(monkeypatch) -> None:
    monkeypatch.setenv("DB_SCHEMA_ALTERNATIVE", "alternative")
    monkeypatch.setenv("DB_TABLE_ALT_HYPERLIQUID_ADDRESS_SNAPSHOTS", "legacy_hyperliquid_address_snapshots")
    monkeypatch.setenv("DB_TABLE_ALT_INVESTING_CALENDAR_SNAPSHOTS", "legacy_investing_calendar_snapshots")

    assert alternative_table("hyperliquid_address_snapshots") == "alternative.hyperliquid_address_snapshots"
    assert alternative_table("investing_calendar_snapshots") == "alternative.investing_calendar_snapshots"


def test_governance_lineage_contracts_prefer_central_resources(monkeypatch) -> None:
    monkeypatch.setenv("DB_SCHEMA_GOVERNANCE", "governance")
    monkeypatch.setenv("DB_TABLE_GOVERNANCE_LINEAGE_EVENTS", "legacy_lineage_events")

    assert governance_lineage_events_table() == "governance.lineage_events"
