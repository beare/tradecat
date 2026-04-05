-- 015_crypto_spot_trades_fact_table.sql
--
-- 已退役：
-- - Spot trades 当前以 `facts_data.market.binance_spot_trades` 为准
-- - 本文件对应早期一次性结构迁移，不再作为现行入口
--
-- 如需旧实现，请查 git history。

DO $$
BEGIN
    RAISE NOTICE '015_crypto_spot_trades_fact_table.sql 已退役；请以当前 market.binance_spot_trades 为准';
END $$;
