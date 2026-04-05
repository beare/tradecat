from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from binance.sources.binance_api import backfill_shared as backfill_mod
from binance.storage.ingest_meta import IngestGap


class _ScanRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date, date]] = []

    def scan_klines(self, symbols, start: date, end: date, interval: str, threshold: float):
        assert symbols == ["BTCUSDT"]
        assert interval == "1m"
        assert threshold == 0.95
        self.calls.append(("klines", start, end))
        return {}

    def scan_metrics(self, symbols, start: date, end: date, threshold: float):
        assert symbols == ["BTCUSDT"]
        assert threshold == 0.95
        self.calls.append(("metrics", start, end))
        return {}


def test_data_backfiller_uses_utc_previous_day_cutoff(monkeypatch):
    recorder = _ScanRecorder()
    monkeypatch.setattr(backfill_mod, "utc_today", lambda: date(2026, 4, 4))

    backfiller = backfill_mod.DataBackfiller.__new__(backfill_mod.DataBackfiller)
    backfiller.lookback_days = 30
    backfiller.workers = 8
    backfiller.threshold = 0.95
    backfiller._scanner = recorder
    backfiller._zip = object()
    backfiller._rest = object()
    backfiller._ts = object()

    klines = backfill_mod.DataBackfiller.run_klines(backfiller, symbols=["BTCUSDT"])
    metrics = backfill_mod.DataBackfiller.run_metrics(backfiller, symbols=["BTCUSDT"])

    assert klines == {"scanned": 1, "gaps": 0, "filled": 0}
    assert metrics == {"scanned": 1, "gaps": 0, "filled": 0}
    assert recorder.calls == [
        ("klines", date(2026, 3, 4), date(2026, 4, 3)),
        ("metrics", date(2026, 3, 4), date(2026, 4, 3)),
    ]


def test_compute_lookback_uses_utc_today(monkeypatch):
    monkeypatch.setattr(backfill_mod, "utc_today", lambda: date(2026, 4, 4))

    assert backfill_mod.compute_lookback("all", 30, date(2026, 4, 1)) == 3


class _FakeGapWriter:
    def __init__(self, existing: dict[tuple[str, int, int], IngestGap] | None = None) -> None:
        self.existing = existing or {}
        self.inserted: list[tuple[str, int, int, str]] = []
        self.reopened: list[int] = []
        self.ignored: list[int] = []
        self.closed: list[int] = []

    def get_gap(self, *, exchange: str, dataset: str, symbol: str, start_time: int, end_time: int):
        return self.existing.get((symbol, start_time, end_time))

    def insert_gap(
        self,
        *,
        exchange: str,
        dataset: str,
        symbol: str,
        start_time: int,
        end_time: int,
        reason: str,
        run_id: int | None,
    ) -> None:
        self.inserted.append((symbol, start_time, end_time, reason))

    def reopen_gap(self, gap_id: int, *, reason: str | None = None) -> None:
        self.reopened.append(gap_id)

    def ignore_gap(self, gap_id: int, *, reason: str | None = None) -> None:
        self.ignored.append(gap_id)

    def close_gap(self, gap_id: int) -> None:
        self.closed.append(gap_id)


def test_gap_governance_skips_ignored_gap_within_cooldown() -> None:
    gap = backfill_mod.GapInfo("BTCUSDT", date(2026, 4, 1), 1440, 1200)
    start_ms, end_ms = backfill_mod._gap_window_ms(gap)
    writer = _FakeGapWriter(
        {
            ("BTCUSDT", start_ms, end_ms): IngestGap(
                gap_id=7,
                exchange="binance",
                dataset="futures.um.candles_1m",
                symbol="BTCUSDT",
                start_time=start_ms,
                end_time=end_ms,
                reason="cooldown",
                status="ignored",
                run_id=None,
                detected_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        }
    )
    governance = backfill_mod.GapGovernance(object(), "futures.um.candles_1m", cooldown_seconds=3600, writer_factory=lambda: writer)

    filtered, summary = governance.filter_attemptable_gaps(
        {"BTCUSDT": [gap]},
        reason="lf_backfill_detected window=2026-03-31..2026-04-02",
    )

    assert filtered == {}
    assert summary.skipped_ignored == 1
    assert writer.reopened == []


def test_gap_governance_reopens_after_cooldown_and_ignores_unfillable() -> None:
    gap = backfill_mod.GapInfo("BTCUSDT", date(2026, 4, 1), 1440, 1200)
    start_ms, end_ms = backfill_mod._gap_window_ms(gap)
    writer = _FakeGapWriter(
        {
            ("BTCUSDT", start_ms, end_ms): IngestGap(
                gap_id=9,
                exchange="binance",
                dataset="futures.um.candles_1m",
                symbol="BTCUSDT",
                start_time=start_ms,
                end_time=end_ms,
                reason="old_ignore",
                status="ignored",
                run_id=None,
                detected_at=datetime.now(UTC) - timedelta(hours=2),
            )
        }
    )
    governance = backfill_mod.GapGovernance(object(), "futures.um.candles_1m", cooldown_seconds=3600, writer_factory=lambda: writer)

    filtered, summary = governance.filter_attemptable_gaps(
        {"BTCUSDT": [gap]},
        reason="lf_backfill_detected window=2026-03-31..2026-04-02",
    )
    close_summary = governance.finalize_attempt(
        filtered,
        {"BTCUSDT": [gap]},
        ignore_reason="lf_backfill_unfillable window=2026-03-31..2026-04-02",
    )

    assert list(filtered) == ["BTCUSDT"]
    assert summary.reopened == 1
    assert writer.reopened == [9]
    assert close_summary.ignored == 1
    assert writer.ignored == [9]
