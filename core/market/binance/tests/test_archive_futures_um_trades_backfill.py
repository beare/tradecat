from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import zipfile

import binance.datasets.futures_um_trades.backfill as um_trades_backfill
from binance.sources.archive_source.download_utils import DownloadResult, DownloadTextResult


def _write_zip_with_csv(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("sample.csv", content)


def test_build_plan_full_month_prefers_monthly() -> None:
    plan = um_trades_backfill._build_plan("BTCUSDT", date(2024, 1, 1), date(2024, 1, 31), prefer_monthly=True)
    assert len(plan) == 1
    assert plan[0].kind == "monthly"
    assert plan[0].period == "2024-01"


def test_build_plan_full_month_daily_when_monthly_disabled() -> None:
    plan = um_trades_backfill._build_plan("BTCUSDT", date(2024, 1, 1), date(2024, 1, 31), prefer_monthly=False)
    assert len(plan) == 31
    assert {item.kind for item in plan} == {"daily"}
    assert plan[0].period == "2024-01-01"
    assert plan[-1].period == "2024-01-31"


def test_build_plan_mixed_partial_and_full_month() -> None:
    plan = um_trades_backfill._build_plan("BTCUSDT", date(2024, 1, 15), date(2024, 3, 10), prefer_monthly=True)
    monthly = [p for p in plan if p.kind == "monthly"]
    daily = [p for p in plan if p.kind == "daily"]
    assert len(monthly) == 1
    assert monthly[0].period == "2024-02"
    assert len(daily) == 27
    assert daily[0].period == "2024-01-15"
    assert daily[-1].period == "2024-03-10"


def test_date_range_ms_utc_end_is_exclusive_next_day() -> None:
    start_ms, end_ms = um_trades_backfill._date_range_ms_utc(date(2024, 2, 1), date(2024, 2, 1))
    expected_start = int(datetime(2024, 2, 1, tzinfo=timezone.utc).timestamp() * 1000)
    expected_end = int(datetime(2024, 2, 2, tzinfo=timezone.utc).timestamp() * 1000)
    assert start_ms == expected_start
    assert end_ms == expected_end


def test_relpath_templates_align_archive_download_layout() -> None:
    assert um_trades_backfill._relpath_daily_zip("BTCUSDT", date(2024, 2, 9)) == "downloads/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2024-02-09.zip"
    assert um_trades_backfill._relpath_monthly_zip("BTCUSDT", "2024-02") == "downloads/futures/um/monthly/trades/BTCUSDT/BTCUSDT-trades-2024-02.zip"
    assert um_trades_backfill._archive_relpath_daily_zip("BTCUSDT", date(2024, 2, 9)) == "data/futures/um/daily/trades/BTCUSDT/BTCUSDT-trades-2024-02-09.zip"
    assert um_trades_backfill._archive_relpath_monthly_zip("BTCUSDT", "2024-02") == "data/futures/um/monthly/trades/BTCUSDT/BTCUSDT-trades-2024-02.zip"


def test_download_or_repair_keeps_existing_zip_when_remote_size_matches(tmp_path: Path, monkeypatch) -> None:
    dst = tmp_path / "BTCUSDT-trades-2026-02.zip"
    _write_zip_with_csv(dst, "id,price,qty,quote_qty,time,is_buyer_maker\n1,1.0,1.0,1.0,1,true\n")

    sha = um_trades_backfill.sha256_file(dst)
    checksum_text = f"{sha}  {dst.name}\n"
    monkeypatch.setattr(
        um_trades_backfill,
        "download_text",
        lambda *args, **kwargs: DownloadTextResult(ok=True, status_code=200, text=checksum_text, error=None),
    )

    def _should_not_download(*args, **kwargs) -> DownloadResult:
        raise AssertionError("remote size 一致时不应触发重下")

    monkeypatch.setattr(um_trades_backfill, "download_file", _should_not_download)

    result = um_trades_backfill._download_or_repair_zip("https://example.com/a.zip", dst, allow_no_checksum=False)
    assert result.ok is True
    assert um_trades_backfill._zip_has_csv(dst)
    assert result.verified is True
    assert result.checksum_sha256 == sha


def test_download_or_repair_redownloads_when_remote_size_mismatch(tmp_path: Path, monkeypatch) -> None:
    dst = tmp_path / "BTCUSDT-trades-2026-03.zip"
    _write_zip_with_csv(dst, "id,price,qty,quote_qty,time,is_buyer_maker\n1,1.0,1.0,1.0,1,true\n")

    monkeypatch.setattr(
        um_trades_backfill,
        "download_text",
        lambda *args, **kwargs: DownloadTextResult(ok=False, status_code=404, text=None, error="404"),
    )
    monkeypatch.setattr(um_trades_backfill, "probe_content_length", lambda *args, **kwargs: dst.stat().st_size + 10)

    called = {"value": False}

    def _fake_download(url: str, target: Path, *, timeout_seconds: float = 30.0, max_retries: int = 3) -> DownloadResult:
        called["value"] = True
        _write_zip_with_csv(target, "id,price,qty,quote_qty,time,is_buyer_maker\n2,2.0,2.0,4.0,2,false\n")
        return DownloadResult(ok=True, status_code=200, error=None)

    monkeypatch.setattr(um_trades_backfill, "download_file", _fake_download)

    result = um_trades_backfill._download_or_repair_zip("https://example.com/b.zip", dst, allow_no_checksum=True)
    assert called["value"] is True
    assert result.ok is True
    assert um_trades_backfill._zip_has_csv(dst)
    assert result.verified is False
