-- 020_crypto_futures_book_ids_swap.sql
--
-- 已退役：
-- - 当前盘口事实表以 `facts_data.market.binance_futures_um_book_ticker`
--   与 `facts_data.market.binance_futures_um_book_depth` 为准
-- - 本文件对应早期一次性结构迁移，不再作为现行入口
--
-- 如需旧实现，请查 git history。

DO $$
BEGIN
    RAISE NOTICE '020_crypto_futures_book_ids_swap.sql 已退役；请以当前 market.binance_futures_um_* 为准';
END $$;
