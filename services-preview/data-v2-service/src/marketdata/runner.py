"""采集编排：选择 REST/WS + 工具适配器，输出字段并集观测结果。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal

from config import settings
from marketdata.union import UnionBuilder

logger = logging.getLogger(__name__)


def run_rest_once(*, tool: Literal["ccxt"], exchange: str, symbol: str) -> None:
    if tool != "ccxt":
        raise ValueError(f"不支持的工具: {tool}")

    from marketdata.REST.CCXT import collect_rest_once

    builder = UnionBuilder()
    events = collect_rest_once(exchange=exchange, symbol=symbol, proxy=settings.http_proxy)
    for e in events:
        builder.observe(e)
    logger.info("REST 观测字段并集: %s", builder.summary())


async def run_ws_collect(*, tool: Literal["ccxt"], exchange: str, symbol: str, seconds: int) -> None:
    if tool != "ccxt":
        raise ValueError(f"不支持的工具: {tool}")

    from marketdata.WS.CCXT import collect_ws

    builder = UnionBuilder()
    t0 = time.monotonic()
    async for e in collect_ws(exchange=exchange, symbol=symbol, proxy=settings.http_proxy, seconds=seconds):
        builder.observe(e)
        if seconds > 0 and (time.monotonic() - t0) >= seconds:
            break

    logger.info("WS 观测字段并集: %s", builder.summary())
    for kind, count in builder.summary().items():
        keys = builder.keys(kind)
        logger.info("%s 字段样例 (%d): %s", kind, count, keys[:20])

