import asyncio
from pathlib import Path

import pytest

from binance.datasets.spot_trades import collect as spot_collect


class _StopAfterFirstFlush(Exception):
    pass


class _FakeSpotExchange:
    def __init__(self) -> None:
        self.markets_by_id = {"BTCUSDT": {"symbol": "BTC/USDT"}}
        self._fetch_calls = 0

    async def load_markets(self) -> None:
        return None

    async def watch_trades(self, symbol: str):
        await asyncio.sleep(0.05)
        raise Exception("Connection timeout")

    async def fetch_trades(self, symbol: str, since=None, limit=None, params=None):
        self._fetch_calls += 1
        if self._fetch_calls > 1:
            return []
        return [
            {
                "id": 1,
                "price": "100000.0",
                "amount": "0.01",
                "timestamp": 1775229782000,
                "info": {
                    "s": "BTCUSDT",
                    "t": 1,
                    "p": "100000.0",
                    "q": "0.01",
                    "T": 1775229782000,
                    "m": False,
                    "M": True,
                },
            }
        ]

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_spot_trades_bootstrap_rest_fill_without_initial_ws(monkeypatch, tmp_path: Path) -> None:
    fake_exchange = _FakeSpotExchange()
    flush_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(spot_collect, "patch_ccxt_fast_client_for_aiohttp_313", lambda: None)
    monkeypatch.setattr(spot_collect.ccxtpro, "binance", lambda cfg: fake_exchange)

    def _fake_flush(csv_buffer, db_buffer, **kwargs):
        csv_rows = sum(len(rows) for rows in csv_buffer.values())
        db_rows = len(db_buffer)
        flush_calls.append((csv_rows, db_rows))
        if csv_rows or db_rows:
            raise _StopAfterFirstFlush
        return 0

    monkeypatch.setattr(spot_collect, "_flush", _fake_flush)

    with pytest.raises(_StopAfterFirstFlush):
        await asyncio.wait_for(
            spot_collect.collect_realtime(
                symbols=["BTCUSDT"],
                service_root=tmp_path,
                database_url="",
                write_csv=True,
                write_db=False,
                flush_max_rows=1000,
                flush_interval_seconds=0.05,
                window_seconds=1,
                rest_overlap_multiplier=1,
                gap_threshold_seconds=1,
                gap_check_interval_seconds=0.05,
            ),
            timeout=1.5,
        )

    assert flush_calls
    assert any(csv_rows > 0 for csv_rows, _ in flush_calls)
