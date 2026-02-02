"""data-v2-service 入口：构造“全字段并集”并输出观测结果。"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Literal

from config import settings
from marketdata.runner import run_rest_once, run_ws_collect

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="data-v2-service")
    p.add_argument("--mode", choices=["rest", "ws"], default="ws", help="采集方式：REST 或 WS")
    p.add_argument("--tool", choices=["ccxt"], default="ccxt", help="工具适配器名称")
    p.add_argument("--exchange", default=settings.exchange, help="交易所名称（ccxt id），例如 binanceusdm")
    p.add_argument("--symbol", default=settings.symbol, help="交易对（ccxt unified symbol），例如 BTC/USDT:USDT")
    p.add_argument("--seconds", type=int, default=30, help="采集运行秒数；0 表示常驻运行")
    return p.parse_args(argv)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    args = _parse_args(sys.argv[1:])
    settings.exchange = args.exchange
    settings.symbol = args.symbol

    mode: Literal["rest", "ws"] = args.mode
    tool: Literal["ccxt"] = args.tool

    if mode == "rest":
        run_rest_once(tool=tool, exchange=args.exchange, symbol=args.symbol)
        return

    asyncio.run(run_ws_collect(tool=tool, exchange=args.exchange, symbol=args.symbol, seconds=args.seconds))


if __name__ == "__main__":
    main()

