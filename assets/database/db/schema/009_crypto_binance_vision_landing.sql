-- 009_crypto_binance_vision_landing.sql
--
-- 当前定位：
-- - 这是 facts_data 的现行 landing DDL。
-- - 只定义当前运行真相里的原子事实表：`market.*`
-- - 只创建当前 `market.binance_*` 与 `market.shared_*` 口径。
--
-- 目标：
-- - 在 schema `market` 下创建“严格对齐 Binance Vision CSV”的落库表（Landing Zone）。
-- - 该层追求：可追溯（file_id）、幂等（唯一键/主键）、可增量（按时间分区/压缩策略）。
-- - 本脚本只包含「基元/物理层（Atomic Physical）」数据集。
--
-- 重要约束（来自当前样本事实）：
-- - spot CSV：无 header，时间戳为 epoch(us)
-- - futures UM CSV：有 header，时间戳多为 epoch(ms) 或 datetime 字符串
-- - option：BVOLIndex 为 epoch(ms)，EOHSummary 为 date+hour
--
-- 命名约定：
-- - 不把 daily/monthly 写进表名；frequency 由 `market.shared_files.frequency` 表达（通过 file_id 回溯）。
-- - 事实层主路径固定为 `market.*`。

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE SCHEMA IF NOT EXISTS market;

-- ==================== market.binance_spot_trades（atomic） ====================

-- spot/daily/trades/{SYMBOL}/{SYMBOL}-trades-YYYY-MM-DD.csv
-- 样本列序（无 header）：
--   id, price, qty, quote_qty, time(us), is_buyer_maker, is_best_match
--
-- 说明（重要）：
-- - 该表是“逐笔事实表”，实时（WS）与历史回填（Vision ZIP）写入同一张表。
-- - 主键使用整型维度键（venue_id/instrument_id），避免 TEXT(symbol) 主键带来的索引放大。
CREATE TABLE IF NOT EXISTS market.binance_spot_trades (
    venue_id        BIGINT NOT NULL,
    instrument_id   BIGINT NOT NULL,
    id              BIGINT NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    quote_qty       DOUBLE PRECISION NOT NULL,
    time            BIGINT NOT NULL, -- epoch(us)
    is_buyer_maker  BOOLEAN NOT NULL,
    is_best_match   BOOLEAN,
    PRIMARY KEY (venue_id, instrument_id, time, id)
);

CREATE OR REPLACE FUNCTION market.unix_now_us() RETURNS BIGINT
LANGUAGE SQL
STABLE
AS $$
  SELECT (EXTRACT(EPOCH FROM NOW()) * 1000000)::BIGINT
$$;

SELECT create_hypertable(
    'market.binance_spot_trades',
    'time',
    chunk_time_interval => 86400000000,
    create_default_indexes => FALSE,
    if_not_exists => TRUE
);
DROP INDEX IF EXISTS market.binance_spot_trades_time_idx;
SELECT set_integer_now_func('market.binance_spot_trades', 'market.unix_now_us', replace_if_exists => TRUE);

ALTER TABLE market.binance_spot_trades
    SET (timescaledb.compress = TRUE,
         timescaledb.compress_segmentby = 'venue_id,instrument_id',
         timescaledb.compress_orderby = 'time,id');

DO $$
BEGIN
    PERFORM add_compression_policy('market.binance_spot_trades', 2592000000000);
EXCEPTION WHEN duplicate_object THEN NULL;
END$$;

-- ==================== market.binance_futures_um_trades（atomic） ====================

-- futures/um/daily/trades/{SYMBOL}/{SYMBOL}-trades-YYYY-MM-DD.csv
-- header：id,price,qty,quote_qty,time(ms),is_buyer_maker
CREATE TABLE IF NOT EXISTS market.binance_futures_um_trades (
    venue_id        BIGINT NOT NULL,
    instrument_id   BIGINT NOT NULL,
    id              BIGINT NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    quote_qty       DOUBLE PRECISION NOT NULL,
    time            BIGINT NOT NULL, -- epoch(ms)
    is_buyer_maker  BOOLEAN NOT NULL,
    PRIMARY KEY (venue_id, instrument_id, time, id)
);

CREATE OR REPLACE FUNCTION market.unix_now_ms() RETURNS BIGINT
LANGUAGE SQL
STABLE
AS $$
  SELECT (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT
$$;

SELECT create_hypertable(
    'market.binance_futures_um_trades',
    'time',
    chunk_time_interval => 86400000,
    create_default_indexes => FALSE,
    if_not_exists => TRUE
);
DROP INDEX IF EXISTS market.binance_futures_um_trades_time_idx;
SELECT set_integer_now_func('market.binance_futures_um_trades', 'market.unix_now_ms', replace_if_exists => TRUE);

ALTER TABLE market.binance_futures_um_trades
    SET (timescaledb.compress = TRUE,
         timescaledb.compress_segmentby = 'venue_id,instrument_id',
         timescaledb.compress_orderby = 'time,id');

DO $$
BEGIN
    PERFORM add_compression_policy('market.binance_futures_um_trades', 2592000000);
EXCEPTION WHEN duplicate_object THEN NULL;
END$$;

-- ==================== market.binance_futures_um_book_ticker（atomic） ====================

-- futures/um/daily/bookTicker/{SYMBOL}/{SYMBOL}-bookTicker-YYYY-MM-DD.csv
-- header：update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time(ms),event_time(ms)
CREATE TABLE IF NOT EXISTS market.binance_futures_um_book_ticker (
    venue_id         BIGINT NOT NULL,
    instrument_id    BIGINT NOT NULL,
    update_id        BIGINT NOT NULL,
    best_bid_price   DOUBLE PRECISION NOT NULL,
    best_bid_qty     DOUBLE PRECISION NOT NULL,
    best_ask_price   DOUBLE PRECISION NOT NULL,
    best_ask_qty     DOUBLE PRECISION NOT NULL,
    transaction_time BIGINT,
    event_time       BIGINT NOT NULL, -- epoch(ms)
    PRIMARY KEY (venue_id, instrument_id, event_time, update_id)
);

SELECT create_hypertable(
    'market.binance_futures_um_book_ticker',
    'event_time',
    chunk_time_interval => 86400000,
    create_default_indexes => FALSE,
    if_not_exists => TRUE
);
DROP INDEX IF EXISTS market.binance_futures_um_book_ticker_event_time_idx;
SELECT set_integer_now_func('market.binance_futures_um_book_ticker', 'market.unix_now_ms', replace_if_exists => TRUE);

ALTER TABLE market.binance_futures_um_book_ticker
    SET (timescaledb.compress = TRUE,
         timescaledb.compress_segmentby = 'venue_id,instrument_id',
         timescaledb.compress_orderby = 'event_time,update_id');

DO $$
BEGIN
    PERFORM add_compression_policy('market.binance_futures_um_book_ticker', 259200000);
EXCEPTION WHEN duplicate_object THEN NULL;
END$$;

-- ==================== market.binance_futures_um_book_depth（atomic） ====================

-- futures/um/daily/bookDepth/{SYMBOL}/{SYMBOL}-bookDepth-YYYY-MM-DD.csv
-- header：timestamp(datetime),percentage,depth,notional
CREATE TABLE IF NOT EXISTS market.binance_futures_um_book_depth (
    venue_id      BIGINT NOT NULL,
    instrument_id BIGINT NOT NULL,
    timestamp     BIGINT NOT NULL, -- epoch(ms)，导入时按 UTC 解析官方 datetime 再转 ms
    percentage    DOUBLE PRECISION NOT NULL,
    depth         DOUBLE PRECISION NOT NULL,
    notional      DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (venue_id, instrument_id, timestamp, percentage)
);

SELECT create_hypertable(
    'market.binance_futures_um_book_depth',
    'timestamp',
    chunk_time_interval => 604800000,
    create_default_indexes => FALSE,
    if_not_exists => TRUE
);
DROP INDEX IF EXISTS market.binance_futures_um_book_depth_timestamp_idx;
SELECT set_integer_now_func('market.binance_futures_um_book_depth', 'market.unix_now_ms', replace_if_exists => TRUE);

ALTER TABLE market.binance_futures_um_book_depth
    SET (timescaledb.compress = TRUE,
         timescaledb.compress_segmentby = 'venue_id,instrument_id',
         timescaledb.compress_orderby = 'timestamp,percentage');

DO $$
BEGIN
    PERFORM add_compression_policy('market.binance_futures_um_book_depth', 2592000000);
EXCEPTION WHEN duplicate_object THEN NULL;
END$$;

-- ==================== market.binance_futures_um_metrics_atomic（atomic） ====================

-- futures/um/daily/metrics/{SYMBOL}/{SYMBOL}-metrics-YYYY-MM-DD.csv
-- header：create_time(datetime),symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio
CREATE TABLE IF NOT EXISTS market.binance_futures_um_metrics_atomic (
    file_id         BIGINT NOT NULL REFERENCES market.shared_files(file_id),
    create_time     TIMESTAMPTZ NOT NULL,
    symbol          TEXT NOT NULL,
    sum_open_interest                NUMERIC(38, 12) NOT NULL,
    sum_open_interest_value          NUMERIC(38, 12) NOT NULL,
    count_toptrader_long_short_ratio NUMERIC(38, 12),
    sum_toptrader_long_short_ratio   NUMERIC(38, 12),
    count_long_short_ratio           NUMERIC(38, 12),
    sum_taker_long_short_vol_ratio   NUMERIC(38, 12),
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, create_time)
);

SELECT create_hypertable(
    'market.binance_futures_um_metrics_atomic',
    'create_time',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

ALTER TABLE market.binance_futures_um_metrics_atomic
    SET (timescaledb.compress = TRUE,
         timescaledb.compress_segmentby = 'symbol',
         timescaledb.compress_orderby = 'create_time');

DO $$
BEGIN
    PERFORM add_compression_policy('market.binance_futures_um_metrics_atomic', INTERVAL '90 days');
EXCEPTION WHEN duplicate_object THEN NULL;
END$$;

-- ==================== market.binance_option_*（atomic, 当前保留占位） ====================

-- 当前运行库已于 2026-03-28 归档 CM 原子事实链路。
-- 若未来恢复 CM，需要重新引入对应表 / view / cagg，并同步恢复 CLI 入口与 catalog。

-- option/daily/BVOLIndex/{SYMBOL}/{SYMBOL}-BVOLIndex-YYYY-MM-DD.csv
-- header：calc_time(ms),symbol,base_asset,quote_asset,index_value
CREATE TABLE IF NOT EXISTS market.binance_option_bvol_index (
    file_id         BIGINT NOT NULL REFERENCES market.shared_files(file_id),
    calc_time       BIGINT NOT NULL, -- epoch(ms)
    calc_time_ts    TIMESTAMPTZ NOT NULL,
    symbol          TEXT NOT NULL,
    base_asset      TEXT,
    quote_asset     TEXT,
    index_value     NUMERIC(38, 12) NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, calc_time_ts)
);

SELECT create_hypertable(
    'market.binance_option_bvol_index',
    'calc_time_ts',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

ALTER TABLE market.binance_option_bvol_index
    SET (timescaledb.compress = TRUE,
         timescaledb.compress_segmentby = 'symbol',
         timescaledb.compress_orderby = 'calc_time_ts');

DO $$
BEGIN
    PERFORM add_compression_policy('market.binance_option_bvol_index', INTERVAL '180 days');
EXCEPTION WHEN duplicate_object THEN NULL;
END$$;

-- option/daily/EOHSummary/{UNDERLYING}/{UNDERLYING}-EOHSummary-YYYY-MM-DD.csv
CREATE TABLE IF NOT EXISTS market.binance_option_eoh_summary (
    file_id         BIGINT NOT NULL REFERENCES market.shared_files(file_id),
    date            DATE NOT NULL,
    hour            SMALLINT NOT NULL CHECK (hour >= 0 AND hour <= 23),
    hour_ts         TIMESTAMPTZ NOT NULL,
    symbol          TEXT NOT NULL,
    underlying      TEXT NOT NULL,
    type            TEXT NOT NULL,
    strike          TEXT NOT NULL,
    open            NUMERIC(38, 12),
    high            NUMERIC(38, 12),
    low             NUMERIC(38, 12),
    close           NUMERIC(38, 12),
    volume_contracts    NUMERIC(38, 12),
    volume_usdt         NUMERIC(38, 12),
    best_bid_price   NUMERIC(38, 12),
    best_ask_price   NUMERIC(38, 12),
    best_bid_qty     NUMERIC(38, 12),
    best_ask_qty     NUMERIC(38, 12),
    best_buy_iv      NUMERIC(38, 12),
    best_sell_iv     NUMERIC(38, 12),
    mark_price       NUMERIC(38, 12),
    mark_iv          NUMERIC(38, 12),
    delta            NUMERIC(38, 12),
    gamma            NUMERIC(38, 12),
    vega             NUMERIC(38, 12),
    theta            NUMERIC(38, 12),
    openinterest_contracts NUMERIC(38, 12),
    openinterest_usdt      NUMERIC(38, 12),
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, hour_ts)
);

SELECT create_hypertable(
    'market.binance_option_eoh_summary',
    'hour_ts',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

ALTER TABLE market.binance_option_eoh_summary
    SET (timescaledb.compress = TRUE,
         timescaledb.compress_segmentby = 'underlying',
         timescaledb.compress_orderby = 'hour_ts,symbol');

DO $$
BEGIN
    PERFORM add_compression_policy('market.binance_option_eoh_summary', INTERVAL '365 days');
EXCEPTION WHEN duplicate_object THEN NULL;
END$$;
