-- 016_crypto_trades_readable_views.sql
--
-- 目标：
-- - 为当前 `facts_data.market` 的 trades 事实表提供“人类可读”的只读视图。
-- - 只创建当前 `market.binance_vw_*` 视图。

CREATE SCHEMA IF NOT EXISTS market;

CREATE OR REPLACE VIEW market.binance_vw_futures_um_trades AS
SELECT
    v.venue_code,
    sm.symbol,
    t.venue_id,
    t.instrument_id,
    t.time,
    ts.time_ts_utc,
    (ts.time_ts_utc AT TIME ZONE 'Asia/Shanghai') AS time_ts_cn,
    t.id,
    t.price,
    t.qty,
    t.quote_qty,
    t.is_buyer_maker
FROM market.binance_futures_um_trades t
JOIN market.shared_venue v
  ON v.venue_id = t.venue_id
CROSS JOIN LATERAL (
    SELECT to_timestamp(t.time / 1000.0) AS time_ts_utc
) ts
LEFT JOIN LATERAL (
    SELECT m.symbol
    FROM market.shared_symbol_map m
    WHERE m.venue_id = t.venue_id
      AND m.instrument_id = t.instrument_id
      AND tstzrange(
            m.effective_from,
            COALESCE(m.effective_to, 'infinity'::timestamptz),
            '[)'
          ) @> ts.time_ts_utc
    LIMIT 1
) sm ON TRUE;

CREATE OR REPLACE VIEW market.binance_vw_spot_trades AS
SELECT
    v.venue_code,
    sm.symbol,
    t.venue_id,
    t.instrument_id,
    t.time,
    ts.time_ts_utc,
    (ts.time_ts_utc AT TIME ZONE 'Asia/Shanghai') AS time_ts_cn,
    t.id,
    t.price,
    t.qty,
    t.quote_qty,
    t.is_buyer_maker,
    t.is_best_match
FROM market.binance_spot_trades t
JOIN market.shared_venue v
  ON v.venue_id = t.venue_id
CROSS JOIN LATERAL (
    SELECT to_timestamp(t.time / 1000000.0) AS time_ts_utc
) ts
LEFT JOIN LATERAL (
    SELECT m.symbol
    FROM market.shared_symbol_map m
    WHERE m.venue_id = t.venue_id
      AND m.instrument_id = t.instrument_id
      AND tstzrange(
            m.effective_from,
            COALESCE(m.effective_to, 'infinity'::timestamptz),
            '[)'
          ) @> ts.time_ts_utc
    LIMIT 1
) sm ON TRUE;
