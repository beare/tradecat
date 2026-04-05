-- 006_candles_meta_views.sql
--
-- 已退役：
-- - 当前多周期 K 线与指标视图由 `assets/database/db/stacks/facts.sql` 引入的现行脚本定义
-- - 本文件对应早期一次性视图迁移过程，已不再作为 bootstrap 或运维入口
--
-- 如需旧实现，请查 git history。

DO $$
BEGIN
    RAISE NOTICE '006_candles_meta_views.sql 已退役；请使用 assets/database/db/stacks/facts.sql';
END $$;
