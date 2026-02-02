"""CCXT REST 采集器：尽可能拉取可用字段。"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import ccxt

from marketdata.types import UnionEvent
from marketdata.union import to_union_event

logger = logging.getLogger(__name__)


def _get_exchange(exchange: str, proxy: Optional[str]) -> ccxt.Exchange:
    cls = getattr(ccxt, exchange, None)
    if cls is None:
        raise ValueError(f"不支持的交易所: {exchange}")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    return cls({"enableRateLimit": True, "timeout": 20000, "proxies": proxies})


def _safe_call(fn, *, name: str) -> Any | None:
    try:
        return fn()
    except Exception as e:
        logger.warning("REST 获取失败 %s: %s", name, e)
        return None


def collect_rest_once(*, exchange: str, symbol: str, proxy: Optional[str] = None) -> List[UnionEvent]:
    ex = _get_exchange(exchange, proxy)
    ex.load_markets()

    events: List[UnionEvent] = []

    ticker = _safe_call(lambda: ex.fetch_ticker(symbol), name="ticker")
    if ticker is not None:
        events.append(to_union_event(transport="REST", tool="ccxt", exchange=exchange, symbol=symbol, kind="ticker", raw=ticker))

    orderbook = _safe_call(lambda: ex.fetch_order_book(symbol, limit=1000), name="orderbook")
    if orderbook is not None:
        events.append(
            to_union_event(transport="REST", tool="ccxt", exchange=exchange, symbol=symbol, kind="orderbook", raw=orderbook)
        )

    trades = _safe_call(lambda: ex.fetch_trades(symbol, limit=100), name="trades")
    if trades is not None:
        events.append(to_union_event(transport="REST", tool="ccxt", exchange=exchange, symbol=symbol, kind="trades", raw=trades))

    ohlcv = _safe_call(lambda: ex.fetch_ohlcv(symbol, timeframe="1m", limit=10), name="ohlcv")
    if ohlcv is not None:
        events.append(to_union_event(transport="REST", tool="ccxt", exchange=exchange, symbol=symbol, kind="ohlcv", raw=ohlcv))

    ex.close()
    return events

