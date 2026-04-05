"""market.shared_files 写入器（获取/创建 file_id）。

# 设计目标
# - 任何一行事实数据必须可回溯到官方相对路径 rel_path
# - rel_path 唯一，因此我们可以通过 upsert 得到稳定的 file_id
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import json
import logging
from typing import Dict, Optional

import psycopg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageFileSpec:
    rel_path: str
    source: str
    market_root: str
    market: Optional[str]
    product: Optional[str]
    frequency: Optional[str]
    dataset: Optional[str]
    symbol: Optional[str]
    interval: Optional[str]
    file_date: Optional[date]
    file_month: Optional[date]
    content_kind: str = "csv"

    # 文件属性（可选，但建议写入，用于审计/复现）
    size_bytes: Optional[int] = None
    checksum_sha256: Optional[str] = None
    downloaded_at: Optional[datetime] = None
    extracted_at: Optional[datetime] = None
    parser_version: str = "v1"

    # 快速质量统计（可选）
    row_count: Optional[int] = None
    min_event_ts: Optional[datetime] = None
    max_event_ts: Optional[datetime] = None

    # 旁路标记（例如 unverified / local_path）
    meta: dict = field(default_factory=dict)


class StorageFilesWriter:
    """market.shared_files 的最小 upsert 封装（带内存缓存）。"""

    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self._cache: Dict[str, int] = {}

    def get_or_create_file_id(self, spec: StorageFileSpec) -> int:
        sql = """
        INSERT INTO market.shared_files (
            rel_path,
            content_kind,
            source,
            market_root,
            market,
            product,
            frequency,
            dataset,
            symbol,
            interval,
            file_date,
            file_month,
            size_bytes,
            checksum_sha256,
            downloaded_at,
            extracted_at,
            parser_version,
            row_count,
            min_event_ts,
            max_event_ts,
            meta
        ) VALUES (
            %(rel_path)s,
            %(content_kind)s,
            %(source)s,
            %(market_root)s,
            %(market)s,
            %(product)s,
            %(frequency)s,
            %(dataset)s,
            %(symbol)s,
            %(interval)s,
            %(file_date)s,
            %(file_month)s,
            %(size_bytes)s,
            %(checksum_sha256)s,
            %(downloaded_at)s,
            %(extracted_at)s,
            %(parser_version)s,
            %(row_count)s,
            %(min_event_ts)s,
            %(max_event_ts)s,
            %(meta)s::jsonb
        )
        ON CONFLICT (rel_path)
        DO UPDATE SET
            content_kind = EXCLUDED.content_kind,
            source = EXCLUDED.source,
            market_root = EXCLUDED.market_root,
            market = EXCLUDED.market,
            product = EXCLUDED.product,
            frequency = EXCLUDED.frequency,
            dataset = EXCLUDED.dataset,
            symbol = EXCLUDED.symbol,
            interval = EXCLUDED.interval,
            file_date = EXCLUDED.file_date,
            file_month = EXCLUDED.file_month,

            size_bytes = COALESCE(EXCLUDED.size_bytes, market.shared_files.size_bytes),
            checksum_sha256 = COALESCE(EXCLUDED.checksum_sha256, market.shared_files.checksum_sha256),
            downloaded_at = COALESCE(EXCLUDED.downloaded_at, market.shared_files.downloaded_at),
            extracted_at = COALESCE(EXCLUDED.extracted_at, market.shared_files.extracted_at),
            parser_version = EXCLUDED.parser_version,
            row_count = COALESCE(EXCLUDED.row_count, market.shared_files.row_count),
            min_event_ts = COALESCE(EXCLUDED.min_event_ts, market.shared_files.min_event_ts),
            max_event_ts = COALESCE(EXCLUDED.max_event_ts, market.shared_files.max_event_ts),
            meta = market.shared_files.meta || EXCLUDED.meta
        RETURNING file_id
        """

        meta_json = json.dumps(spec.meta or {}, ensure_ascii=False)

        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "rel_path": spec.rel_path,
                    "content_kind": spec.content_kind,
                    "source": spec.source,
                    "market_root": spec.market_root,
                    "market": spec.market,
                    "product": spec.product,
                    "frequency": spec.frequency,
                    "dataset": spec.dataset,
                    "symbol": spec.symbol,
                    "interval": spec.interval,
                    "file_date": spec.file_date,
                    "file_month": spec.file_month,
                    "size_bytes": int(spec.size_bytes) if spec.size_bytes is not None else None,
                    "checksum_sha256": spec.checksum_sha256,
                    "downloaded_at": spec.downloaded_at,
                    "extracted_at": spec.extracted_at,
                    "parser_version": spec.parser_version,
                    "row_count": int(spec.row_count) if spec.row_count is not None else None,
                    "min_event_ts": spec.min_event_ts,
                    "max_event_ts": spec.max_event_ts,
                    "meta": meta_json,
                },
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"无法获取 file_id: {spec.rel_path}")
            file_id = int(row[0])

        self._conn.commit()
        self._cache[spec.rel_path] = file_id
        return file_id
