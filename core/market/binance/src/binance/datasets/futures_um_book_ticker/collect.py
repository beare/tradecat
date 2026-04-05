"""Futures UM / bookTicker（Raw/基元：买一卖一流）。

# ==================== 对齐官方目录语义 ====================
# - rel_path（官方相对路径）：
#   data/futures/um/daily/bookTicker/{SYMBOL}/{SYMBOL}-bookTicker-YYYY-MM-DD.csv
#
# - CSV header（样本事实）：
#   update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,event_time
#
# ==================== 落库目标（Raw/物理层） ====================
# - 表：market.binance_futures_um_book_ticker
# - 幂等键：PRIMARY KEY (venue_id, instrument_id, event_time, update_id)
# - 维度映射：由 market.shared_venue/market.shared_symbol_map 把 (exchange,symbol) 解析为 (venue_id,instrument_id)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import ccxt.pro as ccxtpro
except Exception:  # pragma: no cover
    ccxtpro = None  # type: ignore

from binance.sources.archive_source.ccxt_pro_compat import patch_ccxt_fast_client_for_aiohttp_313
from binance.sources.archive_source.decimal_utils import format_decimal_for_archive_csv
from binance.sources.archive_source.time_utils import ms_to_date_utc
from binance.sources.archive_source.paths import relpath_futures_um_book_ticker_daily
from binance.storage.csv_appender import append_csv_rows
from binance.storage.ingest_meta import IngestMetaWriter, IngestRunSpec
from binance.storage.pg import connect
from binance.storage.raw_futures_um_book_ticker import RawFuturesUmBookTickerRow, RawFuturesUmBookTickerWriter

logger = logging.getLogger(__name__)

CSV_HEADER: Tuple[str, ...] = (
    "update_id",
    "best_bid_price",
    "best_bid_qty",
    "best_ask_price",
    "best_ask_qty",
    "transaction_time",
    "event_time",
)


@dataclass(frozen=True)
class UmBookTicker:
    symbol: str  # Binance 官方历史归档源 symbol，如 BTCUSDT
    update_id: int
    best_bid_price: Decimal
    best_bid_qty: Decimal
    best_ask_price: Decimal
    best_ask_qty: Decimal
    transaction_time: Optional[int]
    event_time: int  # epoch(ms)

    @property
    def file_date(self) -> date:
        return ms_to_date_utc(self.event_time)


@dataclass
class _RealtimeStats:
    ws_events_enqueued: int = 0
    ws_parse_errors: int = 0
    ws_watch_errors: int = 0

    queue_full_events: int = 0
    queue_max_size: int = 0
    max_lag_ms: int = 0
    gaps_inserted: int = 0

    csv_rows_written: int = 0
    db_rows_attempted: int = 0
    db_rows_inserted: int = 0
    csv_disabled_due_to_backpressure: int = 0
    csv_disabled_at_queue_size: int = 0


def _to_int(value: Any, *, field: str) -> int:
    if value is None:
        raise ValueError(f"{field} 缺失")
    return int(value)


def _to_decimal(value: Any, *, field: str) -> Decimal:
    if value is None:
        raise ValueError(f"{field} 缺失")
    return Decimal(str(value))


def parse_um_book_ticker_from_ccxt(ticker: Dict[str, Any], fallback_symbol: str) -> UmBookTicker:
    """把 ccxt.pro 的 ticker 对象解析成 UM bookTicker（字段完备）。"""

    info = ticker.get("info") or {}
    symbol = str(info.get("s") or fallback_symbol).upper()

    update_id = info.get("u") or info.get("U") or info.get("updateId")
    if update_id is None:
        raise ValueError("update_id 缺失")

    best_bid_price = _to_decimal(info.get("b") or ticker.get("bid"), field="best_bid_price")
    best_bid_qty = _to_decimal(info.get("B"), field="best_bid_qty")
    best_ask_price = _to_decimal(info.get("a") or ticker.get("ask"), field="best_ask_price")
    best_ask_qty = _to_decimal(info.get("A"), field="best_ask_qty")

    transaction_time_raw = info.get("T")
    transaction_time = int(transaction_time_raw) if transaction_time_raw is not None and str(transaction_time_raw) != "" else None

    event_time_raw = info.get("E") or ticker.get("timestamp")
    event_time = _to_int(event_time_raw, field="event_time")

    return UmBookTicker(
        symbol=symbol,
        update_id=int(update_id),
        best_bid_price=best_bid_price,
        best_bid_qty=best_bid_qty,
        best_ask_price=best_ask_price,
        best_ask_qty=best_ask_qty,
        transaction_time=transaction_time,
        event_time=event_time,
    )


def um_book_ticker_to_csv_row(t: UmBookTicker) -> Tuple[str, str, str, str, str, str, str]:
    return (
        str(t.update_id),
        format_decimal_for_archive_csv(t.best_bid_price),
        format_decimal_for_archive_csv(t.best_bid_qty),
        format_decimal_for_archive_csv(t.best_ask_price),
        format_decimal_for_archive_csv(t.best_ask_qty),
        "" if t.transaction_time is None else str(t.transaction_time),
        str(t.event_time),
    )


def _map_archive_symbol_to_ccxt(exchange: Any, archive_symbol: str) -> str:
    """使用 markets_by_id 做映射：BTCUSDT -> BTC/USDT:USDT。"""

    sym = archive_symbol.upper()
    market = None
    markets = getattr(exchange, "markets_by_id", {}).get(sym)
    if isinstance(markets, list) and markets:
        market = markets[0]
    elif isinstance(markets, dict):
        market = markets

    if not market:
        raise ValueError(f"交易对不存在或未加载 markets: {archive_symbol}")

    ccxt_symbol = market.get("symbol")
    if not ccxt_symbol:
        raise ValueError(f"无法映射到 ccxt symbol: {archive_symbol}")
    return str(ccxt_symbol)


def _build_ccxt_pro_exchange_config() -> dict[str, Any]:
    import os

    proxy = (os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("ALL_PROXY") or "").strip()
    cfg: dict[str, Any] = {
        "enableRateLimit": True,
        "timeout": 30000,
    }
    if proxy:
        cfg["wssProxy"] = proxy
        cfg["httpsProxy"] = proxy
    return cfg


async def _watch_book_ticker(
    exchange: Any,
    ccxt_symbol: str,
    archive_symbol: str,
    out_queue: "asyncio.Queue[UmBookTicker]",
    stats: _RealtimeStats,
    last_seen_time_ms: Dict[str, int],
    last_seen_id: Dict[str, int],
) -> None:
    backoff = 1.0
    last_full_log = 0.0
    while True:
        try:
            result = await exchange.watch_bids_asks([ccxt_symbol])
            backoff = 1.0
            if isinstance(result, dict):
                ticker = result.get(ccxt_symbol)
                if ticker is None and len(result) == 1:
                    ticker = next(iter(result.values()))
            else:
                ticker = result
            if ticker is None:
                raise ValueError("watch_bids_asks 返回空结果")

            try:
                t = parse_um_book_ticker_from_ccxt(ticker, fallback_symbol=archive_symbol)
            except Exception as e:
                stats.ws_parse_errors += 1
                logger.warning("[%s] 解析 bookTicker 失败: %s", archive_symbol, e)
                continue

            last_seen_time_ms[archive_symbol] = int(t.event_time)
            last_seen_id[archive_symbol] = int(t.update_id)
            stats.max_lag_ms = max(stats.max_lag_ms, int(time.time() * 1000) - int(t.event_time))

            if out_queue.full():
                stats.queue_full_events += 1
                now = time.time()
                if (now - last_full_log) > 5.0:
                    last_full_log = now
                    logger.warning("[%s] out_queue 已满（阻塞背压中）: maxsize=%d", archive_symbol, out_queue.maxsize)
            await out_queue.put(t)
            stats.ws_events_enqueued += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            stats.ws_watch_errors += 1
            logger.warning("[%s] watch_bids_asks(bookTicker) 失败，%.1fs 后重试: %s", archive_symbol, backoff, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _flush(
    csv_buffer: Dict[Path, List[Tuple[str, str, str, str, str, str, str]]],
    db_buffer: List[RawFuturesUmBookTickerRow],
    *,
    write_csv: bool,
    write_db: bool,
    writer: Optional[RawFuturesUmBookTickerWriter],
    meta_writer: Optional[IngestMetaWriter],
    exchange: str,
    dataset: str,
) -> int:
    inserted = 0

    if write_csv and csv_buffer:
        for path, rows in list(csv_buffer.items()):
            append_csv_rows(path, CSV_HEADER, rows)
        csv_buffer.clear()

    if write_db and writer and db_buffer:
        attempted = int(len(db_buffer))
        inserted = int(writer.insert_rows(db_buffer))
        logger.info("UM bookTicker 入库 inserted=%d attempted=%d", inserted, attempted)
        if meta_writer:
            _update_watermark(meta_writer, db_buffer, exchange=exchange, dataset=dataset)
        db_buffer.clear()

    return int(inserted)


def _update_watermark(
    meta_writer: IngestMetaWriter, rows: List[RawFuturesUmBookTickerRow], *, exchange: str, dataset: str
) -> None:
    if not rows:
        return

    by_symbol: Dict[str, tuple[int, int]] = {}
    for r in rows:
        sym = str(r.symbol).upper()
        t = int(r.event_time)
        u = int(r.update_id)
        prev = by_symbol.get(sym)
        if prev is None:
            by_symbol[sym] = (t, u)
        else:
            prev_t, prev_u = prev
            if t > prev_t or (t == prev_t and u > prev_u):
                by_symbol[sym] = (t, u)

    for sym, (t, u) in by_symbol.items():
        meta_writer.upsert_watermark(exchange=exchange, dataset=dataset, symbol=sym, last_time=int(t), last_id=int(u))


async def collect_realtime(
    *,
    symbols: Sequence[str],
    service_root: Path,
    database_url: str,
    write_csv: bool = True,
    write_db: bool = True,
    flush_max_rows: int = 5000,
    flush_interval_seconds: float = 1.0,
    gap_threshold_seconds: int = 30,
    gap_check_interval_seconds: float = 10.0,
) -> None:
    if ccxtpro is None:  # pragma: no cover
        raise RuntimeError("未安装 ccxtpro（import ccxt.pro 失败）")

    patch_ccxt_fast_client_for_aiohttp_313()

    if not symbols:
        raise ValueError("symbols 不能为空")

    write_csv_requested = bool(write_csv)

    conn = connect(database_url) if write_db else None
    writer = RawFuturesUmBookTickerWriter(conn) if conn is not None else None
    meta_writer = IngestMetaWriter(conn) if conn is not None else None
    run_id = None
    if meta_writer:
        run_id = meta_writer.start_run(IngestRunSpec(exchange="binance", dataset="futures.um.bookTicker", mode="realtime"))

    exchange = None
    tasks: List[asyncio.Task] = []
    q: asyncio.Queue[UmBookTicker] = asyncio.Queue(maxsize=100000)
    stats = _RealtimeStats()
    last_seen_time_ms: Dict[str, int] = {}
    last_seen_id: Dict[str, int] = {}

    run_status = "success"
    run_error: Optional[str] = None

    csv_buffer: Dict[Path, List[Tuple[str, str, str, str, str, str, str]]] = {}
    db_buffer: List[RawFuturesUmBookTickerRow] = []
    csv_rows_count = 0

    try:
        exchange = ccxtpro.binanceusdm(_build_ccxt_pro_exchange_config())
        await exchange.load_markets()

        ccxt_symbol_map = {sym: _map_archive_symbol_to_ccxt(exchange, sym) for sym in symbols}

        for archive_symbol, ccxt_symbol in ccxt_symbol_map.items():
            tasks.append(
                asyncio.create_task(
                    _watch_book_ticker(
                        exchange,
                        ccxt_symbol,
                        archive_symbol,
                        q,
                        stats,
                        last_seen_time_ms,
                        last_seen_id,
                    )
                )
            )

        async def _gap_watch_loop() -> None:
            threshold_ms = int(gap_threshold_seconds) * 1000
            inflight: Dict[str, float] = {}
            while True:
                await asyncio.sleep(max(float(gap_check_interval_seconds), 1.0))
                now_ms = int(time.time() * 1000)

                for archive_symbol in list(ccxt_symbol_map.keys()):
                    last_ms = last_seen_time_ms.get(archive_symbol)
                    if last_ms is None:
                        continue
                    if now_ms - last_ms < threshold_ms:
                        continue

                    last_trigger = inflight.get(archive_symbol, 0.0)
                    if (time.time() - last_trigger) < max(5.0, float(gap_threshold_seconds) / 2):
                        continue
                    inflight[archive_symbol] = time.time()

                    if meta_writer and run_id is not None:
                        meta_writer.insert_gap(
                            exchange="binance",
                            dataset="futures.um.bookTicker",
                            symbol=archive_symbol,
                            start_time=int(last_ms),
                            end_time=int(now_ms),
                            reason=f"realtime_stale>{gap_threshold_seconds}s; ws_only",
                            run_id=run_id,
                        )
                        stats.gaps_inserted += 1

        tasks.append(asyncio.create_task(_gap_watch_loop()))

        last_flush = asyncio.get_event_loop().time()

        while True:
            try:
                t0 = await asyncio.wait_for(q.get(), timeout=flush_interval_seconds)

                batch: List[UmBookTicker] = [t0]
                drain_n = min(q.qsize(), 5000)
                for _ in range(drain_n):
                    try:
                        batch.append(q.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                stats.queue_max_size = max(stats.queue_max_size, q.qsize() + len(batch))

                for t in batch:
                    rel_path = relpath_futures_um_book_ticker_daily(t.symbol, t.file_date)
                    local_path = service_root / rel_path

                    if (
                        write_csv
                        and write_db
                        and stats.csv_disabled_due_to_backpressure == 0
                        and q.maxsize > 0
                        and q.qsize() >= int(q.maxsize * 0.9)
                    ):
                        write_csv = False
                        csv_buffer.clear()
                        csv_rows_count = 0
                        stats.csv_disabled_due_to_backpressure = 1
                        stats.csv_disabled_at_queue_size = max(stats.csv_disabled_at_queue_size, int(q.qsize()))
                        logger.warning(
                            "out_queue backlog 过高，自动关闭 CSV 写入以保障入库: qsize=%d maxsize=%d",
                            int(q.qsize()),
                            int(q.maxsize),
                        )

                    if write_csv:
                        csv_buffer.setdefault(local_path, []).append(um_book_ticker_to_csv_row(t))
                        csv_rows_count += 1

                    if write_db and writer:
                        db_buffer.append(
                            RawFuturesUmBookTickerRow(
                                exchange="binance",
                                symbol=t.symbol,
                                update_id=t.update_id,
                                best_bid_price=t.best_bid_price,
                                best_bid_qty=t.best_bid_qty,
                                best_ask_price=t.best_ask_price,
                                best_ask_qty=t.best_ask_qty,
                                transaction_time=t.transaction_time,
                                event_time=t.event_time,
                            )
                        )

                    if len(db_buffer) >= flush_max_rows or csv_rows_count >= flush_max_rows:
                        stats.csv_rows_written += sum(len(rows) for rows in csv_buffer.values())
                        stats.db_rows_attempted += len(db_buffer)
                        stats.db_rows_inserted += _flush(
                            csv_buffer,
                            db_buffer,
                            write_csv=write_csv,
                            write_db=write_db,
                            writer=writer,
                            meta_writer=meta_writer,
                            exchange="binance",
                            dataset="futures.um.bookTicker",
                        )
                        csv_rows_count = 0
                        last_flush = asyncio.get_event_loop().time()

                now = asyncio.get_event_loop().time()
                if (db_buffer or csv_rows_count) and now - last_flush >= flush_interval_seconds:
                    stats.csv_rows_written += sum(len(rows) for rows in csv_buffer.values())
                    stats.db_rows_attempted += len(db_buffer)
                    stats.db_rows_inserted += _flush(
                        csv_buffer,
                        db_buffer,
                        write_csv=write_csv,
                        write_db=write_db,
                        writer=writer,
                        meta_writer=meta_writer,
                        exchange="binance",
                        dataset="futures.um.bookTicker",
                    )
                    csv_rows_count = 0
                    last_flush = now

            except asyncio.TimeoutError:
                now = asyncio.get_event_loop().time()
                if now - last_flush >= flush_interval_seconds:
                    stats.csv_rows_written += sum(len(rows) for rows in csv_buffer.values())
                    stats.db_rows_attempted += len(db_buffer)
                    stats.db_rows_inserted += _flush(
                        csv_buffer,
                        db_buffer,
                        write_csv=write_csv,
                        write_db=write_db,
                        writer=writer,
                        meta_writer=meta_writer,
                        exchange="binance",
                        dataset="futures.um.bookTicker",
                    )
                    csv_rows_count = 0
                    last_flush = now

    except asyncio.CancelledError:
        run_status = "partial"
        run_error = "collector cancelled"
        raise
    except Exception as e:  # noqa: BLE001
        run_status = "failed"
        run_error = str(e)
        logger.error("UM bookTicker 实时采集异常: %s", e, exc_info=True)
        raise
    finally:
        for task in tasks:
            try:
                task.cancel()
            except Exception:
                pass
        await asyncio.gather(*tasks, return_exceptions=True)

        try:
            while True:
                try:
                    t = q.get_nowait()
                except asyncio.QueueEmpty:
                    break

                rel_path = relpath_futures_um_book_ticker_daily(t.symbol, t.file_date)
                local_path = service_root / rel_path

                if write_csv:
                    csv_buffer.setdefault(local_path, []).append(um_book_ticker_to_csv_row(t))
                    csv_rows_count += 1

                if write_db and writer:
                    db_buffer.append(
                        RawFuturesUmBookTickerRow(
                            exchange="binance",
                            symbol=t.symbol,
                            update_id=t.update_id,
                            best_bid_price=t.best_bid_price,
                            best_bid_qty=t.best_bid_qty,
                            best_ask_price=t.best_ask_price,
                            best_ask_qty=t.best_ask_qty,
                            transaction_time=t.transaction_time,
                            event_time=t.event_time,
                        )
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("退出前 drain 队列失败: %s", e)

        try:
            stats.csv_rows_written += sum(len(rows) for rows in csv_buffer.values())
            stats.db_rows_attempted += len(db_buffer)
            stats.db_rows_inserted += _flush(
                csv_buffer,
                db_buffer,
                write_csv=write_csv,
                write_db=write_db,
                writer=writer,
                meta_writer=meta_writer,
                exchange="binance",
                dataset="futures.um.bookTicker",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("退出前 flush 失败: %s", e)

        try:
            if exchange is not None:
                await exchange.close()
        finally:
            if meta_writer and run_id is not None:
                try:
                    meta_writer.finish_run(
                        run_id,
                        status=run_status,
                        error_message=run_error,
                        meta={
                            "symbols": [str(s).upper() for s in symbols],
                            "symbols_count": len(symbols),
                            "write_csv_requested": bool(write_csv_requested),
                            "write_csv": bool(write_csv),
                            "write_db": bool(write_db),
                            "flush_max_rows": int(flush_max_rows),
                            "flush_interval_seconds": float(flush_interval_seconds),
                            "queue_maxsize": int(q.maxsize),
                            "ws_events_enqueued": int(stats.ws_events_enqueued),
                            "ws_parse_errors": int(stats.ws_parse_errors),
                            "ws_watch_errors": int(stats.ws_watch_errors),
                            "queue_full_events": int(stats.queue_full_events),
                            "queue_max_size": int(stats.queue_max_size),
                            "max_lag_ms": int(stats.max_lag_ms),
                            "gaps_inserted": int(stats.gaps_inserted),
                            "csv_rows_written": int(stats.csv_rows_written),
                            "db_rows_attempted": int(stats.db_rows_attempted),
                            "db_rows_inserted": int(stats.db_rows_inserted),
                            "csv_disabled_due_to_backpressure": int(stats.csv_disabled_due_to_backpressure),
                            "csv_disabled_at_queue_size": int(stats.csv_disabled_at_queue_size),
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("写入 ingest_runs 结束状态失败: %s", e)
            if conn is not None:
                conn.close()


__all__ = ["collect_realtime"]
