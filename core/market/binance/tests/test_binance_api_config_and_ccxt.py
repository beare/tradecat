from __future__ import annotations

import pytest

import binance.sources.binance_api.adapters.ccxt as ccxt_adapter
from binance.sources.binance_api.config import Settings, normalize_interval


def test_normalize_interval_accepts_known_values() -> None:
    assert normalize_interval("1m") == "1m"
    assert normalize_interval("5m") == "5m"
    assert normalize_interval("1h") == "1h"
    assert normalize_interval("1d") == "1d"
    assert normalize_interval("1w") == "1w"
    assert normalize_interval("1M") == "1M"


def test_normalize_interval_rejects_unknown_values() -> None:
    with pytest.raises(ValueError):
        normalize_interval("bad")


def test_settings_database_url_from_env(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5433/market_data")
    settings = Settings()
    assert settings.database_url.startswith("postgresql://")


def test_maybe_set_ban_from_error_sets_ban_with_source(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_parse_ban(msg: str) -> float:
        calls["parse_ban_msg"] = msg
        return 2000.0

    def fake_set_ban(until: float, source: str | None = None) -> None:
        calls["set_ban"] = (until, source)

    monkeypatch.setattr(ccxt_adapter, "parse_ban", fake_parse_ban)
    monkeypatch.setattr(ccxt_adapter, "set_ban", fake_set_ban)
    monkeypatch.setattr(ccxt_adapter.time, "time", lambda: 1000.0)

    ok = ccxt_adapter._maybe_set_ban_from_error(
        "418 I'm a teapot ... IP banned until 1772473135505",
        source="rest_gapfill",
    )
    assert ok is True
    assert isinstance(calls.get("parse_ban_msg"), str)
    assert calls.get("set_ban") == (2000.0, "rest_gapfill")


def test_maybe_set_ban_from_error_sets_short_ban_for_429(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_set_ban(until: float, source: str | None = None) -> None:
        calls["set_ban"] = (until, source)

    monkeypatch.setattr(ccxt_adapter, "set_ban", fake_set_ban)
    monkeypatch.setattr(ccxt_adapter.time, "time", lambda: 1000.0)

    ok = ccxt_adapter._maybe_set_ban_from_error("429 Too many requests", source="metrics")
    assert ok is True
    assert calls.get("set_ban") == (1060.0, "metrics")


def test_fetch_ohlcv_native_klines_exception_triggers_ban_and_short_circuits(monkeypatch) -> None:
    monkeypatch.setattr(ccxt_adapter, "acquire", lambda *_: None)
    monkeypatch.setattr(ccxt_adapter, "release", lambda *_: None)

    calls: dict[str, object] = {}

    def fake_set_ban(until: float, source: str | None = None) -> None:
        calls["set_ban"] = (until, source)

    monkeypatch.setattr(ccxt_adapter, "set_ban", fake_set_ban)
    monkeypatch.setattr(ccxt_adapter, "parse_ban", lambda *_: 2000.0)
    monkeypatch.setattr(ccxt_adapter.time, "time", lambda: 1000.0)

    class _FakeClient:
        def fapiPublicGetKlines(self, *_args, **_kwargs):
            raise RuntimeError("418 I'm a teapot ... IP banned until 1772473135505")

    monkeypatch.setattr(ccxt_adapter, "get_client", lambda *_: _FakeClient())

    out = ccxt_adapter.fetch_ohlcv(
        "binance",
        "BTCUSDT",
        interval="1m",
        since_ms=None,
        limit=1,
        ban_source="rest_gapfill",
    )
    assert out == []
    assert calls.get("set_ban") == (2000.0, "rest_gapfill")
