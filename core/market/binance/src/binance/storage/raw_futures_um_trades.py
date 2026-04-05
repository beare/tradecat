"""UM 合约逐笔（trades）原子事实入库写入器。

# 对齐目标
# - 表：market.binance_futures_um_trades
# - CSV 列：id,price,qty,quote_qty,time,is_buyer_maker
# - 关键约束：字段完备 + 幂等（主键冲突时 DO NOTHING）
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import Iterable, List

import psycopg

from binance.storage.core_registry import CoreRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawFuturesUmTradeRow:
    exchange: str
    symbol: str
    id: int
    price: Decimal
    qty: Decimal
    quote_qty: Decimal
    time: int
    is_buyer_maker: bool


class RawFuturesUmTradesWriter:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self._core = CoreRegistry(conn)

    def insert_rows(self, rows: Iterable[RawFuturesUmTradeRow]) -> int:
        batch: List[RawFuturesUmTradeRow] = list(rows)
        if not batch:
            return 0

        sql = """
        INSERT INTO market.binance_futures_um_trades (
            venue_id,
            instrument_id,
            id,
            price,
            qty,
            quote_qty,
            time,
            is_buyer_maker
        ) VALUES (
            %(venue_id)s,
            %(instrument_id)s,
            %(id)s,
            %(price)s,
            %(qty)s,
            %(quote_qty)s,
            %(time)s,
            %(is_buyer_maker)s
        )
        ON CONFLICT (venue_id, instrument_id, time, id) DO NOTHING
        """

        with self._conn.cursor() as cur:
            # 映射/注册 core 维表（按 symbol 分组，避免每行查询）
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
                        "venue_id": venue_id,
                        "instrument_id": instrument_id,
                        "id": int(r.id),
                        "price": float(r.price),
                        "qty": float(r.qty),
                        "quote_qty": float(r.quote_qty),
                        "time": int(r.time),
                        "is_buyer_maker": bool(r.is_buyer_maker),
                    }
                )
            cur.executemany(sql, params)
            rc = cur.rowcount
            inserted = int(rc) if rc is not None and int(rc) >= 0 else len(batch)

        self._conn.commit()
        return int(inserted)
