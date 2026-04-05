"""旁路元数据写入器（runs/watermark/gaps）。

目标：
- 不污染事实表（raw trades 只存事实）
- 仍能做到：可观测、可巡检、可补齐（你最关心 gap）
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import psycopg
from psycopg import sql

from assets.common.contracts.db_contracts import governance_lineage_events_table_name, governance_schema

logger = logging.getLogger(__name__)


LINEAGE_ACTOR_ID = "core/market/binance"

# dataset（ingest_runs.dataset）-> resource_id（assets/contracts/resources.v1.yaml）
_DATASET_TO_RESOURCE_ID: dict[str, str] = {
    "futures.um.trades": "facts/market/futures_um_trades",
    "futures.um.bookTicker": "facts/market/futures_um_book_ticker",
    "futures.um.bookDepth": "facts/market/futures_um_book_depth",
    "futures.um.metrics": "facts/market/futures_um_metrics_snapshot_5m",
    "spot.trades": "facts/market/spot_trades",
}


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_event_key(*, actor_id: str, run_id: str, action: str, resource_id: str, occurred_at: datetime) -> str:
    canonical = "|".join(
        [
            (actor_id or "").strip(),
            (run_id or "").strip(),
            (action or "").strip(),
            (resource_id or "").strip(),
            _iso_z(occurred_at),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _governance_schema() -> str:
    return governance_schema()


def _lineage_events_table() -> str:
    return governance_lineage_events_table_name()


def _emit_lineage_for_ingest_run(
    conn: psycopg.Connection,
    *,
    ingest_run_id: int,
    run_status: str,
    error_message: str | None,
    meta: dict | None,
) -> None:
    """按 ingest_run 写入一条血缘事件（append-only）。

    约束：
    - 只在已知 dataset->resource_id 映射时写入；未知 dataset 跳过（避免写错语义）。
    - fail-soft：写血缘失败不得影响 ingest_runs 的状态落库。
    """

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT exchange, dataset, mode FROM market.binance_ingest_runs WHERE run_id=%s",
                (int(ingest_run_id),),
            )
            row = cur.fetchone()
    except Exception:
        return

    if not row:
        return

    exchange, dataset, mode = row[0], row[1], row[2]
    dataset_str = str(dataset or "").strip()
    resource_id = _DATASET_TO_RESOURCE_ID.get(dataset_str)
    if not resource_id:
        return

    occurred_at = datetime.now(timezone.utc)
    action = "produce"
    status_norm = "success" if str(run_status or "").strip().lower() == "success" else "failed"
    run_id_text = str(int(ingest_run_id))
    event_key = _build_event_key(
        actor_id=LINEAGE_ACTOR_ID,
        run_id=run_id_text,
        action=action,
        resource_id=resource_id,
        occurred_at=occurred_at,
    )

    clean_meta: dict[str, object] = {
        "exchange": str(exchange or ""),
        "dataset": dataset_str,
        "mode": str(mode or ""),
    }

    if error_message:
        clean_meta["error_text"] = str(error_message)[:1000]

    # 只挑少量可判定字段，避免把大 meta 复制进血缘表
    if isinstance(meta, dict):
        for k in (
            "symbols_count",
            "ws_trades_enqueued",
            "ws_watch_errors",
            "rest_fetch_calls",
            "db_rows_attempted",
            "db_rows_inserted",
            "max_lag_ms",
            "gaps_inserted",
        ):
            if k in meta:
                clean_meta[k] = meta.get(k)

    insert_sql = sql.SQL(
        """
        INSERT INTO {schema}.{table}
            (event_key, occurred_at, actor_id, run_id, action, resource_id, status, meta)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, (%s)::jsonb)
        ON CONFLICT (event_key)
        DO NOTHING
        """
    ).format(schema=sql.Identifier(_governance_schema()), table=sql.Identifier(_lineage_events_table()))

    try:
        with conn.cursor() as cur:
            cur.execute(
                insert_sql,
                (
                    event_key,
                    occurred_at,
                    LINEAGE_ACTOR_ID,
                    run_id_text,
                    action,
                    resource_id,
                    status_norm,
                    json.dumps(clean_meta, ensure_ascii=False, separators=(",", ":"), default=str),
                ),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return


def _deep_merge_dict(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(left)
    for k, rv in right.items():
        lv = merged.get(k)
        if isinstance(lv, dict) and isinstance(rv, dict):
            merged[k] = _deep_merge_dict(lv, rv)
        else:
            merged[k] = rv
    return merged


@dataclass(frozen=True)
class IngestRunSpec:
    exchange: str
    dataset: str
    mode: str  # realtime/backfill/repair


@dataclass(frozen=True)
class IngestGap:
    gap_id: int
    exchange: str
    dataset: str
    symbol: str
    start_time: int
    end_time: int
    reason: Optional[str]
    status: str
    run_id: Optional[int]
    detected_at: datetime | None = None


class IngestMetaWriter:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def start_run(self, spec: IngestRunSpec) -> int:
        sql = """
        INSERT INTO market.binance_ingest_runs (
          exchange, dataset, mode, status
        ) VALUES (
          %(exchange)s, %(dataset)s, %(mode)s, 'running'
        )
        RETURNING run_id
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, {"exchange": spec.exchange, "dataset": spec.dataset, "mode": spec.mode})
            row = cur.fetchone()
            if not row:
                raise RuntimeError("无法创建 ingest_run")
            run_id = int(row[0])
        self._conn.commit()
        return run_id

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        error_message: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> None:
        merged_meta: Optional[dict] = None
        if meta:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT meta FROM market.binance_ingest_runs WHERE run_id = %(run_id)s FOR UPDATE",
                    {"run_id": int(run_id)},
                )
                row = cur.fetchone()
                base: Any = row[0] if row else {}
                if isinstance(base, str):
                    try:
                        base = json.loads(base)
                    except Exception:
                        base = {}
                if not isinstance(base, dict):
                    base = {}
                merged_meta = _deep_merge_dict(base, meta)

        sql = """
        UPDATE market.binance_ingest_runs
        SET status = %(status)s,
            error_message = %(error_message)s,
            finished_at = NOW(),
            meta = COALESCE(%(meta)s::jsonb, market.binance_ingest_runs.meta)
        WHERE run_id = %(run_id)s
        """
        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "run_id": int(run_id),
                    "status": status,
                    "error_message": error_message,
                    "meta": json.dumps(merged_meta, ensure_ascii=False) if merged_meta else None,
                },
            )
        self._conn.commit()

        # 血缘事件：按 ingest_run 落 1 条（append-only；失败不影响主链）
        _emit_lineage_for_ingest_run(
            self._conn,
            ingest_run_id=int(run_id),
            run_status=str(status),
            error_message=error_message,
            meta=meta if isinstance(meta, dict) else None,
        )

    def upsert_watermark(self, *, exchange: str, dataset: str, symbol: str, last_time: int, last_id: int) -> None:
        sql = """
        INSERT INTO market.binance_ingest_watermark (
          exchange, dataset, symbol, last_time, last_id
        ) VALUES (
          %(exchange)s, %(dataset)s, %(symbol)s, %(last_time)s, %(last_id)s
        )
        ON CONFLICT (exchange, dataset, symbol) DO UPDATE SET
          last_time = GREATEST(market.binance_ingest_watermark.last_time, EXCLUDED.last_time),
          last_id   = GREATEST(market.binance_ingest_watermark.last_id,   EXCLUDED.last_id),
          updated_at = NOW()
        """
        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "exchange": exchange,
                    "dataset": dataset,
                    "symbol": symbol.upper(),
                    "last_time": int(last_time),
                    "last_id": int(last_id),
                },
            )
        self._conn.commit()

    def insert_gap(
        self,
        *,
        exchange: str,
        dataset: str,
        symbol: str,
        start_time: int,
        end_time: int,
        reason: str,
        run_id: Optional[int],
    ) -> None:
        if start_time >= end_time:
            return

        sql = """
        INSERT INTO market.binance_ingest_gaps (
          exchange, dataset, symbol, start_time, end_time, status, reason, run_id
        ) VALUES (
          %(exchange)s, %(dataset)s, %(symbol)s, %(start_time)s, %(end_time)s, 'open', %(reason)s, %(run_id)s
        )
        ON CONFLICT (exchange, dataset, symbol, start_time, end_time) DO NOTHING
        """
        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "exchange": exchange,
                    "dataset": dataset,
                    "symbol": symbol.upper(),
                    "start_time": int(start_time),
                    "end_time": int(end_time),
                    "reason": reason,
                    "run_id": int(run_id) if run_id is not None else None,
                },
            )
        self._conn.commit()

    def claim_open_gaps(
        self,
        *,
        exchange: str,
        dataset: str,
        symbols: Optional[Sequence[str]],
        limit: int,
        run_id: int,
    ) -> list[IngestGap]:
        """认领 open gaps（open -> repairing），用于 repair worker 的并发安全消费。

        说明：
        - 使用 `FOR UPDATE SKIP LOCKED`，允许多个 repair 进程并行工作但不重复处理同一 gap。
        - 认领后会写入 `run_id`（指向本次 repair ingest_run）。
        """
        symbols_norm = [str(s).upper() for s in (symbols or []) if str(s).strip()]

        sql = """
        WITH picked AS (
          SELECT gap_id
          FROM market.binance_ingest_gaps
          WHERE exchange = %(exchange)s
            AND dataset = %(dataset)s
            AND status = 'open'
            AND (%(symbols_any)s = FALSE OR symbol = ANY(%(symbols)s::text[]))
          ORDER BY detected_at ASC
          FOR UPDATE SKIP LOCKED
          LIMIT %(limit)s
        )
        UPDATE market.binance_ingest_gaps g
        SET status = 'repairing',
            run_id = %(run_id)s
        FROM picked
        WHERE g.gap_id = picked.gap_id
        RETURNING g.gap_id, g.exchange, g.dataset, g.symbol, g.start_time, g.end_time, g.reason, g.status, g.run_id, g.detected_at
        """

        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "exchange": exchange,
                    "dataset": dataset,
                    "symbols_any": bool(symbols_norm),
                    "symbols": symbols_norm,
                    "limit": int(limit),
                    "run_id": int(run_id),
                },
            )
            rows = cur.fetchall() or []
        self._conn.commit()
        return [
            IngestGap(
                gap_id=int(r[0]),
                exchange=str(r[1]),
                dataset=str(r[2]),
                symbol=str(r[3]),
                start_time=int(r[4]),
                end_time=int(r[5]),
                reason=str(r[6]) if r[6] is not None else None,
                status=str(r[7]),
                run_id=int(r[8]) if r[8] is not None else None,
                detected_at=r[9],
            )
            for r in rows
        ]

    def get_gap(
        self,
        *,
        exchange: str,
        dataset: str,
        symbol: str,
        start_time: int,
        end_time: int,
    ) -> IngestGap | None:
        sql = """
        SELECT gap_id, exchange, dataset, symbol, start_time, end_time, reason, status, run_id, detected_at
        FROM market.binance_ingest_gaps
        WHERE exchange = %(exchange)s
          AND dataset = %(dataset)s
          AND symbol = %(symbol)s
          AND start_time = %(start_time)s
          AND end_time = %(end_time)s
        LIMIT 1
        """
        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "exchange": exchange,
                    "dataset": dataset,
                    "symbol": symbol.upper(),
                    "start_time": int(start_time),
                    "end_time": int(end_time),
                },
            )
            row = cur.fetchone()
        if not row:
            return None
        return IngestGap(
            gap_id=int(row[0]),
            exchange=str(row[1]),
            dataset=str(row[2]),
            symbol=str(row[3]),
            start_time=int(row[4]),
            end_time=int(row[5]),
            reason=str(row[6]) if row[6] is not None else None,
            status=str(row[7]),
            run_id=int(row[8]) if row[8] is not None else None,
            detected_at=row[9],
        )

    def close_gap(self, gap_id: int) -> None:
        self._set_gap_status(gap_id, status="closed", reason=None)

    def reopen_gap(self, gap_id: int, *, reason: Optional[str] = None) -> None:
        self._set_gap_status(gap_id, status="open", reason=reason)

    def ignore_gap(self, gap_id: int, *, reason: Optional[str] = None) -> None:
        self._set_gap_status(gap_id, status="ignored", reason=reason)

    def _set_gap_status(self, gap_id: int, *, status: str, reason: Optional[str]) -> None:
        sql = """
        UPDATE market.binance_ingest_gaps
        SET status = %(status)s,
            reason = COALESCE(%(reason)s, market.binance_ingest_gaps.reason),
            detected_at = NOW()
        WHERE gap_id = %(gap_id)s
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, {"gap_id": int(gap_id), "status": status, "reason": reason})
        self._conn.commit()
