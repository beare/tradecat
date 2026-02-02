"""CCXT Pro WS 采集器：订阅可用 channel，尽可能观测字段并集。"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

import ccxt.pro as ccxtpro

from marketdata.types import UnionEvent
from marketdata.union import to_union_event

logger = logging.getLogger(__name__)


def _get_exchange(exchange: str, proxy: Optional[str]):
    cls = getattr(ccxtpro, exchange, None)
    if cls is None:
        raise ValueError(f"不支持的交易所: {exchange}")
    # REST（load_markets）走 httpsProxy；WS 走 wssProxy
    return cls(
        {
            "enableRateLimit": True,
            "timeout": 20000,
            "httpsProxy": proxy,
            "wssProxy": proxy,
        }
    )


async def _loop_watch(name: str, coro_factory, q: asyncio.Queue, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            payload = await coro_factory()
            await q.put((name, payload, None))
        except Exception as e:
            await q.put((name, None, e))
            await asyncio.sleep(1)


async def collect_ws(*, exchange: str, symbol: str, proxy: Optional[str] = None, seconds: int = 30) -> AsyncIterator[UnionEvent]:
    """
    采集 WS 数据并 yield UnionEvent。

    设计目标：
    - 不追求“实时正确性”，追求“字段空间覆盖”（union-of-fields）。
    - 每类数据只要能拿到一次，就能把 key 纳入并集。
    """
    ex = _get_exchange(exchange, proxy)
    await ex.load_markets()

    stop = asyncio.Event()
    q: asyncio.Queue = asyncio.Queue()

    async def watch_ticker():
        return await ex.watch_ticker(symbol)

    async def watch_trades():
        return await ex.watch_trades(symbol, None, 10)

    async def watch_orderbook():
        return await ex.watch_order_book(symbol, 50)

    async def watch_ohlcv():
        return await ex.watch_ohlcv(symbol, "1m", None, 2)

    tasks = [
        asyncio.create_task(_loop_watch("ticker", watch_ticker, q, stop)),
        asyncio.create_task(_loop_watch("trades", watch_trades, q, stop)),
        asyncio.create_task(_loop_watch("orderbook", watch_orderbook, q, stop)),
        asyncio.create_task(_loop_watch("ohlcv", watch_ohlcv, q, stop)),
    ]

    async def _timer():
        if seconds <= 0:
            return
        await asyncio.sleep(seconds)
        stop.set()

    timer_task = asyncio.create_task(_timer())

    try:
        while not stop.is_set() or not q.empty():
            name, payload, err = await q.get()
            if err is not None:
                logger.debug("WS %s 异常: %s", name, err)
                continue
            yield to_union_event(transport="WS", tool="ccxt", exchange=exchange, symbol=symbol, kind=name, raw=payload)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        timer_task.cancel()
        await ex.close()

