-- 011_crypto_binance_vision_derived.sql
--
-- 已退役：
-- - 当前派生库主入口：`assets/database/db/stacks/derived.sql`
-- - 早期 Binance Vision 派生层一次性建表脚本已不再维护
--
-- 如需旧实现，请查 git history。

DO $$
BEGIN
    RAISE NOTICE '011_crypto_binance_vision_derived.sql 已退役；请使用 assets/database/db/stacks/derived.sql';
END $$;
