\pset footer off

\echo '=== Binance Timescale 治理快照 ==='
SELECT 'EXT' AS kind, extname AS name, extversion AS detail
FROM pg_extension
WHERE extname = 'timescaledb';

WITH relation_expectation(kind, rel_schema, rel_name, expected_type) AS (
    VALUES
        ('active',    'market', 'binance_futures_um_candles_1m',                'BASE TABLE'),
        ('active',    'market', 'binance_futures_um_metrics_snapshot_5m',        'BASE TABLE'),
        ('active',    'market', 'binance_futures_um_trades',         'BASE TABLE'),
        ('active',    'market', 'binance_futures_um_book_ticker',    'BASE TABLE'),
        ('active',    'market', 'binance_futures_um_book_depth',     'BASE TABLE'),
        ('active',    'market', 'binance_spot_trades',               'BASE TABLE'),
        ('residual',  'market', 'binance_futures_um_metrics_atomic', 'BASE TABLE'),
        ('governance','market', 'binance_ingest_gaps',               'BASE TABLE'),
        ('passive',   'market', 'binance_cagg_spot_klines_1m',       'VIEW')
)
SELECT e.kind, e.rel_schema, e.rel_name, e.expected_type, COALESCE(t.table_type, 'MISSING') AS actual_type
FROM relation_expectation e
LEFT JOIN information_schema.tables t
  ON t.table_schema = e.rel_schema
 AND t.table_name = e.rel_name
ORDER BY e.kind, e.rel_name;

\echo '=== Binance hypertable / dimension ==='
WITH dimension_now_func AS (
    SELECT
        h.schema_name AS hypertable_schema,
        h.table_name AS hypertable_name,
        d.column_name,
        CASE
            WHEN d.integer_now_func_schema IS NULL THEN NULL
            ELSE d.integer_now_func_schema || '.' || d.integer_now_func
        END AS integer_now_func
    FROM _timescaledb_catalog.hypertable h
    JOIN _timescaledb_catalog.dimension d
      ON d.hypertable_id = h.id
)
SELECT
    h.hypertable_name,
    d.column_name,
    d.dimension_type,
    COALESCE(d.time_interval::text, '-') AS time_interval,
    COALESCE(d.integer_interval::text, '-') AS integer_interval,
    COALESCE(nf.integer_now_func, '-') AS integer_now_func,
    h.num_chunks,
    h.compression_enabled
FROM timescaledb_information.hypertables h
JOIN timescaledb_information.dimensions d
  ON d.hypertable_schema = h.hypertable_schema
 AND d.hypertable_name = h.hypertable_name
LEFT JOIN dimension_now_func nf
  ON nf.hypertable_schema = d.hypertable_schema
 AND nf.hypertable_name = d.hypertable_name
 AND nf.column_name = d.column_name
WHERE h.hypertable_schema = 'market'
  AND h.hypertable_name IN (
    'binance_futures_um_candles_1m',
    'binance_futures_um_metrics_snapshot_5m',
    'binance_futures_um_trades',
    'binance_futures_um_book_ticker',
    'binance_futures_um_book_depth',
    'binance_spot_trades',
    'binance_futures_um_metrics_atomic'
  )
ORDER BY h.hypertable_name, d.dimension_number;

\echo '=== Binance policy job / last status ==='
SELECT
    j.hypertable_name,
    j.proc_name,
    j.schedule_interval::text AS schedule_interval,
    j.config::text AS config,
    COALESCE(s.last_run_status, '-') AS last_run_status,
    COALESCE(to_char(s.last_successful_finish, 'YYYY-MM-DD HH24:MI:SSOF'), '-') AS last_successful_finish
FROM timescaledb_information.jobs j
LEFT JOIN timescaledb_information.job_stats s USING (job_id)
WHERE j.hypertable_schema = 'market'
  AND j.hypertable_name IN (
    'binance_futures_um_candles_1m',
    'binance_futures_um_metrics_snapshot_5m',
    'binance_futures_um_trades',
    'binance_futures_um_book_ticker',
    'binance_futures_um_book_depth',
    'binance_spot_trades',
    'binance_futures_um_metrics_atomic',
    'binance_cagg_spot_klines_1m'
  )
ORDER BY j.hypertable_name, j.proc_name;

\echo '=== Binance chunk / size ==='
SELECT
    h.hypertable_name,
    h.num_chunks,
    pg_size_pretty(hypertable_size(format('%I.%I', h.hypertable_schema, h.hypertable_name))) AS total_size
FROM timescaledb_information.hypertables h
WHERE h.hypertable_schema = 'market'
  AND h.hypertable_name IN (
    'binance_futures_um_candles_1m',
    'binance_futures_um_metrics_snapshot_5m',
    'binance_futures_um_trades',
    'binance_futures_um_book_ticker',
    'binance_futures_um_book_depth',
    'binance_spot_trades',
    'binance_futures_um_metrics_atomic'
  )
ORDER BY h.hypertable_name;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'market'
          AND table_name = 'binance_futures_um_candles_1m'
          AND table_type = 'BASE TABLE'
    ) THEN
        RAISE EXCEPTION '缺少物理表: market.binance_futures_um_candles_1m';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'market'
          AND table_name = 'binance_futures_um_metrics_snapshot_5m'
          AND table_type = 'BASE TABLE'
    ) THEN
        RAISE EXCEPTION '缺少物理表: market.binance_futures_um_metrics_snapshot_5m';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'market'
          AND table_name = 'binance_ingest_gaps'
          AND table_type = 'BASE TABLE'
    ) THEN
        RAISE EXCEPTION '缺少治理表: market.binance_ingest_gaps';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'market'
          AND table_name = 'binance_cagg_spot_klines_1m'
          AND table_type = 'VIEW'
    ) THEN
        RAISE EXCEPTION '缺少 passive 对象: market.binance_cagg_spot_klines_1m';
    END IF;
END$$;

DO $$
DECLARE
    mismatch_count INTEGER;
BEGIN
    WITH expected(hypertable_name, column_name, time_interval, integer_interval, integer_now_func) AS (
        VALUES
            ('binance_futures_um_candles_1m',                'bucket_ts',   '1 day',   NULL,          NULL),
            ('binance_futures_um_metrics_snapshot_5m',        'create_time', '7 days',  NULL,          NULL),
            ('binance_futures_um_metrics_atomic', 'create_time', '30 days', NULL,          NULL),
            ('binance_futures_um_trades',         'time',        NULL,      '86400000',    'market.unix_now_ms'),
            ('binance_futures_um_book_ticker',    'event_time',  NULL,      '86400000',    'market.unix_now_ms'),
            ('binance_futures_um_book_depth',     'timestamp',   NULL,      '604800000',   'market.unix_now_ms'),
            ('binance_spot_trades',               'time',        NULL,      '86400000000', 'market.unix_now_us')
    ),
    actual AS (
        SELECT
            d.hypertable_name,
            d.column_name,
            COALESCE(time_interval::text, NULL) AS time_interval,
            COALESCE(integer_interval::text, NULL) AS integer_interval,
            CASE
                WHEN c.integer_now_func_schema IS NULL THEN NULL
                ELSE c.integer_now_func_schema || '.' || c.integer_now_func
            END AS integer_now_func
        FROM timescaledb_information.dimensions d
        LEFT JOIN _timescaledb_catalog.hypertable h
          ON h.schema_name = d.hypertable_schema
         AND h.table_name = d.hypertable_name
        LEFT JOIN _timescaledb_catalog.dimension c
          ON c.hypertable_id = h.id
         AND c.column_name = d.column_name
        WHERE d.hypertable_schema = 'market'
    )
    SELECT count(*) INTO mismatch_count
    FROM expected e
    LEFT JOIN actual a USING (hypertable_name, column_name)
    WHERE COALESCE(a.time_interval, '-') <> COALESCE(e.time_interval, '-')
       OR COALESCE(a.integer_interval, '-') <> COALESCE(e.integer_interval, '-')
       OR COALESCE(a.integer_now_func, '-') <> COALESCE(e.integer_now_func, '-');

    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'Timescale dimension / integer_now_func 漂移：% 项不匹配', mismatch_count;
    END IF;
END$$;

DO $$
DECLARE
    mismatch_count INTEGER;
BEGIN
    WITH expected(hypertable_name, proc_name, config_key, expected_value) AS (
        VALUES
            ('binance_futures_um_candles_1m',                'policy_compression',                  'compress_after', '3 days'),
            ('binance_futures_um_candles_1m',                'policy_retention',                    'drop_after',     '180 days'),
            ('binance_futures_um_metrics_snapshot_5m',        'policy_compression',                  'compress_after', '7 days'),
            ('binance_futures_um_metrics_atomic', 'policy_compression',                  'compress_after', '90 days'),
            ('binance_futures_um_trades',         'policy_compression',                  'compress_after', '2592000000'),
            ('binance_futures_um_book_ticker',    'policy_compression',                  'compress_after', '259200000'),
            ('binance_futures_um_book_depth',     'policy_compression',                  'compress_after', '2592000000'),
            ('binance_spot_trades',               'policy_compression',                  'compress_after', '2592000000000'),
            ('binance_cagg_spot_klines_1m',       'policy_refresh_continuous_aggregate', 'start_offset',   '2592000000000'),
            ('binance_cagg_spot_klines_1m',       'policy_refresh_continuous_aggregate', 'end_offset',     '300000000')
    ),
    actual AS (
        SELECT
            hypertable_name,
            proc_name,
            key AS config_key,
            value AS actual_value
        FROM timescaledb_information.jobs j,
        LATERAL jsonb_each_text(j.config)
        WHERE hypertable_schema = 'market'
    )
    SELECT count(*) INTO mismatch_count
    FROM expected e
    LEFT JOIN actual a
      ON a.hypertable_name = e.hypertable_name
     AND a.proc_name = e.proc_name
     AND a.config_key = e.config_key
    WHERE COALESCE(a.actual_value, '-') <> e.expected_value;

    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'Timescale policy 配置漂移：% 项不匹配', mismatch_count;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM timescaledb_information.jobs
        WHERE hypertable_schema = 'market'
          AND hypertable_name IN (
            'binance_futures_um_metrics_snapshot_5m',
            'binance_futures_um_trades',
            'binance_futures_um_book_ticker',
            'binance_futures_um_book_depth',
            'binance_spot_trades',
            'binance_futures_um_metrics_atomic'
          )
          AND proc_name = 'policy_retention'
    ) THEN
        RAISE EXCEPTION '发现未批准的 retention policy：HF/raw/residual 当前不允许自动 retention';
    END IF;
END$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM timescaledb_information.jobs j
        LEFT JOIN timescaledb_information.job_stats s USING (job_id)
        WHERE j.hypertable_schema = 'market'
          AND j.hypertable_name IN (
            'binance_futures_um_candles_1m',
            'binance_futures_um_metrics_snapshot_5m',
            'binance_futures_um_trades',
            'binance_futures_um_book_ticker',
            'binance_futures_um_book_depth',
            'binance_spot_trades',
            'binance_futures_um_metrics_atomic',
            'binance_cagg_spot_klines_1m'
          )
          AND s.last_run_status IS DISTINCT FROM 'Success'
    ) THEN
        RAISE EXCEPTION '发现非 Success 的 Timescale policy job';
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM timescaledb_information.continuous_aggregates ca
        WHERE ca.view_schema = 'market'
          AND ca.view_name = 'binance_cagg_spot_klines_1m'
          AND ca.hypertable_schema = 'market'
          AND ca.hypertable_name = 'binance_spot_trades'
          AND ca.materialized_only = FALSE
          AND ca.finalized = TRUE
    ) THEN
        RAISE EXCEPTION 'spot_candles_1m CAGG 定义漂移';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM timescaledb_information.continuous_aggregates ca
        JOIN pg_indexes idx
          ON idx.schemaname = ca.materialization_hypertable_schema
         AND idx.tablename = ca.materialization_hypertable_name
        WHERE ca.view_schema = 'market'
          AND ca.view_name = 'binance_cagg_spot_klines_1m'
          AND idx.indexname = 'idx_cagg_spot_klines_1m_bucket_vid_iid'
    ) THEN
        RAISE EXCEPTION 'spot_candles_1m CAGG 缺少关键索引 idx_cagg_spot_klines_1m_bucket_vid_iid';
    END IF;
END$$;

\echo '✓ Binance Timescale governance audit passed'
