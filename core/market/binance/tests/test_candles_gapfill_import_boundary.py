from __future__ import annotations

from datetime import UTC, date, datetime

from binance.datasets.futures_um_candles_1m import backfill as backfill_mod
from binance.sources.binance_api import backfill_shared as shared_mod
from binance.datasets.futures_um_candles_1m.collect import WSCollector


class _FakeGap:
    def __init__(self, day: date) -> None:
        self.date = day


class _FakeScanner:
    def __init__(self, _ts: object) -> None:
        self.calls = 0

    def scan_klines(self, symbols: list[str], start: date, end: date, interval: str, threshold: float):
        assert symbols == ["BTCUSDT"]
        assert start <= end
        assert interval == "1m"
        assert threshold == 0.95
        self.calls += 1
        if self.calls == 1:
            return {"BTCUSDT": [_FakeGap(date(2026, 4, 1))]}
        return {}


class _FakeZipBackfiller:
    def __init__(self, _ts: object, workers: int) -> None:
        assert workers == 2

    def cleanup_old_files(self) -> None:
        return None

    def fill_kline_gaps(self, gaps: dict[str, list[_FakeGap]], interval: str) -> int:
        assert interval == "1m"
        assert list(gaps) == ["BTCUSDT"]
        return 3


class _FakeRestBackfiller:
    def __init__(self, _ts: object, workers: int, source_tag: str) -> None:
        raise AssertionError("本用例不应走 REST 回补")


class _FakeGovernance:
    def __init__(self, _ts: object, _dataset: str) -> None:
        pass

    def filter_attemptable_gaps(self, gaps, *, reason: str):
        assert "window=2026-03-31..2026-04-02" in reason
        return gaps, _Summary()

    def finalize_attempt(self, attempted, remaining, *, ignore_reason: str):
        assert "window=2026-03-31..2026-04-02" in ignore_reason
        return _Summary()


class _Summary:
    def to_dict(self) -> dict[str, int]:
        return {}


def test_ws_smart_backfill_uses_dataset_backfill_contract(monkeypatch):
    monkeypatch.setattr(backfill_mod, "GapScanner", _FakeScanner)
    monkeypatch.setattr(backfill_mod, "ZipBackfiller", _FakeZipBackfiller)
    monkeypatch.setattr(backfill_mod, "RestBackfiller", _FakeRestBackfiller)
    monkeypatch.setattr(shared_mod, "GapGovernance", _FakeGovernance)
    monkeypatch.setattr(shared_mod, "completed_utc_day_window", lambda lookback_days: (date(2026, 3, 31), date(2026, 4, 2)))

    collector = WSCollector.__new__(WSCollector)
    collector._ts = object()
    collector._symbols = {"BTC-USDT-PERP": "BTCUSDT"}
    collector._lineage_run_id = f"test_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"

    has_gaps, lookback_days = collector._smart_backfill(2)

    assert has_gaps is True
    assert lookback_days == 2
