"""Futures UM / bookDepth（Raw/基元：百分比档位深度曲线）。

# ==================== 对齐官方目录语义 ====================
# - rel_path（官方相对路径）：
#   data/futures/um/daily/bookDepth/{SYMBOL}/{SYMBOL}-bookDepth-YYYY-MM-DD.csv
#
# - CSV header（样本事实）：
#   timestamp,percentage,depth,notional
#
# ==================== 落库目标（Raw/物理层） ====================
# - 表：market.binance_futures_um_book_depth
# - 幂等键：PRIMARY KEY (venue_id, instrument_id, timestamp, percentage)
# - 维度映射：由 market.shared_venue/market.shared_symbol_map 把 (exchange,symbol) 解析为 (venue_id,instrument_id)
#
# 注意：
# - 交易所 WS 并不直接提供 官方历史归档源 的 bookDepth CSV；
# - 这里采用“order book snapshot -> 计算 depth curve”的方式产出同字段事件流（见文档解释）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import ccxt.pro as ccxtpro
except Exception:  # pragma: no cover
    ccxtpro = None  # type: ignore

from binance.sources.archive_source.ccxt_pro_compat import patch_ccxt_fast_client_for_aiohttp_313
from binance.sources.archive_source.time_utils import ms_to_date_utc, ms_to_datetime_utc
from binance.sources.archive_source.paths import relpath_futures_um_book_depth_daily
from binance.storage.csv_appender import append_csv_rows
from binance.storage.ingest_meta import IngestMetaWriter, IngestRunSpec
from binance.storage.pg import connect
from binance.storage.raw_futures_um_book_depth import RawFuturesUmBookDepthRow, RawFuturesUmBookDepthWriter

logger = logging.getLogger(__name__)

# 默认百分比档位（若要 100% 对齐官方 CSV，请以 官方历史归档源 文件为准后再调整）
PCTS: Tuple[float, ...] = (-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0)

DEFAULT_ORDERBOOK_LIMIT = 1000
DEFAULT_EMIT_INTERVAL_SECONDS = 5.0

CSV_HEADER: Tuple[str, ...] = ("timestamp", "percentage", "depth", "notional")


@dataclass(frozen=True)
class UmBookDepthPoint:
    symbol: str  # Binance 官方历史归档源 symbol，如 BTCUSDT
    timestamp: int  # epoch(ms)
    percentage: float
    depth: float
    notional: float

    @property
    def file_date(self) -> date:
        return ms_to_date_utc(self.timestamp)


@dataclass
class _RealtimeStats:
    ws_books_seen: int = 0
    ws_watch_errors: int = 0

    curve_points_enqueued: int = 0
    queue_full_events: int = 0
    queue_max_size: int = 0
    max_lag_ms: int = 0
    gaps_inserted: int = 0

    csv_rows_written: int = 0
    db_rows_attempted: int = 0
    db_rows_inserted: int = 0
    csv_disabled_due_to_backpressure: int = 0
    csv_disabled_at_queue_size: int = 0


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


def _compute_depth_curve(book: Dict[str, Any]) -> List[Tuple[float, float, float]]:
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return []

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    if best_bid <= 0 or best_ask <= 0:
        return []
    mid = (best_bid + best_ask) / 2.0

    out: List[Tuple[float, float, float]] = []
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


def um_book_depth_to_csv_row(p: UmBookDepthPoint) -> Tuple[str, str, str, str]:
    ts_str = ms_to_datetime_utc(p.timestamp).strftime("%Y-%m-%d %H:%M:%S")
    return (ts_str, f"{p.percentage:.4f}", f"{p.depth:.8f}", f"{p.notional:.8f}")


async def _watch_order_book(
    exchange: Any,
    ccxt_symbol: str,
    archive_symbol: str,
    *,
    limit: int,
    shared_books: Dict[str, Dict[str, Any]],
    stats: _RealtimeStats,
) -> None:
    backoff = 1.0
    while True:
        try:
            ob = await exchange.watchOrderBook(ccxt_symbol, limit)
            shared_books[archive_symbol] = ob
            stats.ws_books_seen += 1
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            stats.ws_watch_errors += 1
            logger.warning("[%s] watchOrderBook 失败，%.1fs 后重试: %s", archive_symbol, backoff, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


async def _emit_curve_loop(
    archive_symbol: str,
    *,
    shared_books: Dict[str, Dict[str, Any]],
    out_queue: "asyncio.Queue[UmBookDepthPoint]",
    emit_interval_seconds: float,
    stats: _RealtimeStats,
    last_seen_time_ms: Dict[str, int],
) -> None:
    last_full_log = 0.0
    while True:
        await asyncio.sleep(max(float(emit_interval_seconds), 0.5))
        book = shared_books.get(archive_symbol)
        if not book:
            continue

        ts_ms = int(book.get("timestamp") or int(time.time() * 1000))
        curve = _compute_depth_curve(book)
        if not curve:
            continue

        last_seen_time_ms[archive_symbol] = int(ts_ms)
        stats.max_lag_ms = max(stats.max_lag_ms, int(time.time() * 1000) - int(ts_ms))

        for pct, depth, notional in curve:
            if out_queue.full():
                stats.queue_full_events += 1
                now = time.time()
                if (now - last_full_log) > 5.0:
                    last_full_log = now
                    logger.warning("[%s] out_queue 已满（阻塞背压中）: maxsize=%d", archive_symbol, out_queue.maxsize)
            await out_queue.put(
                UmBookDepthPoint(
                    symbol=archive_symbol,
                    timestamp=ts_ms,
                    percentage=float(pct),
                    depth=float(depth),
                    notional=float(notional),
                )
            )
            stats.curve_points_enqueued += 1


def _flush(
    csv_buffer: Dict[Path, List[Tuple[str, str, str, str]]],
    db_buffer: List[RawFuturesUmBookDepthRow],
    *,
    write_csv: bool,
    write_db: bool,
    writer: Optional[RawFuturesUmBookDepthWriter],
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
        logger.info("UM bookDepth 入库 inserted=%d attempted=%d", inserted, attempted)
        if meta_writer:
            _update_watermark(meta_writer, db_buffer, exchange=exchange, dataset=dataset)
        db_buffer.clear()

    return int(inserted)


def _update_watermark(meta_writer: IngestMetaWriter, rows: List[RawFuturesUmBookDepthRow], *, exchange: str, dataset: str) -> None:
    if not rows:
        return

    by_symbol: Dict[str, int] = {}
    for r in rows:
        sym = str(r.symbol).upper()
        t = int(r.timestamp)
        prev = by_symbol.get(sym)
        if prev is None or t > prev:
            by_symbol[sym] = t

    for sym, t in by_symbol.items():
        meta_writer.upsert_watermark(exchange=exchange, dataset=dataset, symbol=sym, last_time=int(t), last_id=0)


async def collect_realtime(
    *,
    symbols: Sequence[str],
    service_root: Path,
    database_url: str,
    write_csv: bool = True,
    write_db: bool = True,
    flush_max_rows: int = 5000,
    flush_interval_seconds: float = 1.0,
    gap_threshold_seconds: int = 60,
    gap_check_interval_seconds: float = 10.0,
    orderbook_limit: int = DEFAULT_ORDERBOOK_LIMIT,
    emit_interval_seconds: float = DEFAULT_EMIT_INTERVAL_SECONDS,
) -> None:
    if ccxtpro is None:  # pragma: no cover
        raise RuntimeError("未安装 ccxtpro（import ccxt.pro 失败）")

    patch_ccxt_fast_client_for_aiohttp_313()

    if not symbols:
        raise ValueError("symbols 不能为空")

    write_csv_requested = bool(write_csv)

    conn = connect(database_url) if write_db else None
    writer = RawFuturesUmBookDepthWriter(conn) if conn is not None else None
    meta_writer = IngestMetaWriter(conn) if conn is not None else None
    run_id = None
    if meta_writer:
        run_id = meta_writer.start_run(IngestRunSpec(exchange="binance", dataset="futures.um.bookDepth", mode="realtime"))

    exchange = None
    tasks: List[asyncio.Task] = []
    q: asyncio.Queue[UmBookDepthPoint] = asyncio.Queue(maxsize=200000)
    stats = _RealtimeStats()
    last_seen_time_ms: Dict[str, int] = {}

    run_status = "success"
    run_error: Optional[str] = None

    shared_books: Dict[str, Dict[str, Any]] = {}
    csv_buffer: Dict[Path, List[Tuple[str, str, str, str]]] = {}
    db_buffer: List[RawFuturesUmBookDepthRow] = []
    csv_rows_count = 0

    try:
        exchange = ccxtpro.binanceusdm(_build_ccxt_pro_exchange_config())
        await exchange.load_markets()

        ccxt_symbol_map = {sym: _map_archive_symbol_to_ccxt(exchange, sym) for sym in symbols}

        for archive_symbol, ccxt_symbol in ccxt_symbol_map.items():
            tasks.append(
                asyncio.create_task(
                    _watch_order_book(
                        exchange,
                        ccxt_symbol,
                        archive_symbol,
                        limit=int(orderbook_limit),
                        shared_books=shared_books,
                        stats=stats,
                    )
                )
            )
            tasks.append(
                asyncio.create_task(
                    _emit_curve_loop(
                        archive_symbol,
                        shared_books=shared_books,
                        out_queue=q,
                        emit_interval_seconds=float(emit_interval_seconds),
                        stats=stats,
                        last_seen_time_ms=last_seen_time_ms,
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
                            dataset="futures.um.bookDepth",
                            symbol=archive_symbol,
                            start_time=int(last_ms),
                            end_time=int(now_ms),
                            reason=f"realtime_stale>{gap_threshold_seconds}s; derived_curve",
                            run_id=run_id,
                        )
                        stats.gaps_inserted += 1

        tasks.append(asyncio.create_task(_gap_watch_loop()))

        last_flush = asyncio.get_event_loop().time()

        while True:
            try:
                p0 = await asyncio.wait_for(q.get(), timeout=flush_interval_seconds)

                batch: List[UmBookDepthPoint] = [p0]
                drain_n = min(q.qsize(), 5000)
                for _ in range(drain_n):
                    try:
                        batch.append(q.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                stats.queue_max_size = max(stats.queue_max_size, q.qsize() + len(batch))

                for p in batch:
                    rel_path = relpath_futures_um_book_depth_daily(p.symbol, p.file_date)
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
                        csv_buffer.setdefault(local_path, []).append(um_book_depth_to_csv_row(p))
                        csv_rows_count += 1

                    if write_db and writer:
                        db_buffer.append(
                            RawFuturesUmBookDepthRow(
                                exchange="binance",
                                symbol=p.symbol,
                                timestamp=p.timestamp,
                                percentage=p.percentage,
                                depth=p.depth,
                                notional=p.notional,
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
                            dataset="futures.um.bookDepth",
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
                        dataset="futures.um.bookDepth",
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
                        dataset="futures.um.bookDepth",
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
        logger.error("UM bookDepth 实时采集异常: %s", e, exc_info=True)
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
                    p = q.get_nowait()
                except asyncio.QueueEmpty:
                    break

                rel_path = relpath_futures_um_book_depth_daily(p.symbol, p.file_date)
                local_path = service_root / rel_path

                if write_csv:
                    csv_buffer.setdefault(local_path, []).append(um_book_depth_to_csv_row(p))
                    csv_rows_count += 1

                if write_db and writer:
                    db_buffer.append(
                        RawFuturesUmBookDepthRow(
                            exchange="binance",
                            symbol=p.symbol,
                            timestamp=p.timestamp,
                            percentage=p.percentage,
                            depth=p.depth,
                            notional=p.notional,
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
                dataset="futures.um.bookDepth",
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
                            "orderbook_limit": int(orderbook_limit),
                            "emit_interval_seconds": float(emit_interval_seconds),
                            "queue_maxsize": int(q.maxsize),
                            "ws_books_seen": int(stats.ws_books_seen),
                            "ws_watch_errors": int(stats.ws_watch_errors),
                            "curve_points_enqueued": int(stats.curve_points_enqueued),
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
