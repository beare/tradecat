from __future__ import annotations

"""数据血缘事件写入器（append-only）。

设计目标：
- 以 `resource_id` 作为唯一语义锚点（见 assets/contracts/resources.v1.yaml）
- 写入 governance.lineage_events（facts_data 与 derived_data 各一份）
- 幂等：event_key 唯一，重复写入自动忽略

安全约束：
- meta 禁止包含 DSN/Token/私钥等敏感信息（会被清洗/截断）
- 写血缘失败默认 fail-soft（不阻塞主写入链路）
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg import sql

from assets.common.contracts import db_contracts

LOG = logging.getLogger(__name__)


_SENSITIVE_KEY_RE = re.compile(r"(token|secret|password|passwd|dsn|private|cookie|authorization)", re.IGNORECASE)
_SENSITIVE_VAL_RE = re.compile(
    r"(?i)(postgresql|postgres)://|\bsk-[A-Za-z0-9]{16,}\b|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
)


@dataclass(frozen=True)
class LineageEvent:
    actor_id: str
    run_id: str
    action: str
    resource_id: str
    status: str
    occurred_at: datetime
    meta: dict[str, Any]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    """RFC3339-ish: 2026-01-01T00:00:00.000000Z"""
    return _utc(dt).isoformat().replace("+00:00", "Z")


def build_event_key(*, actor_id: str, run_id: str, action: str, resource_id: str, occurred_at: datetime) -> str:
    canonical = "|".join(
        [
            (actor_id or "").strip(),
            (run_id or "").strip(),
            (action or "").strip(),
            (resource_id or "").strip(),
            iso_z(occurred_at),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _statement_timeout_ms() -> int:
    raw = (os.getenv("LINEAGE_PG_STATEMENT_TIMEOUT_MS") or "").strip()
    if not raw:
        return 2000
    try:
        v = int(raw)
    except Exception:
        return 2000
    return max(int(v), 0)


def _sanitize_meta(meta: dict[str, Any] | None, *, max_bytes: int = 16_384) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}

    out: dict[str, Any] = {}
    for k, v in meta.items():
        key = str(k or "").strip()
        if not key:
            continue
        if _SENSITIVE_KEY_RE.search(key):
            continue

        # 字符串值做一次敏感模式过滤，避免误把 DSN/Key 写进 meta
        if isinstance(v, str) and _SENSITIVE_VAL_RE.search(v):
            continue

        out[key] = v

    # 控制体积（避免把大 payload 当 meta 写进去）
    try:
        blob = json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return {}

    if len(blob.encode("utf-8")) <= max_bytes:
        return out

    # 过大：降级为“只保留可判定摘要”
    minimal: dict[str, Any] = {}
    for k in ("item_count", "payload_hash", "duration_ms", "window_from", "window_to", "warning", "error", "error_text"):
        if k in out:
            minimal[k] = out.get(k)

    minimal["meta_truncated"] = True
    return minimal


def emit_lineage_event(
    *,
    dsn: str,
    actor_id: str,
    run_id: str,
    action: str,
    resource_id: str,
    status: str,
    occurred_at: datetime | None = None,
    meta: dict[str, Any] | None = None,
    connect_timeout_s: int = 3,
) -> bool:
    """写入一条血缘事件。

    返回：
    - True：成功插入（首次写入）
    - False：重复（ON CONFLICT）或写入失败

    失败语义：
    - 默认 fail-soft：不抛异常（主链可继续）
    """

    resolved_occurred_at = _utc(occurred_at or datetime.now(timezone.utc))
    clean_meta = _sanitize_meta(meta)
    event_key = build_event_key(
        actor_id=actor_id,
        run_id=run_id,
        action=action,
        resource_id=resource_id,
        occurred_at=resolved_occurred_at,
    )

    schema = db_contracts.governance_schema()
    table = db_contracts.governance_lineage_events_table_name()

    insert_sql = sql.SQL(
        """
        INSERT INTO {schema}.{table}
            (event_key, occurred_at, actor_id, run_id, action, resource_id, status, meta)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, (%s)::jsonb)
        ON CONFLICT (event_key)
        DO NOTHING
        """
    ).format(schema=sql.Identifier(schema), table=sql.Identifier(table))

    timeout_ms = int(_statement_timeout_ms())
    kwargs: dict[str, object] = {"connect_timeout": int(connect_timeout_s)}
    if timeout_ms > 0:
        kwargs["options"] = f"-c statement_timeout={timeout_ms}"

    try:
        with psycopg.connect(dsn, **kwargs) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    insert_sql,
                    (
                        event_key,
                        resolved_occurred_at,
                        (actor_id or "").strip(),
                        (run_id or "").strip(),
                        (action or "").strip(),
                        (resource_id or "").strip(),
                        (status or "").strip(),
                        json.dumps(clean_meta, ensure_ascii=False, separators=(",", ":"), default=str),
                    ),
                )
                inserted = (cur.rowcount or 0) > 0
            conn.commit()
        return inserted
    except Exception as exc:
        LOG.warning(
            "血缘事件写入失败（将忽略以保证主链可用） actor_id=%s action=%s resource_id=%s err=%s",
            (actor_id or "").strip(),
            (action or "").strip(),
            (resource_id or "").strip(),
            type(exc).__name__,
            exc_info=True,
        )
        return False
