-- 019_crypto_raw_trades_sanity_checks.sql
--
-- 目标：
-- - 为当前 `market.binance_*_trades` 事实表补齐最小 sanity CHECK。
-- - 不再处理已归档的 CM 事实表，只校验当前 `market.binance_*` 口径。

CREATE SCHEMA IF NOT EXISTS market;

DO $$
BEGIN
    IF to_regclass('market.binance_futures_um_trades') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'chk_futures_um_trades_sanity'
              AND conrelid = 'market.binance_futures_um_trades'::regclass
        ) THEN
            EXECUTE $sql$
                ALTER TABLE market.binance_futures_um_trades
                ADD CONSTRAINT chk_futures_um_trades_sanity
                CHECK (
                  venue_id > 0
                  AND instrument_id > 0
                  AND time > 0
                  AND id >= 0
                  AND price >= 0
                  AND qty >= 0
                  AND quote_qty >= 0
                )
                NOT VALID
            $sql$;
        END IF;
    END IF;

    IF to_regclass('market.binance_spot_trades') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'chk_spot_trades_sanity'
              AND conrelid = 'market.binance_spot_trades'::regclass
        ) THEN
            EXECUTE $sql$
                ALTER TABLE market.binance_spot_trades
                ADD CONSTRAINT chk_spot_trades_sanity
                CHECK (
                  venue_id > 0
                  AND instrument_id > 0
                  AND time > 0
                  AND id >= 0
                  AND price >= 0
                  AND qty >= 0
                  AND quote_qty >= 0
                )
                NOT VALID
            $sql$;
        END IF;
    END IF;
END
$$;
