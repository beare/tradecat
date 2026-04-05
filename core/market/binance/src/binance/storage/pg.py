"""PostgreSQL 写入工具（psycopg3）。

# 约束
# - 仅负责最小连接与执行，不引入复杂 ORM
# - 上层负责幂等语义（ON CONFLICT）与批量策略
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg

logger = logging.getLogger(__name__)


def connect(database_url: str) -> psycopg.Connection:
    if not database_url:
        raise ValueError("DATABASE_URL 为空，无法连接 PostgreSQL")
    return psycopg.connect(database_url)


@contextmanager
def cursor(conn: psycopg.Connection) -> Iterator[psycopg.Cursor]:
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()
