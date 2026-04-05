"""Futures UM / trades（下载回填，原子事实）

你现在的目标是：
- 历史：以 Binance 官方历史归档源 daily/monthly ZIP 为“权威来源”做全量补齐
- 实时：以 ccxtpro.watchTrades 为主，并用巡检+REST补拉兜底
- 两条链路都写入同一张事实表 `market.binance_futures_um_trades`（幂等去重）

# ==================== 对齐官方目录语义 ====================
# - daily ZIP：
#   data/futures/um/daily/trades/{SYMBOL}/{SYMBOL}-trades-YYYY-MM-DD.zip
# - monthly ZIP：
#   data/futures/um/monthly/trades/{SYMBOL}/{SYMBOL}-trades-YYYY-MM.zip
#
# ==================== “智能选择”（避免重复与无意义下载） ====================
# - month 完整覆盖（且不是当前月）→ 优先 monthly ZIP（一个月一个文件）
# - 边界月/当前月/禁用 monthly → 按日 daily ZIP
#
# 说明：daily 与 monthly 的数据本质上是同一事实集合；如果两者都导入，会产生重复写入压力。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timezone
import logging
import multiprocessing
import os
from pathlib import Path
from typing import Optional
import zipfile

import psycopg

from binance.sources.archive_source.download_utils import (
    download_file,
    download_text,
    parse_checksum_text,
    probe_content_length,
    sha256_file,
)
from binance.storage.core_registry import CoreRegistry
from binance.storage.pg import connect
from binance.storage.ingest_meta import IngestMetaWriter, IngestRunSpec
from binance.storage.import_meta import ImportBatchSpec, ImportMetaWriter
from binance.storage.storage_files import StorageFileSpec, StorageFilesWriter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PlanItem:
    symbol: str
    kind: str  # "daily" | "monthly"
    period: str  # YYYY-MM 或 YYYY-MM-DD
    start_date: date
    end_date: date


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_end(d: date) -> date:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)


def _iter_months(start: date, end: date) -> list[date]:
    cur = _month_start(start)
    last = _month_start(end)
    out: list[date] = []
    while cur <= last:
        out.append(cur)
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


def _build_plan(symbol: str, start_date: date, end_date: date, *, prefer_monthly: bool) -> list[_PlanItem]:
    if start_date > end_date:
        raise ValueError("start_date 不能大于 end_date")

    today_utc = datetime.now(tz=timezone.utc).date()
    current_month = _month_start(today_utc)

    items: list[_PlanItem] = []
    for m in _iter_months(start_date, end_date):
        m_start = m
        m_end = _month_end(m)
        want_start = max(start_date, m_start)
        want_end = min(end_date, m_end)

        full_month = want_start == m_start and want_end == m_end
        if not prefer_monthly or not full_month or m == current_month:
            d = want_start
            while d <= want_end:
                items.append(
                    _PlanItem(
                        symbol=symbol,
                        kind="daily",
                        period=f"{d:%Y-%m-%d}",
                        start_date=d,
                        end_date=d,
                    )
                )
                d = date.fromordinal(d.toordinal() + 1)
            continue

        items.append(
            _PlanItem(
                symbol=symbol,
                kind="monthly",
                period=f"{m_start:%Y-%m}",
                start_date=want_start,
                end_date=want_end,
            )
        )

    return items


def _relpath_daily_zip(symbol: str, d: date) -> str:
    sym = symbol.upper()
    return f"downloads/futures/um/daily/trades/{sym}/{sym}-trades-{d:%Y-%m-%d}.zip"


def _relpath_monthly_zip(symbol: str, month: str) -> str:
    sym = symbol.upper()
    return f"downloads/futures/um/monthly/trades/{sym}/{sym}-trades-{month}.zip"


def _archive_relpath_daily_zip(symbol: str, d: date) -> str:
    sym = symbol.upper()
    return f"data/futures/um/daily/trades/{sym}/{sym}-trades-{d:%Y-%m-%d}.zip"


def _archive_relpath_monthly_zip(symbol: str, month: str) -> str:
    sym = symbol.upper()
    return f"data/futures/um/monthly/trades/{sym}/{sym}-trades-{month}.zip"


def _url_daily_zip(binance_data_base: str, symbol: str, d: date) -> str:
    sym = symbol.upper()
    return f"{binance_data_base}/data/futures/um/daily/trades/{sym}/{sym}-trades-{d:%Y-%m-%d}.zip"


def _url_monthly_zip(binance_data_base: str, symbol: str, month: str) -> str:
    sym = symbol.upper()
    return f"{binance_data_base}/data/futures/um/monthly/trades/{sym}/{sym}-trades-{month}.zip"


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
                    "COPY tmp_um_trades (id, price, qty, quote_qty, time, is_buyer_maker) FROM STDIN WITH (FORMAT csv)"
                ) as copy:
                    first = fp.readline()
                    if first:
                        first_field = first.split(b",", 1)[0].strip().lower()
                        has_header = first_field in {b"id", b"tradeid"}
                        if not has_header:
                            copy.write(first)
                    while True:
                        chunk = fp.read(1024 * 1024)
                        if not chunk:
                            break
                        copy.write(chunk)


def _zip_has_csv(zip_path: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            return any(n.lower().endswith(".csv") for n in names)
    except Exception:
        return False


@dataclass(frozen=True)
class VerifiedZipResult:
    ok: bool
    status_code: Optional[int]
    error: Optional[str]
    checksum_sha256: Optional[str]
    verified: bool


def _checksum_url(zip_url: str) -> str:
    return f"{zip_url}.CHECKSUM"


def _download_or_repair_zip(
    url: str,
    dst: Path,
    *,
    allow_no_checksum: bool,
    timeout_seconds: float = 60.0,
    max_retries: int = 3,
) -> VerifiedZipResult:
    zip_filename = dst.name
    checksum_url = _checksum_url(url)

    checksum_resp = download_text(checksum_url, timeout_seconds=timeout_seconds, max_retries=max_retries)
    expected_sha: Optional[str] = None
    verified = False

    if checksum_resp.ok:
        mapping = parse_checksum_text(checksum_resp.text or "")
        expected_sha = mapping.get(zip_filename)
        if not expected_sha:
            return VerifiedZipResult(
                ok=False,
                status_code=200,
                error=f"CHECKSUM 未包含目标文件名: {zip_filename}",
                checksum_sha256=None,
                verified=False,
            )
        verified = True
    else:
        if checksum_resp.status_code == 404 and allow_no_checksum:
            expected_sha = None
            verified = False
        else:
            return VerifiedZipResult(
                ok=False,
                status_code=checksum_resp.status_code,
                error=f"CHECKSUM 下载失败: {checksum_resp.error}",
                checksum_sha256=None,
                verified=False,
            )

    if dst.exists():
        if _zip_has_csv(dst):
            if expected_sha:
                local_sha = sha256_file(dst)
                if local_sha == expected_sha:
                    return VerifiedZipResult(ok=True, status_code=200, error=None, checksum_sha256=expected_sha, verified=True)
                logger.warning("发现 sha256 不一致 ZIP，准备重下: %s", dst)
            else:
                # 无 checksum 时退化为“大小一致”快速校验（不可靠，但用于逃生模式）。
                remote_size = probe_content_length(url, timeout_seconds=timeout_seconds)
                if remote_size is None:
                    local_sha = sha256_file(dst)
                    return VerifiedZipResult(ok=True, status_code=200, error=None, checksum_sha256=local_sha, verified=False)
                local_size = dst.stat().st_size
                if local_size == remote_size:
                    local_sha = sha256_file(dst)
                    return VerifiedZipResult(ok=True, status_code=200, error=None, checksum_sha256=local_sha, verified=False)
                logger.warning("发现大小不一致 ZIP，准备重下: %s (local=%d remote=%d)", dst, local_size, remote_size)
        else:
            logger.warning("发现损坏 ZIP，准备重下: %s", dst)

        try:
            dst.unlink()
        except Exception as e:
            return VerifiedZipResult(ok=False, status_code=None, error=f"删除损坏文件失败: {e}", checksum_sha256=None, verified=False)

    for attempt in range(max_retries):
        r = download_file(url, dst, timeout_seconds=timeout_seconds, max_retries=max_retries)
        if not r.ok:
            return VerifiedZipResult(ok=False, status_code=r.status_code, error=r.error, checksum_sha256=None, verified=False)

        if not _zip_has_csv(dst):
            try:
                dst.unlink()
            except Exception:
                pass
            return VerifiedZipResult(ok=False, status_code=None, error="下载后 ZIP 校验失败（无 CSV 或文件损坏）", checksum_sha256=None, verified=False)

        local_sha = sha256_file(dst)
        if expected_sha and local_sha != expected_sha:
            logger.warning("sha256 校验失败，准备重下: %s (attempt=%d/%d)", dst, attempt + 1, max_retries)
            try:
                dst.unlink()
            except Exception:
                pass
            continue

        return VerifiedZipResult(ok=True, status_code=200, error=None, checksum_sha256=expected_sha or local_sha, verified=verified)

    return VerifiedZipResult(ok=False, status_code=None, error="sha256 校验失败且重试耗尽", checksum_sha256=None, verified=False)


@dataclass(frozen=True)
class IngestZipStats:
    affected_rows: int
    file_rows: int
    file_min_time: Optional[int]
    file_max_time: Optional[int]
    file_max_id: Optional[int]
    chunks_decompressed: int = 0
    chunks_recompressed: int = 0


def _verify_local_zip(dst: Path) -> VerifiedZipResult:
    if not dst.exists():
        return VerifiedZipResult(ok=False, status_code=None, error=f"本地文件不存在: {dst}", checksum_sha256=None, verified=False)
    if not _zip_has_csv(dst):
        return VerifiedZipResult(ok=False, status_code=None, error=f"本地 ZIP 损坏（无 CSV）: {dst}", checksum_sha256=None, verified=False)
    local_sha = sha256_file(dst)
    return VerifiedZipResult(ok=True, status_code=200, error=None, checksum_sha256=local_sha, verified=False)


def _apply_fast_ingest_session_settings(cur: psycopg.Cursor) -> None:
    # 默认“更稳”：保证崩溃/断电时已提交数据尽量不丢。
    # 若你明确要极致速度，可设置环境变量 `TC_UNSAFE_FAST_INGEST=1`（可能在崩溃时丢失最近一小段已提交事务）。
    if str(os.environ.get("TC_UNSAFE_FAST_INGEST", "")).strip().lower() in {"1", "true", "yes"}:
        cur.execute("SET synchronous_commit = off")
    cur.execute("SET wal_compression = on")
    cur.execute("SET timescaledb.max_open_chunks_per_insert = 64")
    cur.execute("SET work_mem = '64MB'")


def _should_skip_local_ingest(cur: psycopg.Cursor, *, rel_path: str) -> bool:
    cur.execute(
        """
        SELECT extracted_at, row_count
        FROM market.shared_files
        WHERE rel_path = %(rel_path)s
        """,
        {"rel_path": str(rel_path)},
    )
    row = cur.fetchone()
    if not row:
        return False
    extracted_at, row_count = row[0], row[1]
    return extracted_at is not None and row_count is not None


def _partition_local_tasks_by_size(tasks: list[_PlanItem], *, service_root: Path, workers: int) -> list[list[_PlanItem]]:
    if workers <= 1:
        return [list(tasks)]

    weighted: list[tuple[int, _PlanItem]] = []
    for it in tasks:
        if it.kind == "daily":
            rel = _relpath_daily_zip(it.symbol, it.start_date)
        else:
            rel = _relpath_monthly_zip(it.symbol, it.period)
        p = service_root / rel
        size = int(p.stat().st_size) if p.exists() else 0
        weighted.append((size, it))

    weighted.sort(key=lambda x: x[0], reverse=True)
    buckets: list[list[_PlanItem]] = [[] for _ in range(int(workers))]
    bucket_sizes = [0 for _ in range(int(workers))]
    for size, it in weighted:
        idx = min(range(int(workers)), key=lambda i: bucket_sizes[i])
        buckets[idx].append(it)
        bucket_sizes[idx] += int(size)
    return [b for b in buckets if b]


def _ingest_local_plan_items_worker(
    *,
    worker_id: int,
    tasks: list[_PlanItem],
    service_root: Path,
    database_url: str,
    force_update: bool,
) -> dict[str, object]:
    dataset = "futures.um.trades"

    if not tasks:
        return {"worker_id": int(worker_id), "status": "success", "planned": 0, "ingested": 0, "skipped": 0, "failed": 0}

    conn = connect(database_url)
    try:
        with conn.cursor() as cur:
            _apply_fast_ingest_session_settings(cur)
        conn.commit()

        compress_after_ms: Optional[int] = None
        try:
            with conn.cursor() as cur:
                compress_after_ms = _get_um_trades_compress_after_ms(cur)
        except Exception:
            compress_after_ms = None
        if compress_after_ms is None:
            logger.warning("local-only: 读取 compress_after 失败，将禁用自动压缩（依赖后续手工/策略 job）")

        core_registry = CoreRegistry(conn)
        meta_writer = IngestMetaWriter(conn)
        import_writer = ImportMetaWriter(conn)
        storage_writer = StorageFilesWriter(conn)

        batch_id = import_writer.start_batch(
            ImportBatchSpec(
                source="binance_archive",
                note=f"binance_archive um trades local-only (worker={worker_id})",
                meta={"dataset": dataset, "worker_id": int(worker_id), "planned": len(tasks)},
            )
        )

        run_id = meta_writer.start_run(IngestRunSpec(exchange="binance", dataset=dataset, mode="backfill"))

        run_meta = {
            "dataset": dataset,
            "mode": "backfill",
            "local_only": True,
            "worker_id": int(worker_id),
            "planned": int(len(tasks)),
            "ingest_ok": 0,
            "ingest_failed": 0,
            "skip_existing": 0,
            "skip_existing_local_missing": 0,
            "file_rows_total": 0,
            "affected_rows_total": 0,
            "chunks_decompressed_total": 0,
            "chunks_recompressed_total": 0,
            "chunks_compressed_total": 0,
            "chunks_compress_failed_total": 0,
        }

        status = "success"
        error_message = None

        for item in tasks:
            sym = item.symbol.upper()

            if item.kind == "daily":
                rel = _relpath_daily_zip(sym, item.start_date)
                archive_rel = _archive_relpath_daily_zip(sym, item.start_date)
            elif item.kind == "monthly":
                rel = _relpath_monthly_zip(sym, item.period)
                archive_rel = _archive_relpath_monthly_zip(sym, item.period)
            else:
                raise RuntimeError(f"未知 plan item kind: {item.kind}")

            dst = service_root / rel
            start_ms, end_ms = _date_range_ms_utc(item.start_date, item.end_date)

            with conn.cursor() as cur:
                if _should_skip_local_ingest(cur, rel_path=archive_rel):
                    logger.info("[%s] local-only: 已入库，跳过: %s", sym, archive_rel)
                    if dst.exists():
                        run_meta["skip_existing"] = int(run_meta["skip_existing"]) + 1
                    else:
                        logger.warning("[%s] local-only: 已入库但本地 ZIP 缺失（离线不可复现）: %s", sym, dst)
                        run_meta["skip_existing_local_missing"] = int(run_meta["skip_existing_local_missing"]) + 1

                    # 已入库但未压缩的场景：允许重跑时补做压缩，避免后续爆盘。
                    if compress_after_ms is not None:
                        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                        threshold_ms = int(now_ms) - int(compress_after_ms)
                        try:
                            chunks = _list_um_trades_uncompressed_chunks_older_than(
                                cur,
                                start_ms=int(start_ms),
                                end_ms=int(end_ms),
                                older_than_end_ms=int(threshold_ms),
                            )
                            for ch in chunks:
                                try:
                                    cur.execute("SELECT compress_chunk(%s::regclass)", (str(ch),))
                                    run_meta["chunks_compressed_total"] = int(run_meta["chunks_compressed_total"]) + 1
                                except Exception as e:  # noqa: BLE001
                                    run_meta["chunks_compress_failed_total"] = int(run_meta["chunks_compress_failed_total"]) + 1
                                    logger.warning("[%s] local-only: compress_chunk 失败（chunk=%s）: %s", sym, ch, e)
                            conn.commit()
                        except Exception as e:  # noqa: BLE001
                            logger.warning("[%s] local-only: 自动压缩失败（skip path）: %s", sym, e)
                    continue

            r = _verify_local_zip(dst)
            if not r.ok:
                logger.warning("[%s] local-only: 跳过（文件不可用）: %s", sym, r.error or dst)
                run_meta["ingest_failed"] = int(run_meta["ingest_failed"]) + 1
                status = "partial"
                storage_writer.get_or_create_file_id(
                    StorageFileSpec(
                        rel_path=archive_rel,
                        content_kind="zip",
                        source="binance_archive",
                        market_root="crypto",
                        market="futures",
                        product="um",
                        frequency=item.kind,
                        dataset="trades",
                        symbol=sym,
                        interval=None,
                        file_date=item.start_date if item.kind == "daily" else None,
                        file_month=date.fromisoformat(f"{item.period}-01") if item.kind == "monthly" else None,
                        size_bytes=dst.stat().st_size if dst.exists() else None,
                        checksum_sha256=None,
                        downloaded_at=None,
                        extracted_at=None,
                        meta={"local_only": True, "local_path": str(dst), "error": r.error},
                    )
                )
                import_writer.insert_import_error(
                    batch_id=batch_id,
                    file_id=None,
                    error_type="local_missing" if not dst.exists() else "local_zip_invalid",
                    message=r.error or "local zip invalid",
                    meta={"archive_rel_path": archive_rel, "local_path": str(dst)},
                )
                continue

            try:
                stats = _ingest_zip(
                    conn,
                    zip_path=dst,
                    symbol=sym,
                    exchange="binance",
                    start_ms=start_ms,
                    end_ms=end_ms,
                    meta_writer=meta_writer,
                    dataset=dataset,
                    core_registry=core_registry,
                    force_update=bool(force_update),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] local-only: 入库失败: %s (zip=%s)", sym, e, dst.name, exc_info=True)
                run_meta["ingest_failed"] = int(run_meta["ingest_failed"]) + 1
                status = "partial"
                import_writer.insert_import_error(
                    batch_id=batch_id,
                    file_id=None,
                    error_type="ingest_failed",
                    message=str(e),
                    meta={"archive_rel_path": archive_rel, "local_path": str(dst)},
                )
                continue

            conn.commit()
            run_meta["ingest_ok"] = int(run_meta["ingest_ok"]) + 1
            run_meta["file_rows_total"] = int(run_meta["file_rows_total"]) + int(stats.file_rows)
            run_meta["affected_rows_total"] = int(run_meta["affected_rows_total"]) + int(stats.affected_rows)
            run_meta["chunks_decompressed_total"] = int(run_meta["chunks_decompressed_total"]) + int(stats.chunks_decompressed)
            run_meta["chunks_recompressed_total"] = int(run_meta["chunks_recompressed_total"]) + int(stats.chunks_recompressed)

            storage_writer.get_or_create_file_id(
                StorageFileSpec(
                    rel_path=archive_rel,
                    content_kind="zip",
                    source="binance_archive",
                    market_root="crypto",
                    market="futures",
                    product="um",
                    frequency=item.kind,
                    dataset="trades",
                    symbol=sym,
                    interval=None,
                    file_date=item.start_date if item.kind == "daily" else None,
                    file_month=date.fromisoformat(f"{item.period}-01") if item.kind == "monthly" else None,
                    size_bytes=dst.stat().st_size if dst.exists() else None,
                    checksum_sha256=r.checksum_sha256,
                    downloaded_at=None,
                    extracted_at=datetime.now(tz=timezone.utc),
                    row_count=stats.file_rows,
                    min_event_ts=(
                        datetime.fromtimestamp(stats.file_min_time / 1000.0, tz=timezone.utc)
                        if stats.file_min_time is not None
                        else None
                    ),
                    max_event_ts=(
                        datetime.fromtimestamp(stats.file_max_time / 1000.0, tz=timezone.utc)
                        if stats.file_max_time is not None
                        else None
                    ),
                    meta={"local_only": True, "verified": False, "local_path": str(dst)},
                )
            )

            # ==================== 自动压缩（防爆盘） ====================
            # 说明：全历史导入若不及时压缩，rowstore+PK 索引会快速膨胀到 TB 级。
            if compress_after_ms is not None:
                now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                threshold_ms = int(now_ms) - int(compress_after_ms)
                try:
                    with conn.cursor() as cur:
                        chunks = _list_um_trades_uncompressed_chunks_older_than(
                            cur,
                            start_ms=int(start_ms),
                            end_ms=int(end_ms),
                            older_than_end_ms=int(threshold_ms),
                        )
                        for ch in chunks:
                            try:
                                cur.execute("SELECT compress_chunk(%s::regclass)", (str(ch),))
                                run_meta["chunks_compressed_total"] = int(run_meta["chunks_compressed_total"]) + 1
                            except Exception as e:  # noqa: BLE001
                                run_meta["chunks_compress_failed_total"] = int(run_meta["chunks_compress_failed_total"]) + 1
                                logger.warning("[%s] local-only: compress_chunk 失败（chunk=%s）: %s", sym, ch, e)
                    conn.commit()
                except Exception as e:  # noqa: BLE001
                    logger.warning("[%s] local-only: 自动压缩失败: %s", sym, e)

        meta_writer.finish_run(run_id, status=status, error_message=error_message, meta=run_meta)
        import_writer.finish_batch(batch_id, status=status, meta=run_meta)

        return {
            "worker_id": int(worker_id),
            "status": status,
            "planned": int(len(tasks)),
            "ingested": int(run_meta["ingest_ok"]),
            "skipped": int(run_meta["skip_existing"]),
            "failed": int(run_meta["ingest_failed"]),
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_um_trades_compress_after_ms(cur: psycopg.Cursor) -> Optional[int]:
    """读取 market.binance_futures_um_trades 的 compress_after（ms）。

    说明：
    - UM trades 使用 integer hypertable（time=epoch ms）并启用压缩策略；
    - 为避免代码里硬编码与 DDL 漂移，这里优先从 Timescale 的 policy job 读取真实值；
    - 读取失败时返回 None（表示“无法判定”，调用方需决定是否降级）。
    """

    try:
        cur.execute(
            """
            SELECT (j.config->>'compress_after')::BIGINT
            FROM timescaledb_information.jobs j
            WHERE j.proc_name = 'policy_compression'
              AND j.hypertable_schema = 'crypto'
              AND j.hypertable_name = 'raw_futures_um_trades'
            ORDER BY j.job_id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except Exception:
        logger.debug("读取 UM trades compress_after 失败", exc_info=True)
        return None
    return None


def _is_um_trades_window_compressed(cur: psycopg.Cursor, *, start_ms: int, end_ms: int) -> Optional[bool]:
    """判断该窗口是否命中已压缩 chunk（True=存在压缩 chunk，False=都未压缩，None=无法判定）。"""

    try:
        cur.execute(
            """
            SELECT 1
            FROM timescaledb_information.chunks c
            WHERE c.hypertable_schema = 'crypto'
              AND c.hypertable_name = 'raw_futures_um_trades'
              AND c.is_compressed = TRUE
              AND c.range_start_integer < %(end)s
              AND c.range_end_integer > %(start)s
            LIMIT 1
            """,
            {"start": int(start_ms), "end": int(end_ms)},
        )
        return cur.fetchone() is not None
    except Exception:
        logger.debug("读取 UM trades chunk 压缩状态失败", exc_info=True)
        return None


def _list_um_trades_compressed_chunks(cur: psycopg.Cursor, *, start_ms: int, end_ms: int) -> list[str]:
    """列出窗口内命中的 compressed chunks（返回 chunk 的 regclass 字符串）。"""

    cur.execute(
        """
        SELECT format('%%I.%%I', c.chunk_schema, c.chunk_name)
        FROM timescaledb_information.chunks c
        WHERE c.hypertable_schema = 'crypto'
          AND c.hypertable_name = 'raw_futures_um_trades'
          AND c.is_compressed = TRUE
          AND c.range_start_integer < %(end)s
          AND c.range_end_integer > %(start)s
        ORDER BY c.range_start_integer
        """,
        {"start": int(start_ms), "end": int(end_ms)},
    )
    return [str(r[0]) for r in (cur.fetchall() or []) if r and r[0]]


def _list_um_trades_uncompressed_chunks(cur: psycopg.Cursor, *, start_ms: int, end_ms: int) -> list[str]:
    """列出窗口内命中的 uncompressed chunks（返回 chunk 的 regclass 字符串）。"""

    cur.execute(
        """
        SELECT format('%%I.%%I', c.chunk_schema, c.chunk_name)
        FROM timescaledb_information.chunks c
        WHERE c.hypertable_schema = 'crypto'
          AND c.hypertable_name = 'raw_futures_um_trades'
          AND c.is_compressed = FALSE
          AND c.range_start_integer < %(end)s
          AND c.range_end_integer > %(start)s
        ORDER BY c.range_start_integer
        """,
        {"start": int(start_ms), "end": int(end_ms)},
    )
    return [str(r[0]) for r in (cur.fetchall() or []) if r and r[0]]


def _list_um_trades_uncompressed_chunks_older_than(
    cur: psycopg.Cursor,
    *,
    start_ms: int,
    end_ms: int,
    older_than_end_ms: int,
) -> list[str]:
    """列出窗口内“未压缩且已超过阈值”的 chunks（返回 chunk 的 regclass 字符串）。"""

    cur.execute(
        """
        SELECT format('%%I.%%I', c.chunk_schema, c.chunk_name)
        FROM timescaledb_information.chunks c
        WHERE c.hypertable_schema = 'crypto'
          AND c.hypertable_name = 'raw_futures_um_trades'
          AND c.is_compressed = FALSE
          AND c.range_start_integer < %(end)s
          AND c.range_end_integer > %(start)s
          AND c.range_end_integer < %(older_than_end)s
        ORDER BY c.range_start_integer
        """,
        {"start": int(start_ms), "end": int(end_ms), "older_than_end": int(older_than_end_ms)},
    )
    return [str(r[0]) for r in (cur.fetchall() or []) if r and r[0]]


def _ingest_zip(
    conn: psycopg.Connection,
    *,
    zip_path: Path,
    symbol: str,
    exchange: str,
    start_ms: int,
    end_ms: int,
    meta_writer: IngestMetaWriter | None,
    dataset: str,
    core_registry: CoreRegistry,
    force_update: bool,
) -> IngestZipStats:
    with conn.cursor() as cur:
        # ==================== 离线异常路径：显式解压/更新/重压（P1） ====================
        # - 正常回填：压缩窗口之外（或命中已压缩 chunk）一律 fail-closed 降级 DO NOTHING
        # - 若需要覆盖官方更正（老数据），必须显式执行 decompress_chunk -> DO UPDATE -> compress_chunk
        # - 该路径代价高，仅允许 operator 显式开启（force_update=True）
        planned_chunks: list[str] = []
        chunks_to_recompress: list[str] = []
        chunks_decompressed = 0
        chunks_recompressed = 0

        if force_update:
            planned_chunks = _list_um_trades_compressed_chunks(cur, start_ms=int(start_ms), end_ms=int(end_ms))

        affected_rows = 0
        tmp_count = 0
        tmp_min_time: Optional[int] = None
        tmp_max_time: Optional[int] = None
        tmp_max_id: Optional[int] = None

        try:
            if force_update and planned_chunks:
                for ch in planned_chunks:
                    cur.execute("SELECT decompress_chunk(%s::regclass)", (str(ch),))
                    chunks_to_recompress.append(str(ch))
                chunks_decompressed = int(len(chunks_to_recompress))
                if chunks_decompressed:
                    logger.warning(
                        "[%s] force_update: 已解压 compressed chunks=%d（窗口 %d..%d）",
                        symbol,
                        chunks_decompressed,
                        int(start_ms),
                        int(end_ms),
                    )

            venue_id, instrument_id = core_registry.resolve_venue_and_instrument_id(
                venue_code=str(exchange).lower(),
                symbol=str(symbol).upper(),
                product="futures_um",
                cursor=cur,
            )

            cur.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS tmp_um_trades (
                  id BIGINT NOT NULL,
                  price DOUBLE PRECISION NOT NULL,
                  qty DOUBLE PRECISION NOT NULL,
                  quote_qty DOUBLE PRECISION NOT NULL,
                  time BIGINT NOT NULL,
                  is_buyer_maker BOOLEAN NOT NULL
                ) ON COMMIT DROP
                """
            )
            cur.execute("TRUNCATE tmp_um_trades")
            _copy_zip_csv_into_tmp(cur, zip_path)

            # 采样核对（轻量）：这批数据在文件内的时间范围与最大 trade id
            cur.execute(
                "SELECT COUNT(*), MIN(time), MAX(time), MAX(id) FROM tmp_um_trades WHERE time >= %s AND time < %s",
                (int(start_ms), int(end_ms)),
            )
            tmp_count, tmp_min_time, tmp_max_time, tmp_max_id = cur.fetchone() or (0, None, None, None)

            # ==================== 压缩窗口门禁（P0） ====================
            # 规则（强制）：
            # - 回填默认允许 ON CONFLICT DO UPDATE（以官方为准）
            # - 但当窗口已落入“压缩可能生效”的时间段后，禁止 UPDATE（否则可能触发 decompress/recompress，代价很高）
            allow_update = False
            if force_update:
                allow_update = True
                logger.warning("[%s] force_update: 已显式启用 DO UPDATE（zip=%s）", symbol, zip_path.name)
            else:
                compress_after_ms = _get_um_trades_compress_after_ms(cur)
                if compress_after_ms is None:
                    logger.warning("[%s] 无法读取 compress_after，保守降级为 DO NOTHING: zip=%s", symbol, zip_path.name)
                else:
                    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                    if int(end_ms) < now_ms - int(compress_after_ms):
                        logger.warning(
                            "[%s] 回填窗口已越过压缩线，降级为 DO NOTHING: end_ms=%d compress_after_ms=%d zip=%s",
                            symbol,
                            int(end_ms),
                            int(compress_after_ms),
                            zip_path.name,
                        )
                    else:
                        compressed = _is_um_trades_window_compressed(cur, start_ms=int(start_ms), end_ms=int(end_ms))
                        if compressed is None:
                            logger.warning("[%s] 无法判定 chunk 压缩状态，保守降级为 DO NOTHING: zip=%s", symbol, zip_path.name)
                        elif compressed:
                            logger.warning("[%s] 窗口命中已压缩 chunk，禁止 UPDATE（避免解压/重压）: zip=%s", symbol, zip_path.name)
                        else:
                            allow_update = True

            conflict_sql = (
                """
                DO UPDATE SET
                  price = EXCLUDED.price,
                  qty = EXCLUDED.qty,
                  quote_qty = EXCLUDED.quote_qty,
                  is_buyer_maker = EXCLUDED.is_buyer_maker
                WHERE (market.binance_futures_um_trades.price,
                       market.binance_futures_um_trades.qty,
                       market.binance_futures_um_trades.quote_qty,
                       market.binance_futures_um_trades.is_buyer_maker)
                  IS DISTINCT FROM
                      (EXCLUDED.price,
                       EXCLUDED.qty,
                       EXCLUDED.quote_qty,
                       EXCLUDED.is_buyer_maker)
                """
                if allow_update
                else "DO NOTHING"
            )

            cur.execute(
                """
                INSERT INTO market.binance_futures_um_trades (
                  venue_id, instrument_id, id, price, qty, quote_qty, time, is_buyer_maker
                )
                SELECT
                  %(venue_id)s,
                  %(instrument_id)s,
                  id,
                  price::DOUBLE PRECISION,
                  qty::DOUBLE PRECISION,
                  quote_qty::DOUBLE PRECISION,
                  time,
                  is_buyer_maker
                FROM tmp_um_trades
                WHERE time >= %(start_ms)s AND time < %(end_ms)s
                ON CONFLICT (venue_id, instrument_id, time, id)
                """
                + conflict_sql,
                {
                    "venue_id": int(venue_id),
                    "instrument_id": int(instrument_id),
                    "start_ms": int(start_ms),
                    "end_ms": int(end_ms),
                },
            )
            affected_rows = int(cur.rowcount or 0)

            if meta_writer and tmp_max_time is not None and tmp_max_id is not None:
                meta_writer.upsert_watermark(
                    exchange=exchange,
                    dataset=dataset,
                    symbol=symbol,
                    last_time=int(tmp_max_time),
                    last_id=int(tmp_max_id),
                )

            # 轻量对账：至少确保事实表在该窗口内“有数据且不小于文件条数”
            cur.execute(
                """
                SELECT COUNT(*)
                FROM market.binance_futures_um_trades
                WHERE venue_id = %s AND instrument_id = %s AND time >= %s AND time < %s
                """,
                (int(venue_id), int(instrument_id), int(start_ms), int(end_ms)),
            )
            fact_count = int((cur.fetchone() or (0,))[0])
            if tmp_count and fact_count < int(tmp_count):
                logger.warning("[%s] 对账异常: file_rows=%d fact_rows=%d zip=%s", symbol, tmp_count, fact_count, zip_path.name)
        finally:
            if force_update and chunks_to_recompress:
                for ch in chunks_to_recompress:
                    try:
                        cur.execute("SELECT compress_chunk(%s::regclass)", (str(ch),))
                        chunks_recompressed += 1
                    except Exception as e:  # noqa: BLE001
                        logger.warning("[%s] force_update: compress_chunk 失败（chunk=%s）: %s", symbol, ch, e)
                if chunks_decompressed:
                    logger.warning(
                        "[%s] force_update: 已重压 chunks=%d/%d（窗口 %d..%d）",
                        symbol,
                        int(chunks_recompressed),
                        int(chunks_decompressed),
                        int(start_ms),
                        int(end_ms),
                    )

        return IngestZipStats(
            affected_rows=int(affected_rows),
            file_rows=int(tmp_count or 0),
            file_min_time=int(tmp_min_time) if tmp_min_time is not None else None,
            file_max_time=int(tmp_max_time) if tmp_max_time is not None else None,
            file_max_id=int(tmp_max_id) if tmp_max_id is not None else None,
            chunks_decompressed=int(chunks_decompressed),
            chunks_recompressed=int(chunks_recompressed),
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
    force_update: bool = False,
    local_only: bool = False,
    workers: int = 1,
) -> dict[str, str]:
    if not symbols:
        raise ValueError("symbols 不能为空")
    if start_date > end_date:
        raise ValueError("start_date 不能大于 end_date")
    if write_db and not database_url:
        raise ValueError("write_db=True 但 DATABASE_URL 为空")

    dataset = "futures.um.trades"
    if int(workers) < 1:
        raise ValueError("workers 必须 >= 1")

    if bool(local_only):
        if not bool(write_db):
            raise ValueError("local-only 模式必须 write_db=True")

        tasks: list[_PlanItem] = []
        for sym in symbols:
            tasks.extend(_build_plan(sym, start_date, end_date, prefer_monthly=prefer_monthly))

        if not tasks:
            return {str(sym).upper(): "success" for sym in symbols}

        w = min(int(workers), len(tasks))
        if w <= 1:
            r = _ingest_local_plan_items_worker(
                worker_id=1,
                tasks=tasks,
                service_root=service_root,
                database_url=database_url,
                force_update=bool(force_update),
            )
            status = str(r.get("status") or "failed")
            return {str(sym).upper(): status for sym in symbols}

        buckets = _partition_local_tasks_by_size(tasks, service_root=service_root, workers=w)
        logger.warning("local-only 并发导入: workers=%d tasks=%d buckets=%d", w, len(tasks), len(buckets))

        ctx = multiprocessing.get_context("fork")
        q: multiprocessing.Queue = ctx.Queue()
        procs: list[multiprocessing.Process] = []

        def _entry(worker_id: int, bucket: list[_PlanItem]) -> None:
            try:
                out = _ingest_local_plan_items_worker(
                    worker_id=int(worker_id),
                    tasks=list(bucket),
                    service_root=service_root,
                    database_url=database_url,
                    force_update=bool(force_update),
                )
            except Exception as e:  # noqa: BLE001
                out = {"worker_id": int(worker_id), "status": "failed", "error": str(e), "planned": len(bucket)}
            q.put(out)

        for i, bucket in enumerate(buckets):
            p = ctx.Process(target=_entry, args=(i + 1, bucket))
            p.start()
            procs.append(p)

        results: list[dict[str, object]] = []
        for _ in procs:
            results.append(q.get())
        for p in procs:
            p.join()

        logger.warning("local-only worker 结果: %s", results)
        overall_status = "success" if all(r.get("status") == "success" for r in results) else "partial"
        return {str(sym).upper(): overall_status for sym in symbols}

    conn = connect(database_url) if write_db else None
    core_registry = CoreRegistry(conn) if conn is not None else None
    import_writer = ImportMetaWriter(conn) if conn is not None else None
    storage_writer = StorageFilesWriter(conn) if conn is not None else None
    batch_id: Optional[int] = None
    symbol_statuses: dict[str, str] = {}

    if import_writer:
        batch_id = import_writer.start_batch(
            ImportBatchSpec(
                source="binance_archive",
                note="binance_archive um trades backfill",
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

            plan = _build_plan(sym, start_date, end_date, prefer_monthly=prefer_monthly)
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
                "force_update": bool(force_update),
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
                "chunks_decompressed_total": 0,
                "chunks_recompressed_total": 0,
            }

            status = "success"
            error_message = None

            try:
                i = 0
                while i < len(plan):
                    item = plan[i]
                    i += 1

                    if item.kind == "daily":
                        rel = _relpath_daily_zip(sym, item.start_date)
                        archive_rel = _archive_relpath_daily_zip(sym, item.start_date)
                        url = _url_daily_zip(binance_data_base, sym, item.start_date)
                        dst = (service_root / rel) if write_files else (service_root / "run" / "tmp_download" / rel)

                        old_checksum = import_writer.get_existing_checksum(archive_rel) if import_writer else None
                        r = _download_or_repair_zip(url, dst, allow_no_checksum=allow_no_checksum, timeout_seconds=60.0, max_retries=3)
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
                                        dataset="trades",
                                        symbol=sym.upper(),
                                        interval=None,
                                        file_date=item.start_date,
                                        file_month=None,
                                        size_bytes=dst.stat().st_size if dst.exists() else None,
                                        checksum_sha256=r.checksum_sha256,
                                        downloaded_at=datetime.now(tz=timezone.utc),
                                        meta={
                                            "verified": bool(r.verified),
                                            "url": url,
                                            "local_path": str(dst),
                                            "error": r.error,
                                        },
                                    )
                                )

                            if import_writer:
                                error_type = "download_failed"
                                if r.status_code == 404 and (r.error or "").startswith("CHECKSUM"):
                                    error_type = "checksum_missing"
                                if r.error and "sha256 校验失败" in r.error:
                                    error_type = "checksum_mismatch"
                                import_writer.insert_import_error(
                                    batch_id=batch_id,
                                    file_id=file_id,
                                    error_type=error_type,
                                    message=r.error or "download failed",
                                    meta={"url": url, "archive_rel_path": archive_rel},
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
                                    dataset="trades",
                                    symbol=sym.upper(),
                                    interval=None,
                                    file_date=item.start_date,
                                    file_month=None,
                                    size_bytes=dst.stat().st_size if dst.exists() else None,
                                    checksum_sha256=r.checksum_sha256,
                                    downloaded_at=datetime.now(tz=timezone.utc),
                                    meta={
                                        "verified": bool(r.verified),
                                        "url": url,
                                        "local_path": str(dst),
                                    },
                                )
                            )

                        if import_writer and r.verified and r.checksum_sha256 and old_checksum and old_checksum != r.checksum_sha256:
                            try:
                                import_writer.insert_file_revision(
                                    rel_path=archive_rel,
                                    old_checksum_sha256=old_checksum,
                                    new_checksum_sha256=r.checksum_sha256,
                                    note="checksum changed on download",
                                )
                            except Exception as e:  # noqa: BLE001
                                logger.warning("[%s] 写入 file_revisions 失败: %s", sym, e)

                        if write_db:
                            assert conn is not None
                            assert core_registry is not None
                            start_ms, end_ms = _date_range_ms_utc(item.start_date, item.end_date)
                            try:
                                stats = _ingest_zip(
                                    conn,
                                    zip_path=dst,
                                    symbol=sym,
                                    exchange="binance",
                                    start_ms=start_ms,
                                    end_ms=end_ms,
                                    meta_writer=meta_writer,
                                    dataset=dataset,
                                    core_registry=core_registry,
                                    force_update=bool(force_update),
                                )
                            except Exception as e:  # noqa: BLE001
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
                            run_meta["chunks_decompressed_total"] = int(run_meta["chunks_decompressed_total"]) + int(
                                stats.chunks_decompressed
                            )
                            run_meta["chunks_recompressed_total"] = int(run_meta["chunks_recompressed_total"]) + int(
                                stats.chunks_recompressed
                            )
                            logger.info(
                                "[%s] %s 入库完成: affected=%d file_rows=%d",
                                sym,
                                item.period,
                                stats.affected_rows,
                                stats.file_rows,
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
                                        dataset="trades",
                                        symbol=sym.upper(),
                                        interval=None,
                                        file_date=item.start_date,
                                        file_month=None,
                                        extracted_at=datetime.now(tz=timezone.utc),
                                        row_count=stats.file_rows,
                                        min_event_ts=(
                                            datetime.fromtimestamp(stats.file_min_time / 1000.0, tz=timezone.utc)
                                            if stats.file_min_time is not None
                                            else None
                                        ),
                                        max_event_ts=(
                                            datetime.fromtimestamp(stats.file_max_time / 1000.0, tz=timezone.utc)
                                            if stats.file_max_time is not None
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
                        r = _download_or_repair_zip(url, dst, allow_no_checksum=allow_no_checksum, timeout_seconds=60.0, max_retries=3)
                        if not r.ok and r.status_code == 404:
                            logger.info("[%s] 月度不存在，降级按日: %s", sym, item.period)
                            run_meta["monthly_404"] = int(run_meta["monthly_404"]) + 1
                            daily = _build_plan(sym, item.start_date, item.end_date, prefer_monthly=False)
                            plan[i:i] = daily
                            continue
                        if not r.ok:
                            logger.warning("[%s] 月度下载失败: %s (status=%s error=%s)", sym, url, r.status_code, r.error)
                            run_meta["download_failed"] = int(run_meta["download_failed"]) + 1
                            status = "partial"
                            file_id = None
                            month_date = date.fromisoformat(f"{item.period}-01")
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
                                        dataset="trades",
                                        symbol=sym.upper(),
                                        interval=None,
                                        file_date=None,
                                        file_month=month_date,
                                        size_bytes=dst.stat().st_size if dst.exists() else None,
                                        checksum_sha256=r.checksum_sha256,
                                        downloaded_at=datetime.now(tz=timezone.utc),
                                        meta={
                                            "verified": bool(r.verified),
                                            "url": url,
                                            "local_path": str(dst),
                                            "error": r.error,
                                        },
                                    )
                                )

                            if import_writer:
                                error_type = "download_failed"
                                if r.status_code == 404 and (r.error or "").startswith("CHECKSUM"):
                                    error_type = "checksum_missing"
                                if r.error and "sha256 校验失败" in r.error:
                                    error_type = "checksum_mismatch"
                                import_writer.insert_import_error(
                                    batch_id=batch_id,
                                    file_id=file_id,
                                    error_type=error_type,
                                    message=r.error or "download failed",
                                    meta={"url": url, "archive_rel_path": archive_rel},
                                )
                            continue

                        run_meta["download_ok"] = int(run_meta["download_ok"]) + 1
                        file_id = None
                        month_date = date.fromisoformat(f"{item.period}-01")
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
                                    dataset="trades",
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

                        if import_writer and r.verified and r.checksum_sha256 and old_checksum and old_checksum != r.checksum_sha256:
                            try:
                                import_writer.insert_file_revision(
                                    rel_path=archive_rel,
                                    old_checksum_sha256=old_checksum,
                                    new_checksum_sha256=r.checksum_sha256,
                                    note="checksum changed on download",
                                )
                            except Exception as e:  # noqa: BLE001
                                logger.warning("[%s] 写入 file_revisions 失败: %s", sym, e)

                        if write_db:
                            assert conn is not None
                            assert core_registry is not None
                            start_ms, end_ms = _date_range_ms_utc(item.start_date, item.end_date)
                            try:
                                stats = _ingest_zip(
                                    conn,
                                    zip_path=dst,
                                    symbol=sym,
                                    exchange="binance",
                                    start_ms=start_ms,
                                    end_ms=end_ms,
                                    meta_writer=meta_writer,
                                    dataset=dataset,
                                    core_registry=core_registry,
                                    force_update=bool(force_update),
                                )
                            except Exception as e:  # noqa: BLE001
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
                                "[%s] %s 月度入库完成: affected=%d file_rows=%d",
                                sym,
                                item.period,
                                stats.affected_rows,
                                stats.file_rows,
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
                                        dataset="trades",
                                        symbol=sym.upper(),
                                        interval=None,
                                        file_date=None,
                                        file_month=month_date,
                                        extracted_at=datetime.now(tz=timezone.utc),
                                        row_count=stats.file_rows,
                                        min_event_ts=(
                                            datetime.fromtimestamp(stats.file_min_time / 1000.0, tz=timezone.utc)
                                            if stats.file_min_time is not None
                                            else None
                                        ),
                                        max_event_ts=(
                                            datetime.fromtimestamp(stats.file_max_time / 1000.0, tz=timezone.utc)
                                            if stats.file_max_time is not None
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

                    raise RuntimeError(f"未知计划类型: {item.kind}")
            except Exception as e:  # noqa: BLE001
                status = "failed"
                error_message = str(e)
                run_meta["ingest_failed"] = int(run_meta["ingest_failed"]) + 1
                logger.error("[%s] 回填失败: %s", sym, e)
            finally:
                if meta_writer and run_id is not None:
                    try:
                        meta_writer.finish_run(run_id, status=status, error_message=error_message, meta=run_meta)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("[%s] 写入 ingest_runs 结束状态失败: %s", sym, e)
                symbol_statuses[str(sym).upper()] = status
    finally:
        if import_writer and batch_id is not None:
            statuses = list(symbol_statuses.values())
            if statuses and all(s == "success" for s in statuses):
                batch_status = "success"
            elif statuses and all(s == "failed" for s in statuses):
                batch_status = "failed"
            else:
                batch_status = "partial" if statuses else "failed"

            try:
                import_writer.finish_batch(batch_id, status=batch_status, meta={"symbol_statuses": symbol_statuses})
            except Exception as e:  # noqa: BLE001
                logger.warning("写入 import_batches 结束状态失败: %s", e)

        if conn is not None:
            conn.close()
    return dict(symbol_statuses)


__all__ = ["download_and_ingest"]
