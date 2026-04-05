"""UM 合约 bookDepth（百分比档位深度曲线）原子事实入库写入器。

# 对齐目标
- 表：market.binance_futures_um_book_depth
- CSV 列：timestamp,percentage,depth,notional
- 公共字段：venue_id,instrument_id（由 core.* 字典化生成）
- 幂等：主键冲突 DO NOTHING
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Iterable, List

import psycopg

from binance.storage.core_registry import CoreRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawFuturesUmBookDepthRow:
    exchange: str
    symbol: str
    timestamp: int  # epoch(ms)
    percentage: float
    depth: float
    notional: float


class RawFuturesUmBookDepthWriter:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self._core = CoreRegistry(conn)

    def insert_rows(self, rows: Iterable[RawFuturesUmBookDepthRow]) -> int:
        batch: List[RawFuturesUmBookDepthRow] = list(rows)
        if not batch:
            return 0

        sql = """
        INSERT INTO market.binance_futures_um_book_depth (
            venue_id,
            instrument_id,
            timestamp,
            percentage,
            depth,
            notional
        ) VALUES (
            %(venue_id)s,
            %(instrument_id)s,
            %(timestamp)s,
            %(percentage)s,
            %(depth)s,
            %(notional)s
        )
        ON CONFLICT (venue_id, instrument_id, timestamp, percentage) DO NOTHING
        """

        with self._conn.cursor() as cur:
            pair_to_ids: dict[tuple[str, str], tuple[int, int]] = {}
            for r in batch:
                key = (str(r.exchange).lower(), str(r.symbol).upper())
                if key in pair_to_ids:
                    continue
                pair_to_ids[key] = self._core.resolve_venue_and_instrument_id(
                    venue_code=key[0],
                    symbol=key[1],
                    product="futures_um",
                    cursor=cur,
                )

            params = []
            for r in batch:
                exchange = str(r.exchange).lower()
                symbol = str(r.symbol).upper()
                venue_id, instrument_id = pair_to_ids[(exchange, symbol)]
                params.append(
                    {
                        "venue_id": int(venue_id),
                        "instrument_id": int(instrument_id),
                        "timestamp": int(r.timestamp),
                        "percentage": float(r.percentage),
                        "depth": float(r.depth),
                        "notional": float(r.notional),
                    }
                )

            cur.executemany(sql, params)
            rc = cur.rowcount
            inserted = int(rc) if rc is not None and int(rc) >= 0 else len(batch)

        self._conn.commit()
        return int(inserted)


__all__ = ["RawFuturesUmBookDepthRow", "RawFuturesUmBookDepthWriter"]
