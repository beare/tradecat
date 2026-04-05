"""Futures UM / bookTicker（下载补齐，Raw/基元）

# 对齐官方目录语义
# - data/futures/um/daily/bookTicker/{SYMBOL}/{SYMBOL}-bookTicker-YYYY-MM-DD.zip
# - data/futures/um/monthly/bookTicker/{SYMBOL}/{SYMBOL}-bookTicker-YYYY-MM.zip
#
# CSV 样本字段
# - 列：update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,event_time
#
# 落库目标（Raw/物理层）
# - market.binance_futures_um_book_ticker
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import logging
from pathlib import Path
from typing import Optional
import zipfile

import psycopg

from binance.sources.archive_source.planner import build_plan
from binance.sources.archive_source.downloader import download_or_repair_zip
from binance.storage.ingest_meta import IngestMetaWriter, IngestRunSpec
from binance.storage.import_meta import ImportBatchSpec, ImportMetaWriter
from binance.storage.pg import connect
from binance.storage.storage_files import StorageFileSpec, StorageFilesWriter
from binance.storage.core_registry import CoreRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestZipStats:
    affected_rows: int
    file_rows: int
    file_min_event_time: Optional[int]
    file_max_event_time: Optional[int]
    file_max_update_id: Optional[int]


def _relpath_daily_zip(symbol: str, d: date) -> str:
    sym = symbol.upper()
    return f"downloads/futures/um/daily/bookTicker/{sym}/{sym}-bookTicker-{d:%Y-%m-%d}.zip"


def _relpath_monthly_zip(symbol: str, month: str) -> str:
    sym = symbol.upper()
    return f"downloads/futures/um/monthly/bookTicker/{sym}/{sym}-bookTicker-{month}.zip"


def _archive_relpath_daily_zip(symbol: str, d: date) -> str:
    sym = symbol.upper()
    return f"data/futures/um/daily/bookTicker/{sym}/{sym}-bookTicker-{d:%Y-%m-%d}.zip"


def _archive_relpath_monthly_zip(symbol: str, month: str) -> str:
    sym = symbol.upper()
    return f"data/futures/um/monthly/bookTicker/{sym}/{sym}-bookTicker-{month}.zip"


def _url_daily_zip(binance_data_base: str, symbol: str, d: date) -> str:
    sym = symbol.upper()
    return f"{binance_data_base}/data/futures/um/daily/bookTicker/{sym}/{sym}-bookTicker-{d:%Y-%m-%d}.zip"


def _url_monthly_zip(binance_data_base: str, symbol: str, month: str) -> str:
    sym = symbol.upper()
    return f"{binance_data_base}/data/futures/um/monthly/bookTicker/{sym}/{sym}-bookTicker-{month}.zip"


def _date_range_ms_utc(start_d: date, end_d: date) -> tuple[int, int]:
    start = datetime(start_d.year, start_d.month, start_d.day, tzinfo=timezone.utc)
    next_day = date.fromordinal(end_d.toordinal() + 1)
    end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _copy_zip_csv_into_tmp(cur: psycopg.Cursor, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        csv_members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_members:
            raise RuntimeError(f"ZIP 内未找到 CSV: {zip_path}")

        for name in csv_members:
            with zf.open(name) as fp:
                with cur.copy(
                    "COPY tmp_um_book_ticker (update_id, best_bid_price, best_bid_qty, best_ask_price, best_ask_qty, transaction_time, event_time) "
                    "FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')"
                ) as copy:
                    while True:
                        chunk = fp.read(1024 * 1024)
                        if not chunk:
                            break
                        copy.write(chunk)


def _ingest_zip(
    conn: psycopg.Connection,
    *,
    zip_path: Path,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> IngestZipStats:
    sym = str(symbol).upper()

    with conn.cursor() as cur:
        core = CoreRegistry(conn)
        venue_id, instrument_id = core.resolve_venue_and_instrument_id(
            venue_code="binance",
            symbol=sym,
            product="futures_um",
            cursor=cur,
        )

        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS tmp_um_book_ticker (
              update_id BIGINT NOT NULL,
              best_bid_price NUMERIC(38, 12) NOT NULL,
              best_bid_qty   NUMERIC(38, 12) NOT NULL,
              best_ask_price NUMERIC(38, 12) NOT NULL,
              best_ask_qty   NUMERIC(38, 12) NOT NULL,
              transaction_time BIGINT,
              event_time BIGINT NOT NULL
            ) ON COMMIT DROP
            """
        )
        cur.execute("TRUNCATE tmp_um_book_ticker")
        _copy_zip_csv_into_tmp(cur, zip_path)

        cur.execute(
            "SELECT COUNT(*), MIN(event_time), MAX(event_time), MAX(update_id) "
            "FROM tmp_um_book_ticker WHERE event_time >= %s AND event_time < %s",
            (int(start_ms), int(end_ms)),
        )
        tmp_count, tmp_min_event, tmp_max_event, tmp_max_update_id = cur.fetchone() or (0, None, None, None)

        cur.execute(
            """
            INSERT INTO market.binance_futures_um_book_ticker (
              venue_id,
              instrument_id,
              update_id,
              best_bid_price,
              best_bid_qty,
              best_ask_price,
              best_ask_qty,
              transaction_time,
              event_time
            )
            SELECT
              %(venue_id)s,
              %(instrument_id)s,
              update_id,
              best_bid_price::double precision,
              best_bid_qty::double precision,
              best_ask_price::double precision,
              best_ask_qty::double precision,
              transaction_time,
              event_time
            FROM tmp_um_book_ticker
            WHERE event_time >= %(start_ms)s AND event_time < %(end_ms)s
            ON CONFLICT (venue_id, instrument_id, event_time, update_id) DO NOTHING
            """,
            {"venue_id": int(venue_id), "instrument_id": int(instrument_id), "start_ms": int(start_ms), "end_ms": int(end_ms)},
        )
        affected_rows = int(cur.rowcount or 0)

        cur.execute(
            """
            SELECT COUNT(*)
            FROM market.binance_futures_um_book_ticker
            WHERE venue_id = %s
              AND instrument_id = %s
              AND event_time >= %s
              AND event_time <  %s
            """,
            (int(venue_id), int(instrument_id), int(start_ms), int(end_ms)),
        )
        fact_count = int((cur.fetchone() or (0,))[0])
        if tmp_count and fact_count < int(tmp_count):
            logger.warning("[%s] 对账异常: file_rows=%d fact_rows=%d zip=%s", sym, int(tmp_count), fact_count, zip_path.name)

        return IngestZipStats(
            affected_rows=affected_rows,
            file_rows=int(tmp_count or 0),
            file_min_event_time=int(tmp_min_event) if tmp_min_event is not None else None,
            file_max_event_time=int(tmp_max_event) if tmp_max_event is not None else None,
            file_max_update_id=int(tmp_max_update_id) if tmp_max_update_id is not None else None,
        )


def download_and_ingest(
    *,
    symbols: list[str],
    start_date: date,
    end_date: date,
    service_root: Path,
    database_url: str,
    binance_data_base: str,
    write_files: bool,
    write_db: bool,
    prefer_monthly: bool,
    allow_no_checksum: bool = False,
) -> dict[str, str]:
    if not symbols:
        raise ValueError("symbols 不能为空")
    if start_date > end_date:
        raise ValueError("start_date 不能大于 end_date")
    if write_db and not database_url:
        raise ValueError("write_db=True 但 DATABASE_URL 为空")

    dataset = "futures.um.bookTicker"
    conn = connect(database_url) if write_db else None
    import_writer = ImportMetaWriter(conn) if conn is not None else None
    storage_writer = StorageFilesWriter(conn) if conn is not None else None
    batch_id: Optional[int] = None
    symbol_statuses: dict[str, str] = {}

    if import_writer:
        batch_id = import_writer.start_batch(
            ImportBatchSpec(
                source="binance_archive",
                note="binance_archive futures/um bookTicker backfill",
                meta={
                    "dataset": dataset,
                    "symbols": [s.upper() for s in symbols],
                    "start_date": f"{start_date:%Y-%m-%d}",
                    "end_date": f"{end_date:%Y-%m-%d}",
                    "prefer_monthly": bool(prefer_monthly),
                    "allow_no_checksum": bool(allow_no_checksum),
                },
            )
        )

    try:
        for sym in symbols:
            meta_writer = IngestMetaWriter(conn) if conn is not None else None
            run_id = None
            if meta_writer:
                run_id = meta_writer.start_run(IngestRunSpec(exchange="binance", dataset=dataset, mode="backfill"))

            plan = build_plan(sym, start_date, end_date, prefer_monthly=prefer_monthly)
            logger.info("[%s] 回填计划: %d 个任务（prefer_monthly=%s）", sym, len(plan), prefer_monthly)

            run_meta = {
                "symbols": [str(sym).upper()],
                "dataset": dataset,
                "start_date": f"{start_date:%Y-%m-%d}",
                "end_date": f"{end_date:%Y-%m-%d}",
                "prefer_monthly": bool(prefer_monthly),
                "allow_no_checksum": bool(allow_no_checksum),
                "write_files": bool(write_files),
                "write_db": bool(write_db),
                "plan_items": len(plan),
                "plan_daily": sum(1 for it in plan if it.kind == "daily"),
                "plan_monthly": sum(1 for it in plan if it.kind == "monthly"),
                "download_ok": 0,
                "download_failed": 0,
                "monthly_404": 0,
                "ingest_ok": 0,
                "ingest_failed": 0,
                "file_rows_total": 0,
                "affected_rows_total": 0,
            }

            status = "success"
            error_message = None

            try:
                i = 0
                while i < len(plan):
                    item = plan[i]
                    i += 1

                    if item.kind == "daily":
                        d = item.start_date
                        rel = _relpath_daily_zip(sym, d)
                        archive_rel = _archive_relpath_daily_zip(sym, d)
                        url = _url_daily_zip(binance_data_base, sym, d)
                        dst = (service_root / rel) if write_files else (service_root / "run" / "tmp_download" / rel)

                        old_checksum = import_writer.get_existing_checksum(archive_rel) if import_writer else None
                        r = download_or_repair_zip(
                            url, dst, allow_no_checksum=allow_no_checksum, timeout_seconds=60.0, max_retries=3
                        )
                        if not r.ok:
                            logger.warning("[%s] 日度下载失败: %s (status=%s error=%s)", sym, url, r.status_code, r.error)
                            run_meta["download_failed"] = int(run_meta["download_failed"]) + 1
                            status = "partial"

                            file_id = None
                            if storage_writer:
                                file_id = storage_writer.get_or_create_file_id(
                                    StorageFileSpec(
                                        rel_path=archive_rel,
                                        content_kind="zip",
                                        source="binance_archive",
                                        market_root="crypto",
                                        market="futures",
                                        product="um",
                                        frequency="daily",
                                        dataset="bookTicker",
                                        symbol=sym.upper(),
                                        interval=None,
                                        file_date=d,
                                        file_month=None,
                                        size_bytes=dst.stat().st_size if dst.exists() else None,
                                        checksum_sha256=r.checksum_sha256,
                                        downloaded_at=datetime.now(tz=timezone.utc),
                                        meta={"verified": bool(r.verified), "url": url, "local_path": str(dst), "error": r.error},
                                    )
                                )

                            if import_writer:
                                import_writer.insert_import_error(
                                    batch_id=batch_id,
                                    file_id=file_id,
                                    error_type="download_failed",
                                    message=str(r.error or "download_failed"),
                                    meta={"url": url, "archive_rel_path": archive_rel, "status_code": r.status_code},
                                )
                            continue

                        run_meta["download_ok"] = int(run_meta["download_ok"]) + 1

                        file_id = None
                        if storage_writer:
                            file_id = storage_writer.get_or_create_file_id(
                                StorageFileSpec(
                                    rel_path=archive_rel,
                                    content_kind="zip",
                                    source="binance_archive",
                                    market_root="crypto",
                                    market="futures",
                                    product="um",
                                    frequency="daily",
                                    dataset="bookTicker",
                                    symbol=sym.upper(),
                                    interval=None,
                                    file_date=d,
                                    file_month=None,
                                    size_bytes=dst.stat().st_size if dst.exists() else None,
                                    checksum_sha256=r.checksum_sha256,
                                    downloaded_at=datetime.now(tz=timezone.utc),
                                    meta={"verified": bool(r.verified), "url": url, "local_path": str(dst)},
                                )
                            )

                        if import_writer and r.checksum_sha256:
                            import_writer.insert_file_revision(
                                rel_path=archive_rel,
                                old_checksum_sha256=old_checksum,
                                new_checksum_sha256=r.checksum_sha256,
                                note="download_and_ingest futures/um bookTicker (daily)",
                            )

                        if write_db and conn is not None:
                            if file_id is None:
                                raise RuntimeError("write_db=True 但 file_id 为空（market.shared_files 未写入）")

                            start_ms, end_ms = _date_range_ms_utc(item.start_date, item.end_date)
                            try:
                                stats = _ingest_zip(
                                    conn,
                                    zip_path=dst,
                                    symbol=sym,
                                    start_ms=start_ms,
                                    end_ms=end_ms,
                                )
                            except Exception as e:  # noqa: BLE001
                                run_meta["ingest_failed"] = int(run_meta["ingest_failed"]) + 1
                                status = "partial"
                                if import_writer:
                                    import_writer.insert_import_error(
                                        batch_id=batch_id,
                                        file_id=file_id,
                                        error_type="ingest_failed",
                                        message=str(e),
                                        meta={"url": url, "archive_rel_path": archive_rel},
                                    )
                                raise

                            conn.commit()
                            run_meta["ingest_ok"] = int(run_meta["ingest_ok"]) + 1
                            run_meta["file_rows_total"] = int(run_meta["file_rows_total"]) + int(stats.file_rows)
                            run_meta["affected_rows_total"] = int(run_meta["affected_rows_total"]) + int(stats.affected_rows)
                            logger.info(
                                "[%s] %s 入库完成: affected=%d file_rows=%d",
                                sym,
                                item.period,
                                stats.affected_rows,
                                stats.file_rows,
                            )

                            if meta_writer and stats.file_max_event_time is not None:
                                meta_writer.upsert_watermark(
                                    exchange="binance",
                                    dataset=dataset,
                                    symbol=sym,
                                    last_time=int(stats.file_max_event_time),
                                    last_id=int(stats.file_max_update_id or 0),
                                )

                            if storage_writer:
                                storage_writer.get_or_create_file_id(
                                    StorageFileSpec(
                                        rel_path=archive_rel,
                                        content_kind="zip",
                                        source="binance_archive",
                                        market_root="crypto",
                                        market="futures",
                                        product="um",
                                        frequency="daily",
                                        dataset="bookTicker",
                                        symbol=sym.upper(),
                                        interval=None,
                                        file_date=d,
                                        file_month=None,
                                        extracted_at=datetime.now(tz=timezone.utc),
                                        row_count=stats.file_rows,
                                        min_event_ts=(
                                            datetime.fromtimestamp(stats.file_min_event_time / 1000.0, tz=timezone.utc)
                                            if stats.file_min_event_time is not None
                                            else None
                                        ),
                                        max_event_ts=(
                                            datetime.fromtimestamp(stats.file_max_event_time / 1000.0, tz=timezone.utc)
                                            if stats.file_max_event_time is not None
                                            else None
                                        ),
                                        meta={"file_rows": stats.file_rows},
                                    )
                                )

                        if not write_files:
                            try:
                                dst.unlink()
                            except Exception:
                                pass
                        continue

                    if item.kind == "monthly":
                        rel = _relpath_monthly_zip(sym, item.period)
                        archive_rel = _archive_relpath_monthly_zip(sym, item.period)
                        url = _url_monthly_zip(binance_data_base, sym, item.period)
                        dst = (service_root / rel) if write_files else (service_root / "run" / "tmp_download" / rel)

                        old_checksum = import_writer.get_existing_checksum(archive_rel) if import_writer else None
                        r = download_or_repair_zip(
                            url, dst, allow_no_checksum=allow_no_checksum, timeout_seconds=60.0, max_retries=3
                        )
                        if not r.ok and int(r.status_code or 0) == 404:
                            logger.info("[%s] 月度不存在，降级按日: %s", sym, item.period)
                            run_meta["monthly_404"] = int(run_meta["monthly_404"]) + 1
                            daily = build_plan(sym, item.start_date, item.end_date, prefer_monthly=False)
                            plan[i:i] = daily
                            continue
                        if not r.ok:
                            logger.warning("[%s] 月度下载失败: %s (status=%s error=%s)", sym, url, r.status_code, r.error)
                            run_meta["download_failed"] = int(run_meta["download_failed"]) + 1
                            status = "partial"
                            month_date = date.fromisoformat(f"{item.period}-01")

                            file_id = None
                            if storage_writer:
                                file_id = storage_writer.get_or_create_file_id(
                                    StorageFileSpec(
                                        rel_path=archive_rel,
                                        content_kind="zip",
                                        source="binance_archive",
                                        market_root="crypto",
                                        market="futures",
                                        product="um",
                                        frequency="monthly",
                                        dataset="bookTicker",
                                        symbol=sym.upper(),
                                        interval=None,
                                        file_date=None,
                                        file_month=month_date,
                                        size_bytes=dst.stat().st_size if dst.exists() else None,
                                        checksum_sha256=r.checksum_sha256,
                                        downloaded_at=datetime.now(tz=timezone.utc),
                                        meta={"verified": bool(r.verified), "url": url, "local_path": str(dst), "error": r.error},
                                    )
                                )

                            if import_writer:
                                import_writer.insert_import_error(
                                    batch_id=batch_id,
                                    file_id=file_id,
                                    error_type="download_failed",
                                    message=str(r.error or "download_failed"),
                                    meta={"url": url, "archive_rel_path": archive_rel, "status_code": r.status_code},
                                )
                            continue

                        run_meta["download_ok"] = int(run_meta["download_ok"]) + 1

                        month_date = date.fromisoformat(f"{item.period}-01")
                        file_id = None
                        if storage_writer:
                            file_id = storage_writer.get_or_create_file_id(
                                StorageFileSpec(
                                    rel_path=archive_rel,
                                    content_kind="zip",
                                    source="binance_archive",
                                    market_root="crypto",
                                    market="futures",
                                    product="um",
                                    frequency="monthly",
                                    dataset="bookTicker",
                                    symbol=sym.upper(),
                                    interval=None,
                                    file_date=None,
                                    file_month=month_date,
                                    size_bytes=dst.stat().st_size if dst.exists() else None,
                                    checksum_sha256=r.checksum_sha256,
                                    downloaded_at=datetime.now(tz=timezone.utc),
                                    meta={"verified": bool(r.verified), "url": url, "local_path": str(dst)},
                                )
                            )

                        if import_writer and r.checksum_sha256:
                            import_writer.insert_file_revision(
                                rel_path=archive_rel,
                                old_checksum_sha256=old_checksum,
                                new_checksum_sha256=r.checksum_sha256,
                                note="download_and_ingest futures/um bookTicker (monthly)",
                            )

                        if write_db and conn is not None:
                            if file_id is None:
                                raise RuntimeError("write_db=True 但 file_id 为空（market.shared_files 未写入）")
                            start_ms, end_ms = _date_range_ms_utc(item.start_date, item.end_date)
                            try:
                                stats = _ingest_zip(
                                    conn,
                                    zip_path=dst,
                                    symbol=sym,
                                    start_ms=start_ms,
                                    end_ms=end_ms,
                                )
                            except Exception as e:  # noqa: BLE001
                                run_meta["ingest_failed"] = int(run_meta["ingest_failed"]) + 1
                                status = "partial"
                                if import_writer:
                                    import_writer.insert_import_error(
                                        batch_id=batch_id,
                                        file_id=file_id,
                                        error_type="ingest_failed",
                                        message=str(e),
                                        meta={"url": url, "archive_rel_path": archive_rel},
                                    )
                                raise

                            conn.commit()
                            run_meta["ingest_ok"] = int(run_meta["ingest_ok"]) + 1
                            run_meta["file_rows_total"] = int(run_meta["file_rows_total"]) + int(stats.file_rows)
                            run_meta["affected_rows_total"] = int(run_meta["affected_rows_total"]) + int(stats.affected_rows)
                            logger.info(
                                "[%s] %s 入库完成: affected=%d file_rows=%d",
                                sym,
                                item.period,
                                stats.affected_rows,
                                stats.file_rows,
                            )

                            if meta_writer and stats.file_max_event_time is not None:
                                meta_writer.upsert_watermark(
                                    exchange="binance",
                                    dataset=dataset,
                                    symbol=sym,
                                    last_time=int(stats.file_max_event_time),
                                    last_id=int(stats.file_max_update_id or 0),
                                )

                            if storage_writer:
                                storage_writer.get_or_create_file_id(
                                    StorageFileSpec(
                                        rel_path=archive_rel,
                                        content_kind="zip",
                                        source="binance_archive",
                                        market_root="crypto",
                                        market="futures",
                                        product="um",
                                        frequency="monthly",
                                        dataset="bookTicker",
                                        symbol=sym.upper(),
                                        interval=None,
                                        file_date=None,
                                        file_month=month_date,
                                        extracted_at=datetime.now(tz=timezone.utc),
                                        row_count=stats.file_rows,
                                        min_event_ts=(
                                            datetime.fromtimestamp(stats.file_min_event_time / 1000.0, tz=timezone.utc)
                                            if stats.file_min_event_time is not None
                                            else None
                                        ),
                                        max_event_ts=(
                                            datetime.fromtimestamp(stats.file_max_event_time / 1000.0, tz=timezone.utc)
                                            if stats.file_max_event_time is not None
                                            else None
                                        ),
                                        meta={"file_rows": stats.file_rows},
                                    )
                                )

                        if not write_files:
                            try:
                                dst.unlink()
                            except Exception:
                                pass
                        continue

                    raise RuntimeError(f"未知 plan kind: {item.kind}")
            except Exception as e:  # noqa: BLE001
                status = "failed"
                error_message = str(e)
                raise
            finally:
                if meta_writer and run_id is not None:
                    try:
                        meta_writer.finish_run(run_id, status=status, error_message=error_message, meta=run_meta)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("[%s] 写入 ingest_runs 结束状态失败: %s", sym, e)

            symbol_statuses[str(sym).upper()] = status
    finally:
        if import_writer and batch_id is not None:
            overall = "success" if all(v == "success" for v in symbol_statuses.values()) else "partial"
            import_writer.finish_batch(batch_id, status=overall, meta={"symbols": symbol_statuses})
        if conn is not None:
            conn.close()

    return symbol_statuses


__all__ = ["download_and_ingest"]
