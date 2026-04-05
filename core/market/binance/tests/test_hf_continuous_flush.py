from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from binance.datasets.futures_um_trades import collect as trades_collect


class _FakeExchange:
    def __init__(self) -> None:
        self.calls = 0
        self.markets_by_id = {"BTCUSDT": [{"symbol": "BTC/USDT:USDT"}]}

    async def load_markets(self):
        return None

    async def watch_trades(self, _symbol: str):
        await asyncio.sleep(0.01)
        self.calls += 1
        trade_id = self.calls
        ts = 1775229377000 + trade_id
        return [
            {
                "info": {
                    "s": "BTCUSDT",
                    "t": trade_id,
                    "p": "100000.0",
                    "q": "0.001",
                    "T": ts,
                    "m": False,
                }
            }
        ]

    async def fetch_trades(self, *_args, **_kwargs):
        return []

    async def close(self):
        return None


class _StopAfterFirstFlush(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_futures_um_trades_flushes_by_time_under_continuous_flow(monkeypatch, tmp_path: Path):
    flush_calls: list[tuple[int, int]] = []

    def _fake_flush(csv_buffer, db_buffer, **_kwargs):
        flush_calls.append((sum(len(rows) for rows in csv_buffer.values()), len(db_buffer)))
        raise _StopAfterFirstFlush("stop after first timed flush")

    monkeypatch.setattr(trades_collect, "ccxtpro", SimpleNamespace(binanceusdm=lambda _cfg: _FakeExchange()))
    monkeypatch.setattr(trades_collect, "patch_ccxt_fast_client_for_aiohttp_313", lambda: None)
    monkeypatch.setattr(trades_collect, "_flush", _fake_flush)

    with pytest.raises(_StopAfterFirstFlush):
        await trades_collect.collect_realtime(
            symbols=["BTCUSDT"],
            service_root=tmp_path,
            database_url="",
            write_csv=True,
            write_db=False,
            flush_max_rows=10_000,
            flush_interval_seconds=0.05,
            window_seconds=300,
            rest_overlap_multiplier=3,
            gap_threshold_seconds=30,
            gap_check_interval_seconds=60.0,
        )

    assert flush_calls, "持续有 trade 流量时也必须按时间触发 flush"
    assert flush_calls[0][0] > 0 or flush_calls[0][1] > 0
