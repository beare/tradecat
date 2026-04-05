"""market.shared_* 审计写入器（导入批次 / 错误 / 文件修订）。

目标：
- 让“下载/导入”具备可追溯证据链：批次、错误、文件替换历史。
- 不污染行情事实表（raw trades 仍保持极简）。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Optional

import psycopg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportBatchSpec:
    source: str
    note: Optional[str] = None
    meta: dict[str, Any] | None = None


class ImportMetaWriter:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def start_batch(self, spec: ImportBatchSpec) -> int:
        sql = """
        INSERT INTO market.shared_import_batches (
          source, status, note, meta
        ) VALUES (
          %(source)s, 'running', %(note)s, %(meta)s::jsonb
        )
        RETURNING batch_id
        """
        meta_json = json.dumps(spec.meta or {}, ensure_ascii=False)
        with self._conn.cursor() as cur:
            cur.execute(sql, {"source": spec.source, "note": spec.note, "meta": meta_json})
            row = cur.fetchone()
            if not row:
                raise RuntimeError("无法创建 import_batch")
            batch_id = int(row[0])
        self._conn.commit()
        return batch_id

    def finish_batch(self, batch_id: int, *, status: str, note: Optional[str] = None, meta: dict[str, Any] | None = None) -> None:
        sql = """
        UPDATE market.shared_import_batches
        SET status = %(status)s,
            note = COALESCE(%(note)s, note),
            meta = meta || %(meta)s::jsonb,
            finished_at = NOW()
        WHERE batch_id = %(batch_id)s
        """
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        with self._conn.cursor() as cur:
            cur.execute(sql, {"batch_id": int(batch_id), "status": status, "note": note, "meta": meta_json})
        self._conn.commit()

    def get_file_record(self, rel_path: str) -> tuple[int, Optional[str]]:
        sql = "SELECT file_id, checksum_sha256 FROM market.shared_files WHERE rel_path = %(rel_path)s"
        with self._conn.cursor() as cur:
            cur.execute(sql, {"rel_path": rel_path})
            row = cur.fetchone()
            if not row:
                raise KeyError(rel_path)
            return int(row[0]), row[1]

    def get_existing_checksum(self, rel_path: str) -> Optional[str]:
        sql = "SELECT checksum_sha256 FROM market.shared_files WHERE rel_path = %(rel_path)s"
        with self._conn.cursor() as cur:
            cur.execute(sql, {"rel_path": rel_path})
            row = cur.fetchone()
            if not row:
                return None
            return row[0]

    def insert_file_revision(self, *, rel_path: str, old_checksum_sha256: Optional[str], new_checksum_sha256: str, note: Optional[str] = None) -> None:
        if old_checksum_sha256 and old_checksum_sha256 == new_checksum_sha256:
            return

        sql = """
        INSERT INTO market.shared_file_revisions (
          rel_path, old_checksum_sha256, new_checksum_sha256, note
        ) VALUES (
          %(rel_path)s, %(old_checksum)s, %(new_checksum)s, %(note)s
        )
        """
        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "rel_path": rel_path,
                    "old_checksum": old_checksum_sha256,
                    "new_checksum": new_checksum_sha256,
                    "note": note,
                },
            )
        self._conn.commit()

    def insert_import_error(
        self,
        *,
        batch_id: Optional[int],
        file_id: Optional[int],
        error_type: str,
        message: str,
        detail: Optional[str] = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        sql = """
        INSERT INTO market.shared_import_errors (
          batch_id, file_id, error_type, message, detail, meta
        ) VALUES (
          %(batch_id)s, %(file_id)s, %(error_type)s, %(message)s, %(detail)s, %(meta)s::jsonb
        )
        """
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "batch_id": int(batch_id) if batch_id is not None else None,
                    "file_id": int(file_id) if file_id is not None else None,
                    "error_type": error_type,
                    "message": message,
                    "detail": detail,
                    "meta": meta_json,
                },
            )
        self._conn.commit()
