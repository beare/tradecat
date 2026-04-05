-- 008_multi_market_core_and_storage.sql
--
-- 目标：
-- - 在 `facts_data.market` 下创建当前运行库使用的共享维表与导入追溯表。
-- - 该脚本只定义当前 `market.shared_*` 元数据与治理表。
--
-- 说明：
-- - 这是当前 facts_data 的 canonical DDL 之一。
-- - 若你在处理历史迁移，请改用 `scripts/ddl/facts_data_layout.sql` / `scripts/ddl/facts_data_converge_layout.sql`。

CREATE SCHEMA IF NOT EXISTS market;

-- ==================== shared：跨市场共享维表 ====================

CREATE TABLE IF NOT EXISTS market.shared_venue (
    venue_id        BIGSERIAL PRIMARY KEY,
    venue_code      TEXT NOT NULL UNIQUE,
    venue_name      TEXT NOT NULL,
    venue_type      TEXT NOT NULL DEFAULT 'exchange',
    country_code    TEXT,
    timezone        TEXT NOT NULL DEFAULT 'UTC',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market.shared_currency (
    currency_code   TEXT PRIMARY KEY,
    currency_name   TEXT,
    decimals        INTEGER,
    currency_type   TEXT NOT NULL DEFAULT 'fiat',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market.shared_instrument (
    instrument_id       BIGSERIAL PRIMARY KEY,
    asset_class         TEXT NOT NULL,
    instrument_type     TEXT NOT NULL,
    base_currency       TEXT,
    quote_currency      TEXT,
    underlying_id       BIGINT REFERENCES market.shared_instrument(instrument_id),
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market.shared_symbol_map (
    venue_id        BIGINT NOT NULL REFERENCES market.shared_venue(venue_id),
    symbol          TEXT NOT NULL,
    instrument_id   BIGINT NOT NULL REFERENCES market.shared_instrument(instrument_id),
    effective_from  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_to    TIMESTAMPTZ,
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (venue_id, symbol, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_market_shared_symbol_map_instrument_id
ON market.shared_symbol_map (instrument_id);

CREATE TABLE IF NOT EXISTS market.shared_calendar_session (
    calendar_code   TEXT NOT NULL,
    session_date    DATE NOT NULL,
    open_ts         TIMESTAMPTZ,
    close_ts        TIMESTAMPTZ,
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (calendar_code, session_date)
);

-- ==================== shared：文件追溯 / 导入审计 ====================

CREATE TABLE IF NOT EXISTS market.shared_files (
    file_id             BIGSERIAL PRIMARY KEY,
    rel_path            TEXT NOT NULL UNIQUE,
    content_kind        TEXT NOT NULL DEFAULT 'csv' CHECK (content_kind IN ('zip','csv','parquet','unknown')),
    parent_file_id      BIGINT REFERENCES market.shared_files(file_id),
    source              TEXT NOT NULL,
    market_root         TEXT NOT NULL,
    market              TEXT,
    product             TEXT,
    frequency           TEXT,
    dataset             TEXT,
    symbol              TEXT,
    interval            TEXT,
    file_date           DATE,
    file_month          DATE,
    size_bytes          BIGINT,
    checksum_sha256     TEXT,
    downloaded_at       TIMESTAMPTZ,
    extracted_at        TIMESTAMPTZ,
    parser_version      TEXT NOT NULL DEFAULT 'v1',
    row_count           BIGINT,
    min_event_ts        TIMESTAMPTZ,
    max_event_ts        TIMESTAMPTZ,
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_shared_files_source ON market.shared_files (source);
CREATE INDEX IF NOT EXISTS idx_market_shared_files_market_root ON market.shared_files (market_root);
CREATE INDEX IF NOT EXISTS idx_market_shared_files_dataset ON market.shared_files (dataset);
CREATE INDEX IF NOT EXISTS idx_market_shared_files_symbol ON market.shared_files (symbol);
CREATE INDEX IF NOT EXISTS idx_market_shared_files_file_date ON market.shared_files (file_date);

CREATE TABLE IF NOT EXISTS market.shared_file_revisions (
    revision_id         BIGSERIAL PRIMARY KEY,
    rel_path            TEXT NOT NULL,
    old_checksum_sha256 TEXT,
    new_checksum_sha256 TEXT NOT NULL,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note                TEXT
);

CREATE INDEX IF NOT EXISTS idx_market_shared_file_revisions_rel_path
ON market.shared_file_revisions (rel_path);

CREATE TABLE IF NOT EXISTS market.shared_import_batches (
    batch_id            BIGSERIAL PRIMARY KEY,
    source              TEXT NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed','partial')),
    note                TEXT,
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS market.shared_import_errors (
    error_id            BIGSERIAL PRIMARY KEY,
    batch_id            BIGINT REFERENCES market.shared_import_batches(batch_id),
    file_id             BIGINT REFERENCES market.shared_files(file_id),
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    error_type          TEXT NOT NULL,
    message             TEXT NOT NULL,
    detail              TEXT,
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb
);
