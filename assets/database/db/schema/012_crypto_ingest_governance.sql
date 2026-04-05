-- 012_crypto_ingest_governance.sql
--
-- 目标：
-- - 在 `facts_data.market` 下创建当前 Binance Vision 采集旁路治理表：
--   - `market.binance_ingest_runs`
--   - `market.binance_ingest_watermark`
--   - `market.binance_ingest_gaps`
--
-- 说明：
-- - 这些表不属于行情事实本体，但属于事实采集治理主链。
-- - 只定义当前 `market.binance_ingest_*` 治理结构。

CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS market.binance_ingest_runs (
    run_id          BIGSERIAL PRIMARY KEY,
    exchange        TEXT NOT NULL,
    dataset         TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK (mode IN ('realtime','backfill','repair')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL CHECK (status IN ('running','success','failed','partial')),
    error_message   TEXT,
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_market_binance_ingest_runs_exchange_dataset_started_at
ON market.binance_ingest_runs (exchange, dataset, started_at DESC);

CREATE TABLE IF NOT EXISTS market.binance_ingest_watermark (
    exchange        TEXT NOT NULL,
    dataset         TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    last_time       BIGINT NOT NULL,
    last_id         BIGINT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (exchange, dataset, symbol)
);

CREATE INDEX IF NOT EXISTS idx_market_binance_ingest_watermark_updated_at
ON market.binance_ingest_watermark (updated_at DESC);

CREATE TABLE IF NOT EXISTS market.binance_ingest_gaps (
    gap_id          BIGSERIAL PRIMARY KEY,
    exchange        TEXT NOT NULL,
    dataset         TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    start_time      BIGINT NOT NULL,
    end_time        BIGINT NOT NULL,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','repairing','closed','ignored')),
    reason          TEXT,
    run_id          BIGINT REFERENCES market.binance_ingest_runs(run_id),
    UNIQUE (exchange, dataset, symbol, start_time, end_time)
);

CREATE INDEX IF NOT EXISTS idx_market_binance_ingest_gaps_status_detected_at
ON market.binance_ingest_gaps (status, detected_at DESC);
