from __future__ import annotations

from datetime import datetime, timezone

from binance.datasets.futures_um_trades.repair import _gap_to_utc_date_range
from binance.storage.ingest_meta import IngestGap


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_gap_to_utc_date_range_end_exclusive_midnight_is_same_day() -> None:
    gap = IngestGap(
        gap_id=1,
        exchange="binance",
        dataset="futures.um.trades",
        symbol="BTCUSDT",
        start_time=_ms(datetime(2026, 2, 12, 23, 59, 59, 999000, tzinfo=timezone.utc)),
        end_time=_ms(datetime(2026, 2, 13, 0, 0, 0, 0, tzinfo=timezone.utc)),
        reason="test",
        status="open",
        run_id=None,
    )
    start_date, end_date = _gap_to_utc_date_range(gap)
    assert start_date.isoformat() == "2026-02-12"
    assert end_date.isoformat() == "2026-02-12"


def test_gap_to_utc_date_range_cross_day() -> None:
    gap = IngestGap(
        gap_id=1,
        exchange="binance",
        dataset="futures.um.trades",
        symbol="BTCUSDT",
        start_time=_ms(datetime(2026, 2, 12, 23, 59, 59, 999000, tzinfo=timezone.utc)),
        end_time=_ms(datetime(2026, 2, 13, 0, 0, 0, 1000, tzinfo=timezone.utc)),
        reason="test",
        status="open",
        run_id=None,
    )
    start_date, end_date = _gap_to_utc_date_range(gap)
    assert start_date.isoformat() == "2026-02-12"
    assert end_date.isoformat() == "2026-02-13"
