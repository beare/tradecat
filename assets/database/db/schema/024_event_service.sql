-- ==================== event-service: per-service tables under schema "alternative" ====================
-- 目的：
-- - 将 event-service 各子服务的 SQLite “事实数据表”迁移/落盘到 PostgreSQL（facts_data）
-- - 按“每个服务独立一张事实表”的策略隔离（不做全局单表聚合）
--
-- 约定（你要求的层级）：
-- - schema：alternative
-- - facts tables：news / telegram / discord / x
-- - snapshot tables：investing_calendar_snapshots / hyperliquid_address_snapshots
-- - 统计/维表（channels/guilds 等）建议用 VIEW 从事实表聚合生成，不再落物理表
--
-- 安全性：
-- - 本文件只做 CREATE IF NOT EXISTS，不做 DROP/TRUNCATE

CREATE SCHEMA IF NOT EXISTS alternative;

-- -------------------- 新闻 --------------------
CREATE TABLE IF NOT EXISTS alternative.news (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL,
    source_key  TEXT NOT NULL,
    content     TEXT,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_hash  TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(event_hash)
);
CREATE INDEX IF NOT EXISTS idx_alternative_news_ts ON alternative.news (ts DESC);
CREATE INDEX IF NOT EXISTS idx_alternative_news_source_ts ON alternative.news (source_key, ts DESC);

-- -------------------- Telegram --------------------
CREATE TABLE IF NOT EXISTS alternative.telegram (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL,
    source_key  TEXT NOT NULL,
    content     TEXT,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_hash  TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(event_hash)
);
CREATE INDEX IF NOT EXISTS idx_alternative_telegram_ts ON alternative.telegram (ts DESC);
CREATE INDEX IF NOT EXISTS idx_alternative_telegram_source_ts ON alternative.telegram (source_key, ts DESC);

-- -------------------- Discord --------------------
CREATE TABLE IF NOT EXISTS alternative.discord (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL,
    source_key  TEXT NOT NULL,
    content     TEXT,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_hash  TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(event_hash)
);
CREATE INDEX IF NOT EXISTS idx_alternative_discord_ts ON alternative.discord (ts DESC);
CREATE INDEX IF NOT EXISTS idx_alternative_discord_source_ts ON alternative.discord (source_key, ts DESC);

-- -------------------- 日历快照（investing-service；每轮任务单行 JSONB） --------------------
CREATE TABLE IF NOT EXISTS alternative.investing_calendar_snapshots (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    snapshot_time TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL DEFAULT 'investing.com',
    status TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    payload_schema_version INTEGER NOT NULL DEFAULT 1,
    payload_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alternative_investing_calendar_snapshots_time ON alternative.investing_calendar_snapshots (snapshot_time DESC);
CREATE INDEX IF NOT EXISTS idx_alternative_investing_calendar_snapshots_status_time ON alternative.investing_calendar_snapshots (status, snapshot_time DESC);
CREATE INDEX IF NOT EXISTS idx_alternative_investing_calendar_snapshots_payload_gin ON alternative.investing_calendar_snapshots USING gin (payload jsonb_path_ops);

-- -------------------- Hyper snapshots（每轮任务单行 JSONB 快照） --------------------
CREATE TABLE IF NOT EXISTS alternative.hyperliquid_address_snapshots (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    snapshot_time TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL DEFAULT 'coinglass',
    status TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    payload_schema_version INTEGER NOT NULL DEFAULT 1,
    payload_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alternative_hl_address_snapshots_time ON alternative.hyperliquid_address_snapshots (snapshot_time DESC);
CREATE INDEX IF NOT EXISTS idx_alternative_hl_address_snapshots_status_time ON alternative.hyperliquid_address_snapshots (status, snapshot_time DESC);
CREATE INDEX IF NOT EXISTS idx_alternative_hl_address_snapshots_payload_gin ON alternative.hyperliquid_address_snapshots USING gin (payload jsonb_path_ops);

-- -------------------- X（tweets） --------------------
CREATE TABLE IF NOT EXISTS alternative.x (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL,
    source_key  TEXT NOT NULL,
    content     TEXT,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_hash  TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(event_hash)
);
CREATE INDEX IF NOT EXISTS idx_alternative_x_ts ON alternative.x (ts DESC);
CREATE INDEX IF NOT EXISTS idx_alternative_x_source_ts ON alternative.x (source_key, ts DESC);

-- -------------------- X group state（telegram-service-unified：分组开关） --------------------
-- 说明：
-- - 当前实现已迁到项目内文件：`core/alternative/x/state/group_state.json`
-- - 默认：未显式配置的 group_id 视为 enabled=true（由应用层回填默认值）
-- - facts_data 不再落库该运行态对象
