"""futures/um/trades 缺口修复（gap repair）。

目标（说人话）：
- 实时侧会把“可能缺数据的时间窗”记录为 `market.binance_ingest_gaps(status='open')`；
- repair 的职责就是把这些 gap 变成“已对账/已补齐”的 closed 工单。

策略（最小闭环）：
1) 认领 open gaps（open -> repairing，SKIP LOCKED 并发安全）
2) 把 gap 的 [start_time,end_time) 转成 UTC 日期范围
3) 调用 Binance 官方历史归档源 权威回填（daily/monthly 智能边界）
4) 成功则 close gap；失败/部分成功则 reopen gap（等待下次重试）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
from pathlib import Path
from typing import Optional, Sequence

from binance.datasets.futures_um_trades.backfill import download_and_ingest
from binance.sources.archive_source.time_utils import ms_to_date_utc
from binance.storage.ingest_meta import IngestMetaWriter, IngestRunSpec, IngestGap
from binance.storage.pg import connect

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepairResult:
    claimed: int
    closed: int
    reopened: int


def _gap_to_utc_date_range(gap: IngestGap) -> tuple[date, date]:
    start_date = ms_to_date_utc(int(gap.start_time))
    # end_time 为 end exclusive；落到日期时要减 1ms，否则会把“刚好跨天边界”的 gap 误判到下一天
    end_ms_inclusive = max(int(gap.start_time), int(gap.end_time) - 1)
    end_date = ms_to_date_utc(end_ms_inclusive)
    return start_date, end_date


def repair_open_gaps(
    *,
    service_root: Path,
    database_url: str,
    binance_data_base: str,
    symbols: Optional[Sequence[str]],
    max_jobs: int,
    write_files: bool,
    prefer_monthly: bool,
    allow_no_checksum: bool,
) -> RepairResult:
    """消费并修复 open gaps。

    注意：
    - 本函数不会“猜测 gap 是否真实缺数据”；它只负责触发权威回填并用结果关闭工单。
    - 对于“当天/最近数据”可能尚未在 官方历史归档源 发布的情况，回填会返回 partial，此时 gap 会被 reopen 等待下次重试。
    """
    if max_jobs <= 0:
        raise ValueError("max_jobs 必须 > 0")
    if not database_url:
        raise ValueError("DATABASE_URL 为空，无法执行 repair")

    conn = connect(database_url)
    meta_writer = IngestMetaWriter(conn)

    run_id = meta_writer.start_run(IngestRunSpec(exchange="binance", dataset="futures.um.trades", mode="repair"))

    run_status = "success"
    run_error: Optional[str] = None
    claimed: list[IngestGap] = []
    closed = 0
    reopened = 0

    try:
        claimed = meta_writer.claim_open_gaps(
            exchange="binance",
            dataset="futures.um.trades",
            symbols=symbols,
            limit=int(max_jobs),
            run_id=run_id,
        )

        if not claimed:
            logger.info("repair: 没有可认领的 open gaps（exchange=binance dataset=futures.um.trades）")
            return RepairResult(claimed=0, closed=0, reopened=0)

        for gap in claimed:
            start_date, end_date = _gap_to_utc_date_range(gap)
            logger.info(
                "[%s] repair gap_id=%d: %d..%d -> %s..%s",
                gap.symbol,
                gap.gap_id,
                gap.start_time,
                gap.end_time,
                start_date.isoformat(),
                end_date.isoformat(),
            )

            try:
                statuses = download_and_ingest(
                    symbols=[gap.symbol],
                    start_date=start_date,
                    end_date=end_date,
                    service_root=service_root,
                    database_url=database_url,
                    binance_data_base=binance_data_base,
                    write_files=write_files,
                    write_db=True,
                    prefer_monthly=prefer_monthly,
                    allow_no_checksum=allow_no_checksum,
                )
                sym_status = statuses.get(str(gap.symbol).upper(), "failed")
                if sym_status == "success":
                    meta_writer.close_gap(gap.gap_id)
                    closed += 1
                    continue

                run_status = "partial"
                reopened += 1
                meta_writer.reopen_gap(gap.gap_id, reason=f"{gap.reason or ''} | repair_result={sym_status}")
            except Exception as e:  # noqa: BLE001
                # 失败：不把 gap 留在 repairing（否则会卡死），直接回滚到 open 等下次重试
                run_status = "partial"
                reopened += 1
                meta_writer.reopen_gap(gap.gap_id, reason=f"{gap.reason or ''} | repair_exception={e}")
                logger.warning("[%s] repair 失败（已 reopen gap_id=%d）: %s", gap.symbol, gap.gap_id, e)

        return RepairResult(claimed=len(claimed), closed=closed, reopened=reopened)
    except Exception as e:  # noqa: BLE001
        run_status = "failed"
        run_error = str(e)
        raise
    finally:
        try:
            meta_writer.finish_run(run_id, status=run_status, error_message=run_error)
        except Exception as e:  # noqa: BLE001
            logger.warning("repair: 写入 ingest_runs 结束状态失败: %s", e)
        conn.close()


__all__ = ["repair_open_gaps", "RepairResult"]
