#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PCTS = (-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0)


def _utc_ts_str(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _patch_ccxt_fast_client_for_aiohttp_313() -> bool:
    """
    兼容 aiohttp>=3.13 的内部实现变更：
    ccxt.pro 的 FastClient 会 monkeypatch aiohttp websocket parser 的 parse_frame，
    但 aiohttp 3.13 的 C reader 上已不存在该属性，导致 WS 直接崩。

    这里做最小侵入：若检测到 parse_frame 不存在，则退化为标准 receive_loop（不做快路径 patch）。
    """
    try:
        from ccxt.async_support.base.ws.client import Client as WsClient  # type: ignore
        from ccxt.async_support.base.ws.fast_client import FastClient  # type: ignore
    except Exception:
        return False

    original = getattr(FastClient, "receive_loop", None)
    if original is None:
        return False

    def receive_loop(self):  # type: ignore[no-untyped-def]
        try:
            connection = self.connection._conn  # noqa: SLF001
            ws_reader = connection.protocol._payload_parser  # noqa: SLF001
            if not hasattr(ws_reader, "parse_frame"):
                return WsClient.receive_loop(self)
        except Exception:
            return WsClient.receive_loop(self)
        return original(self)

    FastClient.receive_loop = receive_loop  # type: ignore[assignment]
    return True


@dataclass
class _SharedOrderBook:
    book: dict[str, Any] | None = None


def _compute_depth_curve(book: dict[str, Any]) -> list[tuple[float, float, float]]:
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return []

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    if best_bid <= 0 or best_ask <= 0:
        return []

    mid = (best_bid + best_ask) / 2.0

    out: list[tuple[float, float, float]] = []
    for pct in PCTS:
        threshold = mid * (1.0 + pct / 100.0)
        depth = 0.0
        notional = 0.0
        if pct < 0:
            for price, qty in bids:
                p = float(price)
                if p < threshold:
                    break
                q = float(qty)
                depth += q
                notional += p * q
        else:
            for price, qty in asks:
                p = float(price)
                if p > threshold:
                    break
                q = float(qty)
                depth += q
                notional += p * q
        out.append((pct, depth, notional))
    return out


async def _capture_book_ticker(exchange, symbol: str, out_path: Path, stop: asyncio.Event) -> int:  # type: ignore[no-untyped-def]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "update_id",
                "best_bid_price",
                "best_bid_qty",
                "best_ask_price",
                "best_ask_qty",
                "transaction_time",
                "event_time",
            ]
        )
        while not stop.is_set():
            try:
                ticker = await exchange.watchTicker(symbol, {"name": "bookTicker"})
            except Exception:
                if stop.is_set():
                    break
                await asyncio.sleep(1)
                continue

            info = ticker.get("info") or {}
            # Binance bookTicker event (futures) 常见字段：u,s,b,B,a,A,T,E
            w.writerow(
                [
                    info.get("u") or info.get("U"),
                    info.get("b"),
                    info.get("B"),
                    info.get("a"),
                    info.get("A"),
                    info.get("T"),
                    info.get("E") or ticker.get("timestamp"),
                ]
            )
            n += 1
            if (n % 500) == 0:
                f.flush()
    return n


async def _watch_order_book(  # type: ignore[no-untyped-def]
    exchange, symbol: str, limit: int, shared: _SharedOrderBook, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        try:
            ob = await exchange.watchOrderBook(symbol, limit)
            shared.book = ob
        except Exception:
            if stop.is_set():
                break
            await asyncio.sleep(1)


async def _emit_book_depth(shared: _SharedOrderBook, interval_s: float, out_path: Path, stop: asyncio.Event) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "percentage", "depth", "notional"])
        while not stop.is_set():
            book = shared.book
            if book is not None:
                ts_ms = int(book.get("timestamp") or _now_ms())
                ts_str = _utc_ts_str(ts_ms)
                curve = _compute_depth_curve(book)
                for pct, depth, notional in curve:
                    w.writerow([ts_str, f"{pct:.2f}", f"{depth:.8f}", f"{notional:.8f}"])
                n += 1
                f.flush()

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except TimeoutError:
                pass
    return n


async def _run(args: argparse.Namespace) -> None:
    import ccxt.pro as ccxtpro  # type: ignore

    _patch_ccxt_fast_client_for_aiohttp_313()

    proxy = args.proxy or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "http://127.0.0.1:7890"
    symbol = args.symbol
    out_dir = Path(args.out_dir)

    # futures UM
    exchange = ccxtpro.binanceusdm(
        {
            "enableRateLimit": True,
            "timeout": 30000,
            "wssProxy": proxy,
            "httpsProxy": proxy,
        }
    )

    started_ms = _now_ms()
    tag = datetime.fromtimestamp(started_ms / 1000.0, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    book_ticker_path = out_dir / f"{args.symbol_id}-bookTicker-ccxtpro-{tag}-3m.csv"
    book_depth_path = out_dir / f"{args.symbol_id}-bookDepth-ccxtpro-{tag}-3m.csv"

    shared = _SharedOrderBook()
    try:
        last_exc: Exception | None = None
        for attempt in range(6):
            try:
                await exchange.load_markets()
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(min(10, 1 + attempt * 2))
        if last_exc is not None:
            raise last_exc
        stop = asyncio.Event()
        tasks = [
            asyncio.create_task(_capture_book_ticker(exchange, symbol, book_ticker_path, stop)),
            asyncio.create_task(_watch_order_book(exchange, symbol, args.depth_limit, shared, stop)),
            asyncio.create_task(_emit_book_depth(shared, args.depth_interval_s, book_depth_path, stop)),
        ]
        await asyncio.sleep(args.duration_s)
        stop.set()
        await exchange.close()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        try:
            await exchange.close()
        except Exception:
            pass

    print(f"bookTicker_csv={book_ticker_path}")
    print(f"bookDepth_csv={book_depth_path}")


def main() -> None:
    p = argparse.ArgumentParser(description="用 ccxt.pro 抓取 Binance UM 的 bookTicker + 派生 bookDepth（3分钟测试）。")
    p.add_argument("--symbol", default="BTC/USDT", help="ccxt unified symbol（默认 BTC/USDT）")
    p.add_argument("--symbol-id", default="BTCUSDT", help="文件名用的 symbol（默认 BTCUSDT）")
    p.add_argument("--duration-s", type=int, default=180, help="抓取时长（秒），默认 180")
    p.add_argument("--proxy", default=None, help="HTTP/WS 代理（默认读环境变量，否则 http://127.0.0.1:7890）")
    p.add_argument("--out-dir", default="core/market/binance/var/hf/downloads/futures/um/realtime/ccxt_pro")
    p.add_argument("--depth-limit", type=int, default=1000, help="watchOrderBook limit（默认 1000）")
    p.add_argument("--depth-interval-s", type=float, default=5.0, help="派生 bookDepth 输出间隔（秒），默认 5.0")
    args = p.parse_args()

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
