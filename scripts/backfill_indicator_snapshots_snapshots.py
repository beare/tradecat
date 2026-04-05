from __future__ import annotations

"""
将 legacy indicator_snapshots 多表数据回灌到 snapshots 单表。

用途：
- INDICATOR_PG_LAYOUT=dual|snapshot 前的“存量数据补齐”
- 用于对照新旧读口径是否一致（不依赖 trading-service 重新跑一轮）

注意：
- 本脚本只做 INSERT ... SELECT，不会删除 legacy 表数据。
- 默认使用 assets/config/.env（存在则加载；不覆盖外部 env）。
"""

import argparse
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from psycopg import connect, sql


def _find_project_root(start: Path) -> Path:
    current = start.resolve()
    for path in [current] + list(current.parents):
        if (path / "assets").is_dir() and (path / "core").is_dir() and (path / "plugins").is_dir():
            return path
    return current.parents[4]


PROJECT_ROOT = _find_project_root(Path(__file__))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from assets.common.contracts.db_contracts import indicator_schema, indicator_snapshots_table_name


def _load_env(project_root: Path) -> None:
    env_path = project_root / "assets" / "config" / ".env"
    if not env_path.exists():
        env_path = project_root / "config" / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _derived_dsn() -> str:
    dsn = (os.getenv("DERIVED_DATABASE_URL") or os.getenv("INDICATOR_DATABASE_URL") or "").strip()
    if dsn:
        return dsn
    base = (os.getenv("DATABASE_URL") or os.getenv("FACTS_DATABASE_URL") or "").strip()
    if "://" in base and "/" in base:
        # 粗暴但足够：替换最后一个 /<db>
        return base.rsplit("/", 1)[0] + "/derived_data"
    return "postgresql://postgres:postgres@localhost:5433/derived_data"


def main() -> int:
    _load_env(PROJECT_ROOT)
    default_schema = indicator_schema()
    default_snapshots_table = indicator_snapshots_table_name()

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", default=default_schema)
    parser.add_argument("--snapshots-table", default=default_snapshots_table)
    parser.add_argument("--only-table", default="", help="只回灌单表（例如：基础数据同步器.py）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要回灌的表，不执行写入")
    args = parser.parse_args()

    schema = (args.schema or "indicator_snapshots").strip()
    snapshots_table = (args.snapshots_table or "snapshots").strip()
    only_table = (args.only_table or "").strip()

    dsn = _derived_dsn()
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            # 确认 snapshots 表存在
            cur.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema=%s AND table_name=%s
                """,
                (schema, snapshots_table),
            )
            if cur.fetchone() is None:
                raise SystemExit(
                    f"缺少 snapshots 表：{schema}.{snapshots_table}（请先执行 assets/database/db/schema/026_indicator_snapshots_snapshots.sql）"
                )

            # 取 legacy 表清单
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema=%s AND table_type='BASE TABLE'
                ORDER BY table_name
                """,
                (schema,),
            )
            tables = [str(r[0]) for r in (cur.fetchall() or []) if r and r[0]]

            # 排除 snapshots 自身
            tables = [t for t in tables if t != snapshots_table]
            if only_table:
                tables = [t for t in tables if t == only_table]

            if not tables:
                print("no_tables_to_backfill")
                return 0

            for t in tables:
                print(f"backfill: {schema}.{t} -> {schema}.{snapshots_table}")
                if args.dry_run:
                    continue

                # INSERT ... SELECT：payload 使用 to_jsonb(t)
                q = sql.SQL(
                    """
                    INSERT INTO {snapshots_tbl} (indicator, symbol, period, data_time, payload)
                    SELECT %s, "交易对", "周期", "数据时间", to_jsonb(src)
                    FROM {src_tbl} AS src
                    ON CONFLICT (indicator, symbol, period, data_time)
                    DO UPDATE SET payload=EXCLUDED.payload, collected_at=now()
                    """
                ).format(
                    snapshots_tbl=sql.Identifier(schema, snapshots_table),
                    src_tbl=sql.Identifier(schema, t),
                )
                cur.execute(q, (t,))
                conn.commit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
