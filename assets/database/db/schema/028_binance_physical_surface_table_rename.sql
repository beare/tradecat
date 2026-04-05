-- Binance LF 物理表收口迁移
--
-- 目标：
-- - market.binance_candles_1m -> market.binance_futures_um_candles_1m
-- - market.binance_futures_metrics_5m -> market.binance_futures_um_metrics_snapshot_5m
--
-- 说明：
-- - 该脚本是 oneoff 迁移，不进入 bootstrap stack
-- - 若新旧表同时存在，直接失败，避免双真相源

DO $$
BEGIN
    IF to_regclass('market.binance_candles_1m') IS NOT NULL
       AND to_regclass('market.binance_futures_um_candles_1m') IS NOT NULL THEN
        RAISE EXCEPTION '检测到旧/新 candles 表同时存在，请先人工清理双真相源';
    END IF;

    IF to_regclass('market.binance_futures_metrics_5m') IS NOT NULL
       AND to_regclass('market.binance_futures_um_metrics_snapshot_5m') IS NOT NULL THEN
        RAISE EXCEPTION '检测到旧/新 metrics 表同时存在，请先人工清理双真相源';
    END IF;

    IF to_regclass('market.binance_candles_1m') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE market.binance_candles_1m RENAME TO binance_futures_um_candles_1m';
    END IF;

    IF to_regclass('market.binance_futures_metrics_5m') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE market.binance_futures_metrics_5m RENAME TO binance_futures_um_metrics_snapshot_5m';
    END IF;
END$$;

ALTER TABLE IF EXISTS market.binance_futures_um_candles_1m
    SET (
        timescaledb.compress = TRUE,
        timescaledb.compress_segmentby = 'exchange,symbol',
        timescaledb.compress_orderby = 'bucket_ts'
    );

DO $$
BEGIN
    BEGIN
        PERFORM remove_compression_policy('market.binance_futures_um_candles_1m');
    EXCEPTION
        WHEN undefined_object THEN NULL;
    END;
    PERFORM add_compression_policy('market.binance_futures_um_candles_1m', INTERVAL '3 days');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END$$;

DO $$
BEGIN
    BEGIN
        PERFORM remove_retention_policy('market.binance_futures_um_candles_1m');
    EXCEPTION
        WHEN undefined_object THEN NULL;
    END;
    PERFORM add_retention_policy('market.binance_futures_um_candles_1m', INTERVAL '180 days');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END$$;

ALTER TABLE IF EXISTS market.binance_futures_um_metrics_snapshot_5m
    SET (
        timescaledb.compress = TRUE,
        timescaledb.compress_segmentby = 'symbol',
        timescaledb.compress_orderby = 'create_time DESC'
    );

DO $$
BEGIN
    BEGIN
        PERFORM remove_compression_policy('market.binance_futures_um_metrics_snapshot_5m');
    EXCEPTION
        WHEN undefined_object THEN NULL;
    END;
    PERFORM add_compression_policy('market.binance_futures_um_metrics_snapshot_5m', INTERVAL '7 days');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END$$;

\ir ../setup_candle_notify_trigger.sql

SELECT
    to_regclass('market.binance_futures_um_candles_1m') AS futures_um_candles_1m,
    to_regclass('market.binance_futures_um_metrics_snapshot_5m') AS futures_um_metrics_snapshot_5m;
