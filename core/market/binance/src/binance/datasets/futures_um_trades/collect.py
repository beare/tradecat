"""Futures UM / trades（Raw/基元：逐笔成交）

# ==================== 对齐官方目录语义 ====================
# - rel_path（官方相对路径）：
#   data/futures/um/daily/trades/{SYMBOL}/{SYMBOL}-trades-YYYY-MM-DD.csv
#
# - CSV header（样本事实）：
#   id,price,qty,quote_qty,time,is_buyer_maker
#
# ==================== 字段设计（强约束：字段完备） ====================
# - id             : 逐笔成交 ID（Binance trade id）
# - price          : 成交价
# - qty            : 成交量（base）
# - quote_qty      : 成交额（quote），必须补齐：quote_qty = price * qty
# - time           : 成交时间 epoch(ms)
# - is_buyer_maker : 买方是否为 maker（Binance 字段 m）
#
# ==================== 落库目标（Raw/物理层） ====================
# - 表：market.binance_futures_um_trades
# - 幂等键：PRIMARY KEY (venue_id, instrument_id, time, id)
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

from binance.sources.archive_source.decimal_utils import format_decimal_for_archive_csv
from binance.sources.archive_source.ccxt_pro_compat import patch_ccxt_fast_client_for_aiohttp_313
from binance.sources.archive_source.time_utils import ms_to_date_utc
from binance.sources.archive_source.paths import relpath_futures_um_trades_daily
from binance.storage.csv_appender import append_csv_rows
from binance.storage.pg import connect
from binance.storage.ingest_meta import IngestMetaWriter, IngestRunSpec
from binance.storage.raw_futures_um_trades import RawFuturesUmTradeRow, RawFuturesUmTradesWriter

logger = logging.getLogger(__name__)

CSV_HEADER: Tuple[str, ...] = ("id", "price", "qty", "quote_qty", "time", "is_buyer_maker")


@dataclass(frozen=True)
class UmTrade:
    """UM trades 原子事件（已对齐官方字段语义）。"""

    symbol: str  # Binance 官方历史归档源 symbol，如 BTCUSDT
    id: int
    price: Decimal
    qty: Decimal
    quote_qty: Decimal
    time: int  # epoch(ms)
    is_buyer_maker: bool

    @property
    def file_date(self) -> date:
        return ms_to_date_utc(self.time)


@dataclass
class _RealtimeStats:
    ws_trades_enqueued: int = 0
    ws_parse_errors: int = 0
    ws_watch_errors: int = 0

    rest_fetch_calls: int = 0
    rest_trades_enqueued: int = 0

    queue_full_events: int = 0
    queue_max_size: int = 0
    max_lag_ms: int = 0

    gaps_inserted: int = 0

    csv_rows_written: int = 0
    db_rows_attempted: int = 0
    db_rows_inserted: int = 0
    csv_disabled_due_to_backpressure: int = 0
    csv_disabled_at_queue_size: int = 0


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        raise ValueError("is_buyer_maker 缺失")
    s = str(value).strip().lower()
    if s in {"true", "1", "yes", "y"}:
        return True
    if s in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"无法解析布尔值: {value!r}")


def parse_um_trade_from_ccxt(trade: Dict[str, Any], fallback_symbol: str) -> UmTrade:
    """把 ccxt.pro 的 trade 对象解析成 UM trades（严格字段完备）。

    依赖：Binance trade stream 的原始字段通常位于 trade['info']：
    - t: trade id
    - p: price (str)
    - q: qty (str)
    - T: trade time (ms)
    - m: isBuyerMaker (bool)
    - s: symbol (BTCUSDT)
    """

    info = trade.get("info") or {}

    symbol = str(info.get("s") or fallback_symbol).upper()

    trade_id = info.get("t")
    if trade_id is None:
        trade_id = trade.get("id")
    if trade_id is None:
        raise ValueError("trade id 缺失")
    trade_id_int = int(trade_id)

    price_raw = info.get("p")
    if price_raw is None:
        price_raw = trade.get("price")
    if price_raw is None:
        raise ValueError("price 缺失")
    price = Decimal(str(price_raw))

    qty_raw = info.get("q")
    if qty_raw is None:
        qty_raw = trade.get("amount")
    if qty_raw is None:
        raise ValueError("qty 缺失")
    qty = Decimal(str(qty_raw))

    time_raw = info.get("T")
    if time_raw is None:
        time_raw = trade.get("timestamp")
    if time_raw is None:
        raise ValueError("time 缺失")
    time_ms = int(time_raw)

    is_buyer_maker = _to_bool(info.get("m"))

    quote_qty = price * qty

    return UmTrade(
        symbol=symbol,
        id=trade_id_int,
        price=price,
        qty=qty,
        quote_qty=quote_qty,
        time=time_ms,
        is_buyer_maker=is_buyer_maker,
    )


def um_trade_to_csv_row(t: UmTrade) -> Tuple[str, str, str, str, str, str]:
    """对齐官方 CSV 字段顺序与表现形式。"""

    return (
        str(t.id),
        format_decimal_for_archive_csv(t.price),
        format_decimal_for_archive_csv(t.qty),
        format_decimal_for_archive_csv(t.quote_qty),
        str(t.time),
        "true" if t.is_buyer_maker else "false",
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


async def _watch_trades(
    exchange: Any,
    ccxt_symbol: str,
    archive_symbol: str,
    out_queue: "asyncio.Queue[UmTrade]",
    stats: _RealtimeStats,
) -> None:
    """单交易对 watchTrades 循环（WS 优先）。"""

    backoff = 1.0
    last_full_log = 0.0
    while True:
        try:
            trades = await exchange.watch_trades(ccxt_symbol)
            backoff = 1.0
            for tr in trades:
                try:
                    t = parse_um_trade_from_ccxt(tr, fallback_symbol=archive_symbol)
                except Exception as e:
                    stats.ws_parse_errors += 1
                    logger.warning("[%s] 解析 trade 失败: %s", archive_symbol, e)
                    continue
                if out_queue.full():
                    stats.queue_full_events += 1
                    now = time.time()
                    if (now - last_full_log) > 5.0:
                        last_full_log = now
                        logger.warning("[%s] out_queue 已满（阻塞背压中）: maxsize=%d", archive_symbol, out_queue.maxsize)
                await out_queue.put(t)
                stats.ws_trades_enqueued += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            stats.ws_watch_errors += 1
            logger.warning("[%s] watchTrades 失败，%.1fs 后重试: %s", archive_symbol, backoff, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


async def collect_realtime(
    *,
    symbols: Sequence[str],
    service_root: Path,
    database_url: str,
    write_csv: bool = True,
    write_db: bool = True,
    flush_max_rows: int = 2000,
    flush_interval_seconds: float = 1.0,
    window_seconds: int = 300,
    rest_overlap_multiplier: int = 3,
    gap_threshold_seconds: int = 30,
    gap_check_interval_seconds: float = 10.0,
) -> None:
    """实时采集 UM trades（WS 优先），并按官方目录落盘/入库。

    - service_root：服务根目录（用于把 rel_path 映射为本地路径：service_root/rel_path）
    """

    if ccxtpro is None:  # pragma: no cover
        raise RuntimeError("未安装 ccxtpro（import ccxt.pro 失败）")

    patch_ccxt_fast_client_for_aiohttp_313()

    if not symbols:
        raise ValueError("symbols 不能为空")

    if write_db and not database_url:
        raise ValueError("write_db=True 但 DATABASE_URL 为空")

    write_csv_requested = bool(write_csv)

    exchange = ccxtpro.binanceusdm(_build_ccxt_pro_exchange_config())

    conn = None
    trades_writer = None
    meta_writer: Optional[IngestMetaWriter] = None
    run_id: Optional[int] = None

    tasks: List[asyncio.Task] = []
    csv_buffer: Dict[Path, List[Tuple[str, str, str, str, str, str]]] = {}
    db_buffer: List[RawFuturesUmTradeRow] = []
    csv_rows_count = 0
    q: Optional[asyncio.Queue[UmTrade]] = None
    ccxt_symbol_map: Dict[str, str] = {}

    last_seen_time_ms: Dict[str, int] = {}
    last_seen_id: Dict[str, int] = {}
    inflight_rest: Dict[str, float] = {}
    run_status = "success"
    run_error: Optional[str] = None
    stats = _RealtimeStats()

    try:
        # 1) markets（用于 id->symbol 映射）
        await exchange.load_markets()

        # 2) 初始化 DB 写入器（可选）
        if write_db:
            conn = connect(database_url)
            trades_writer = RawFuturesUmTradesWriter(conn)
            meta_writer = IngestMetaWriter(conn)
            run_id = meta_writer.start_run(IngestRunSpec(exchange="binance", dataset="futures.um.trades", mode="realtime"))

        # 3) 启动 watch 任务
        q = asyncio.Queue(maxsize=100_000)

        for s in symbols:
            archive_symbol = s.upper()
            ccxt_symbol = _map_archive_symbol_to_ccxt(exchange, archive_symbol)
            logger.info("UM trades WS订阅: %s -> %s", archive_symbol, ccxt_symbol)
            ccxt_symbol_map[archive_symbol] = ccxt_symbol
            tasks.append(asyncio.create_task(_watch_trades(exchange, ccxt_symbol, archive_symbol, q, stats)))

        async def _rest_fill_symbol(archive_symbol: str, *, since_ms: int) -> None:
            if q is None:
                return

            ccxt_symbol = ccxt_symbol_map.get(archive_symbol)
            if not ccxt_symbol:
                return

            pages = 0
            max_pages = 10
            next_since = max(int(since_ms), 0)

            while pages < max_pages:
                pages += 1
                try:
                    params: Dict[str, Any] = {}
                    # 若实现支持 fromId，用它提升“同时间戳高频”场景的补齐确定性。
                    if archive_symbol in last_seen_id:
                        params["fromId"] = int(last_seen_id[archive_symbol]) + 1
                    try:
                        trades = await exchange.fetch_trades(ccxt_symbol, since=next_since, limit=1000, params=params)
                    except Exception:
                        # 部分实现不支持 fromId；降级仅用 since。
                        trades = await exchange.fetch_trades(ccxt_symbol, since=next_since, limit=1000)
                except Exception as e:
                    logger.warning("[%s] REST fetch_trades 失败: %s", archive_symbol, e)
                    return

                stats.rest_fetch_calls += 1
                if not trades:
                    return

                max_time = next_since
                for tr in trades:
                    try:
                        t = parse_um_trade_from_ccxt(tr, fallback_symbol=archive_symbol)
                    except Exception:
                        continue
                    if q.full():
                        stats.queue_full_events += 1
                    await q.put(t)
                    stats.rest_trades_enqueued += 1
                    max_time = max(max_time, t.time)

                if max_time <= next_since:
                    return
                next_since = max_time + 1

        async def _gap_watch_loop() -> None:
            w_ms = int(window_seconds) * 1000
            overlap_ms = int(window_seconds) * int(rest_overlap_multiplier) * 1000
            threshold_ms = int(gap_threshold_seconds) * 1000

            while True:
                await asyncio.sleep(max(float(gap_check_interval_seconds), 1.0))
                now_ms = int(time.time() * 1000)

                for archive_symbol in list(ccxt_symbol_map.keys()):
                    last_ms = last_seen_time_ms.get(archive_symbol)
                    if last_ms is None:
                        continue
                    if now_ms - last_ms < threshold_ms:
                        continue

                    # 防止同一个 symbol 在网络异常时反复触发 REST 补拉
                    last_trigger = inflight_rest.get(archive_symbol, 0.0)
                    if (time.time() - last_trigger) < max(5.0, float(gap_threshold_seconds) / 2):
                        continue
                    inflight_rest[archive_symbol] = time.time()

                    start_ms = max(0, last_ms - overlap_ms)
                    end_ms = now_ms

                    if meta_writer and run_id is not None:
                        meta_writer.insert_gap(
                            exchange="binance",
                            dataset="futures.um.trades",
                            symbol=archive_symbol,
                            start_time=start_ms,
                            end_time=end_ms,
                            reason=f"realtime_stale>{gap_threshold_seconds}s; rest_fill; W={window_seconds}s overlap={rest_overlap_multiplier}x",
                            run_id=run_id,
                        )
                        stats.gaps_inserted += 1

                    logger.info("[%s] 触发巡检补拉: stale=%ds since=%d", archive_symbol, (now_ms - last_ms) // 1000, start_ms)
                    await _rest_fill_symbol(archive_symbol, since_ms=max(now_ms - w_ms - overlap_ms, start_ms))

        tasks.append(asyncio.create_task(_gap_watch_loop()))

        # 4) 写入循环：按 rel_path 分组，批量落盘/入库
        last_flush = asyncio.get_event_loop().time()

        while True:
            try:
                t0 = await asyncio.wait_for(q.get(), timeout=flush_interval_seconds)

                batch: List[UmTrade] = [t0]
                drain_n = min(q.qsize(), 5000)
                for _ in range(drain_n):
                    try:
                        batch.append(q.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                stats.queue_max_size = max(stats.queue_max_size, q.qsize() + len(batch))

                for t in batch:
                    rel_path = relpath_futures_um_trades_daily(t.symbol, t.file_date)
                    local_path = service_root / rel_path

                    last_seen_time_ms[t.symbol] = t.time
                    last_seen_id[t.symbol] = t.id
                    stats.max_lag_ms = max(stats.max_lag_ms, int(time.time() * 1000) - int(t.time))

                    # ==================== 背压降级策略（P1） ====================
                    # 规则（强制）：
                    # - out_queue 为硬上限（maxsize），满时会阻塞，避免内存无限增长；
                    # - 当 backlog 逼近上限且同时开启 write_db+write_csv 时，优先保 DB，自动关闭 CSV 写入（文件可选，DB 是真相源）。
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
                        csv_buffer.setdefault(local_path, []).append(um_trade_to_csv_row(t))
                        csv_rows_count += 1

                    if write_db and trades_writer:
                        db_buffer.append(
                            RawFuturesUmTradeRow(
                                exchange="binance",
                                symbol=t.symbol,
                                id=t.id,
                                price=t.price,
                                qty=t.qty,
                                quote_qty=t.quote_qty,
                                time=t.time,
                                is_buyer_maker=t.is_buyer_maker,
                            )
                        )

                    # flush by size
                    if len(db_buffer) >= flush_max_rows or csv_rows_count >= flush_max_rows:
                        stats.csv_rows_written += sum(len(rows) for rows in csv_buffer.values())
                        stats.db_rows_attempted += len(db_buffer)
                        stats.db_rows_inserted += _flush(
                            csv_buffer,
                            db_buffer,
                            write_csv=write_csv,
                            write_db=write_db,
                            trades_writer=trades_writer,
                            meta_writer=meta_writer,
                            exchange="binance",
                            dataset="futures.um.trades",
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
                        trades_writer=trades_writer,
                        meta_writer=meta_writer,
                        exchange="binance",
                        dataset="futures.um.trades",
                    )
                    csv_rows_count = 0
                    last_flush = now

            except asyncio.TimeoutError:
                # flush by time
                now = asyncio.get_event_loop().time()
                if now - last_flush >= flush_interval_seconds:
                    stats.csv_rows_written += sum(len(rows) for rows in csv_buffer.values())
                    stats.db_rows_attempted += len(db_buffer)
                    stats.db_rows_inserted += _flush(
                        csv_buffer,
                        db_buffer,
                        write_csv=write_csv,
                        write_db=write_db,
                        trades_writer=trades_writer,
                        meta_writer=meta_writer,
                        exchange="binance",
                        dataset="futures.um.trades",
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
        logger.error("UM trades 实时采集异常: %s", e, exc_info=True)
        raise
    finally:
        # 先停采集，再尽力刷干净队列与缓冲区（避免丢数据）
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        try:
            if q is not None:
                while True:
                    try:
                        t = q.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    rel_path = relpath_futures_um_trades_daily(t.symbol, t.file_date)
                    local_path = service_root / rel_path

                    if write_csv:
                        csv_buffer.setdefault(local_path, []).append(um_trade_to_csv_row(t))
                        csv_rows_count += 1

                    if write_db and trades_writer:
                        db_buffer.append(
                            RawFuturesUmTradeRow(
                                exchange="binance",
                                symbol=t.symbol,
                                id=t.id,
                                price=t.price,
                                qty=t.qty,
                                quote_qty=t.quote_qty,
                                time=t.time,
                                is_buyer_maker=t.is_buyer_maker,
                            )
                        )

            stats.csv_rows_written += sum(len(rows) for rows in csv_buffer.values())
            stats.db_rows_attempted += len(db_buffer)
            stats.db_rows_inserted += _flush(
                csv_buffer,
                db_buffer,
                write_csv=write_csv,
                write_db=write_db,
                trades_writer=trades_writer,
                meta_writer=meta_writer,
                exchange="binance",
                dataset="futures.um.trades",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("退出前 flush 失败: %s", e)

        try:
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
                            "queue_maxsize": int(q.maxsize) if q is not None else None,
                            "ws_trades_enqueued": int(stats.ws_trades_enqueued),
                            "ws_parse_errors": int(stats.ws_parse_errors),
                            "ws_watch_errors": int(stats.ws_watch_errors),
                            "rest_fetch_calls": int(stats.rest_fetch_calls),
                            "rest_trades_enqueued": int(stats.rest_trades_enqueued),
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


def _flush(
    csv_buffer: Dict[Path, List[Tuple[str, str, str, str, str, str]]],
    db_buffer: List[RawFuturesUmTradeRow],
    *,
    write_csv: bool,
    write_db: bool,
    trades_writer: Optional[RawFuturesUmTradesWriter],
    meta_writer: Optional[IngestMetaWriter],
    exchange: str,
    dataset: str,
) -> int:
    """同步 flush（被 collect_realtime 调用）。"""

    inserted = 0

    if write_csv and csv_buffer:
        for path, rows in list(csv_buffer.items()):
            append_csv_rows(path, CSV_HEADER, rows)
        csv_buffer.clear()

    if write_db and trades_writer and db_buffer:
        attempted = int(len(db_buffer))
        inserted = int(trades_writer.insert_rows(db_buffer))
        logger.info("UM trades 入库 inserted=%d attempted=%d", inserted, attempted)
        if meta_writer:
            _update_watermark(meta_writer, db_buffer, exchange=exchange, dataset=dataset)
        db_buffer.clear()
    return int(inserted)


def _update_watermark(meta_writer: IngestMetaWriter, rows: List[RawFuturesUmTradeRow], *, exchange: str, dataset: str) -> None:
    if not rows:
        return

    by_symbol: Dict[str, tuple[int, int]] = {}
    for r in rows:
        last = by_symbol.get(r.symbol)
        pair = (int(r.time), int(r.id))
        if last is None or pair > last:
            by_symbol[r.symbol] = pair

    for sym, (last_time, last_id) in by_symbol.items():
        meta_writer.upsert_watermark(exchange=exchange, dataset=dataset, symbol=sym, last_time=last_time, last_id=last_id)


def collect() -> None:
    """最小入口（占位）：后续由 src.__main__ 路由到具体 collector 卡片。"""

    raise NotImplementedError("请从 src.__main__ 调用 collect_realtime，并传入 symbols/service_root/database_url")
