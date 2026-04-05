"""UM 合约 bookTicker（买一卖一流）原子事实入库写入器。

# 对齐目标
- 表：market.binance_futures_um_book_ticker
- CSV 列：update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,event_time
- 公共字段：venue_id,instrument_id（由 core.* 字典化生成）
- 幂等：主键冲突 DO NOTHING
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import Iterable, List, Optional

import psycopg

from binance.storage.core_registry import CoreRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawFuturesUmBookTickerRow:
    exchange: str
    symbol: str
    update_id: int
    best_bid_price: Decimal
    best_bid_qty: Decimal
    best_ask_price: Decimal
    best_ask_qty: Decimal
    transaction_time: Optional[int]
    event_time: int


class RawFuturesUmBookTickerWriter:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self._core = CoreRegistry(conn)

    def insert_rows(self, rows: Iterable[RawFuturesUmBookTickerRow]) -> int:
        batch: List[RawFuturesUmBookTickerRow] = list(rows)
        if not batch:
            return 0

        sql = """
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
        ) VALUES (
            %(venue_id)s,
            %(instrument_id)s,
            %(update_id)s,
            %(best_bid_price)s,
            %(best_bid_qty)s,
            %(best_ask_price)s,
            %(best_ask_qty)s,
            %(transaction_time)s,
            %(event_time)s
        )
        ON CONFLICT (venue_id, instrument_id, event_time, update_id) DO NOTHING
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
                        "update_id": int(r.update_id),
                        "best_bid_price": float(r.best_bid_price),
                        "best_bid_qty": float(r.best_bid_qty),
                        "best_ask_price": float(r.best_ask_price),
                        "best_ask_qty": float(r.best_ask_qty),
                        "transaction_time": int(r.transaction_time) if r.transaction_time is not None else None,
                        "event_time": int(r.event_time),
                    }
                )

            cur.executemany(sql, params)
            rc = cur.rowcount
            inserted = int(rc) if rc is not None and int(rc) >= 0 else len(batch)

        self._conn.commit()
        return int(inserted)


__all__ = ["RawFuturesUmBookTickerRow", "RawFuturesUmBookTickerWriter"]
