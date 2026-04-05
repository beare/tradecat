-- 003_add_all_intervals.sql
--
-- 已退役：
-- - 当前事实库重建入口：`assets/database/db/stacks/facts.sql`
-- - 月线与多周期现状以当前 `market.*` 对象和连续聚合为准
-- - 本文件对应早期一次性迁移过程，保留路径仅为避免外部引用断裂
--
-- 如需旧实现，请查 git history。

DO $$
BEGIN
    RAISE NOTICE '003_add_all_intervals.sql 已退役；请使用 assets/database/db/stacks/facts.sql';
END $$;
