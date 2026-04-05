-- ==================== event-service: unified view for consumption ====================
-- 目的：
-- - 用 PG view 替代 event-service 的 unified.db（SQLite）
-- - 为下游提供“统一事件流”查询入口（UNION ALL 五张事实表）
--
-- 注意：
-- - 本文件只做 CREATE OR REPLACE VIEW，不做 DROP/TRUNCATE
-- - type 命名兼容历史 unified.db：twitter

CREATE SCHEMA IF NOT EXISTS alternative;

CREATE OR REPLACE VIEW alternative.unified_events AS
SELECT
    'news'::text AS type,
    ts,
    source_key,
    COALESCE(data->>'source_label', data->>'label', source_key) AS source_label,
    COALESCE(data->>'tag', '') AS tag,
    content,
    data,
    event_hash,
    ingested_at
FROM alternative.news
UNION ALL
SELECT
    'telegram'::text AS type,
    ts,
    source_key,
    COALESCE(data->>'chat_title', data->>'source_label', data->>'label', source_key) AS source_label,
    COALESCE(data->>'tag', '') AS tag,
    content,
    data,
    event_hash,
    ingested_at
FROM alternative.telegram
UNION ALL
SELECT
    'discord'::text AS type,
    ts,
    source_key,
    COALESCE(data->>'source_label', data->>'label', source_key) AS source_label,
    COALESCE(data->>'tag', '') AS tag,
    content,
    data,
    event_hash,
    ingested_at
FROM alternative.discord
UNION ALL
SELECT
    'calendar'::text AS type,
    c.ts,
    c.source_key,
    COALESCE(c.data->>'source_label', c.data->>'label', c.source_key) AS source_label,
    COALESCE(c.data->>'tag', '日历') AS tag,
    c.content,
    c.data,
    c.event_hash,
    c.ingested_at
FROM (
    SELECT
        (row_data.ts)::timestamptz AS ts,
        row_data.source_key,
        row_data.content,
        row_data.data,
        row_data.event_hash,
        (row_data.ingested_at)::timestamptz AS ingested_at
    FROM (
        SELECT snapshot_time, run_id, payload
        FROM alternative.investing_calendar_snapshots
        WHERE status = 'success'
        ORDER BY snapshot_time DESC, run_id DESC
        LIMIT 1
    ) s
    CROSS JOIN LATERAL jsonb_to_recordset(s.payload->'rows') AS row_data(
        ts text,
        source_key text,
        content text,
        data jsonb,
        event_hash text,
        ingested_at text
    )
) c
UNION ALL
SELECT
    'twitter'::text AS type,
    ts,
    source_key,
    COALESCE(data->>'username', data->>'source_label', data->>'label', source_key) AS source_label,
    COALESCE(data->>'tag', '推特') AS tag,
    content,
    data,
    event_hash,
    ingested_at
FROM alternative.x;
