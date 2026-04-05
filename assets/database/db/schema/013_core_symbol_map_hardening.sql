-- 013_core_symbol_map_hardening.sql
--
-- 目标：
-- - 把 `market.shared_symbol_map` 的语义正确性写死在数据库层。
-- - 这是当前 `facts_data.market` 口径，不再使用历史 `core.symbol_map`。

CREATE SCHEMA IF NOT EXISTS market;

CREATE UNIQUE INDEX IF NOT EXISTS uq_market_shared_symbol_map_active
ON market.shared_symbol_map (venue_id, symbol)
WHERE effective_to IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_market_shared_symbol_map_active_instrument
ON market.shared_symbol_map (venue_id, instrument_id)
WHERE effective_to IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_market_shared_symbol_map_effective_window'
          AND conrelid = 'market.shared_symbol_map'::regclass
    ) THEN
        EXECUTE
            'ALTER TABLE market.shared_symbol_map ' ||
            'ADD CONSTRAINT chk_market_shared_symbol_map_effective_window ' ||
            'CHECK (effective_to IS NULL OR effective_to > effective_from)';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION market.trg_shared_symbol_map_enforce_non_overlapping()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    new_range tstzrange;
BEGIN
    new_range := tstzrange(
        NEW.effective_from,
        COALESCE(NEW.effective_to, 'infinity'::timestamptz),
        '[)'
    );

    IF EXISTS (
        SELECT 1
        FROM market.shared_symbol_map s
        WHERE s.venue_id = NEW.venue_id
          AND s.symbol = NEW.symbol
          AND (TG_OP = 'INSERT' OR (s.venue_id, s.symbol, s.effective_from) <> (OLD.venue_id, OLD.symbol, OLD.effective_from))
          AND tstzrange(
                s.effective_from,
                COALESCE(s.effective_to, 'infinity'::timestamptz),
                '[)'
              ) && new_range
    ) THEN
        RAISE EXCEPTION
            'market.shared_symbol_map 窗口重叠: (venue_id=% symbol=%) effective_from=% effective_to=%',
            NEW.venue_id,
            NEW.symbol,
            NEW.effective_from,
            NEW.effective_to;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM market.shared_symbol_map s
        WHERE s.venue_id = NEW.venue_id
          AND s.instrument_id = NEW.instrument_id
          AND (TG_OP = 'INSERT' OR (s.venue_id, s.symbol, s.effective_from) <> (OLD.venue_id, OLD.symbol, OLD.effective_from))
          AND tstzrange(
                s.effective_from,
                COALESCE(s.effective_to, 'infinity'::timestamptz),
                '[)'
              ) && new_range
    ) THEN
        RAISE EXCEPTION
            'market.shared_symbol_map 窗口重叠: (venue_id=% instrument_id=%) effective_from=% effective_to=%',
            NEW.venue_id,
            NEW.instrument_id,
            NEW.effective_from,
            NEW.effective_to;
    END IF;

    RETURN NEW;
END
$$;

DO $$
DECLARE
    have_btree_gist BOOLEAN;
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'excl_market_shared_symbol_map_symbol_window'
          AND conrelid = 'market.shared_symbol_map'::regclass
    ) AND EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'excl_market_shared_symbol_map_instrument_window'
          AND conrelid = 'market.shared_symbol_map'::regclass
    ) THEN
        RETURN;
    END IF;

    have_btree_gist := EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'btree_gist');
    IF NOT have_btree_gist THEN
        BEGIN
            EXECUTE 'CREATE EXTENSION IF NOT EXISTS btree_gist';
            have_btree_gist := TRUE;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_file OR feature_not_supported THEN
                have_btree_gist := FALSE;
                RAISE NOTICE 'btree_gist 不可用，降级为 trigger 检查 market.shared_symbol_map 窗口不重叠';
        END;
    END IF;

    IF NOT have_btree_gist THEN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_trigger
            WHERE tgname = 'trg_market_shared_symbol_map_non_overlapping'
              AND tgrelid = 'market.shared_symbol_map'::regclass
        ) THEN
            EXECUTE $sql$
                CREATE TRIGGER trg_market_shared_symbol_map_non_overlapping
                BEFORE INSERT OR UPDATE ON market.shared_symbol_map
                FOR EACH ROW EXECUTE FUNCTION market.trg_shared_symbol_map_enforce_non_overlapping()
            $sql$;
        END IF;
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'excl_market_shared_symbol_map_symbol_window'
          AND conrelid = 'market.shared_symbol_map'::regclass
    ) THEN
        EXECUTE $sql$
            ALTER TABLE market.shared_symbol_map
            ADD CONSTRAINT excl_market_shared_symbol_map_symbol_window
            EXCLUDE USING gist (
              venue_id WITH =,
              symbol WITH =,
              tstzrange(
                effective_from,
                COALESCE(effective_to, 'infinity'::timestamptz),
                '[)'
              ) WITH &&
            )
        $sql$;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'excl_market_shared_symbol_map_instrument_window'
          AND conrelid = 'market.shared_symbol_map'::regclass
    ) THEN
        EXECUTE $sql$
            ALTER TABLE market.shared_symbol_map
            ADD CONSTRAINT excl_market_shared_symbol_map_instrument_window
            EXCLUDE USING gist (
              venue_id WITH =,
              instrument_id WITH =,
              tstzrange(
                effective_from,
                COALESCE(effective_to, 'infinity'::timestamptz),
                '[)'
              ) WITH &&
            )
        $sql$;
    END IF;
END
$$;
