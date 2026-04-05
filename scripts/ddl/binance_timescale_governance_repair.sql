CREATE SCHEMA IF NOT EXISTS market;

CREATE OR REPLACE FUNCTION market.unix_now_us() RETURNS BIGINT
LANGUAGE SQL
STABLE
AS $$
  SELECT (EXTRACT(EPOCH FROM NOW()) * 1000000)::BIGINT
$$;

CREATE OR REPLACE FUNCTION market.unix_now_ms() RETURNS BIGINT
LANGUAGE SQL
STABLE
AS $$
  SELECT (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT
$$;

DO $$
BEGIN
    IF to_regclass('market.binance_futures_um_trades') IS NOT NULL THEN
        PERFORM set_integer_now_func('market.binance_futures_um_trades', 'market.unix_now_ms', replace_if_exists => TRUE);
        BEGIN
            PERFORM remove_compression_policy('market.binance_futures_um_trades');
        EXCEPTION
            WHEN undefined_object THEN NULL;
        END;
        PERFORM add_compression_policy('market.binance_futures_um_trades', 2592000000);
    END IF;

    IF to_regclass('market.binance_futures_um_book_ticker') IS NOT NULL THEN
        PERFORM set_integer_now_func('market.binance_futures_um_book_ticker', 'market.unix_now_ms', replace_if_exists => TRUE);
        BEGIN
            PERFORM remove_compression_policy('market.binance_futures_um_book_ticker');
        EXCEPTION
            WHEN undefined_object THEN NULL;
        END;
        PERFORM add_compression_policy('market.binance_futures_um_book_ticker', 259200000);
    END IF;

    IF to_regclass('market.binance_futures_um_book_depth') IS NOT NULL THEN
        PERFORM set_integer_now_func('market.binance_futures_um_book_depth', 'market.unix_now_ms', replace_if_exists => TRUE);
        BEGIN
            PERFORM remove_compression_policy('market.binance_futures_um_book_depth');
        EXCEPTION
            WHEN undefined_object THEN NULL;
        END;
        PERFORM add_compression_policy('market.binance_futures_um_book_depth', 2592000000);
    END IF;

    IF to_regclass('market.binance_spot_trades') IS NOT NULL THEN
        PERFORM set_integer_now_func('market.binance_spot_trades', 'market.unix_now_us', replace_if_exists => TRUE);
        BEGIN
            PERFORM remove_compression_policy('market.binance_spot_trades');
        EXCEPTION
            WHEN undefined_object THEN NULL;
        END;
        PERFORM add_compression_policy('market.binance_spot_trades', 2592000000000);
    END IF;

    IF to_regclass('market.binance_cagg_spot_klines_1m') IS NOT NULL THEN
        BEGIN
            PERFORM remove_continuous_aggregate_policy('market.binance_cagg_spot_klines_1m');
        EXCEPTION
            WHEN undefined_object THEN NULL;
        END;
        PERFORM add_continuous_aggregate_policy(
            'market.binance_cagg_spot_klines_1m',
            start_offset => 2592000000000::BIGINT,
            end_offset => 300000000::BIGINT,
            schedule_interval => INTERVAL '1 minute'
        );
    END IF;
END$$;
