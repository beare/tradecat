-- 017_crypto_trades_cagg_klines.sql
--
-- 目标：
-- - 基于当前 `market.binance_*_trades` 事实表构建 Continuous Aggregates。
-- - 只创建当前 `market.binance_cagg_*` 对象。

CREATE SCHEMA IF NOT EXISTS market;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM timescaledb_information.continuous_aggregates
        WHERE view_schema = 'market'
          AND view_name = 'binance_cagg_futures_um_klines_1m'
    ) THEN
        EXECUTE $sql$
        CREATE MATERIALIZED VIEW market.binance_cagg_futures_um_klines_1m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT
            time_bucket(60000, time) AS bucket_ms,
            venue_id,
            instrument_id,
            first(price, id) AS open,
            max(price) AS high,
            min(price) AS low,
            last(price, id) AS close,
            sum(qty) AS volume,
            sum(quote_qty) AS quote_volume,
            count(*) AS trade_count,
            sum(qty) FILTER (WHERE is_buyer_maker = false) AS taker_buy_volume,
            sum(quote_qty) FILTER (WHERE is_buyer_maker = false) AS taker_buy_quote_volume,
            min(id) AS first_id,
            max(id) AS last_id
        FROM market.binance_futures_um_trades
        GROUP BY 1,2,3
        WITH NO DATA
        $sql$;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_market_binance_cagg_futures_um_klines_1m_bucket_vid_iid
ON market.binance_cagg_futures_um_klines_1m (bucket_ms, venue_id, instrument_id);

DO $$
BEGIN
    PERFORM add_continuous_aggregate_policy(
        'market.binance_cagg_futures_um_klines_1m',
        start_offset => 2592000000::BIGINT,
        end_offset => 300000::BIGINT,
        schedule_interval => INTERVAL '1 minute'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_parameter_value THEN
        IF POSITION('refresh policy already exists' IN SQLERRM) > 0 THEN
            NULL;
        ELSE
            RAISE;
        END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM timescaledb_information.continuous_aggregates
        WHERE view_schema = 'market'
          AND view_name = 'binance_cagg_spot_klines_1m'
    ) THEN
        EXECUTE $sql$
        CREATE MATERIALIZED VIEW market.binance_cagg_spot_klines_1m
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT
            time_bucket(60000000, time) AS bucket_us,
            venue_id,
            instrument_id,
            first(price, id) AS open,
            max(price) AS high,
            min(price) AS low,
            last(price, id) AS close,
            sum(qty) AS volume,
            sum(quote_qty) AS quote_volume,
            count(*) AS trade_count,
            sum(qty) FILTER (WHERE is_buyer_maker = false) AS taker_buy_volume,
            sum(quote_qty) FILTER (WHERE is_buyer_maker = false) AS taker_buy_quote_volume,
            min(id) AS first_id,
            max(id) AS last_id
        FROM market.binance_spot_trades
        GROUP BY 1,2,3
        WITH NO DATA
        $sql$;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_market_binance_cagg_spot_klines_1m_bucket_vid_iid
ON market.binance_cagg_spot_klines_1m (bucket_us, venue_id, instrument_id);

DO $$
BEGIN
    PERFORM add_continuous_aggregate_policy(
        'market.binance_cagg_spot_klines_1m',
        start_offset => 2592000000000::BIGINT,
        end_offset => 300000000::BIGINT,
        schedule_interval => INTERVAL '1 minute'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN invalid_parameter_value THEN
        IF POSITION('refresh policy already exists' IN SQLERRM) > 0 THEN
            NULL;
        ELSE
            RAISE;
        END IF;
END$$;
