-- facts_data_converge_layout.sql
--
-- 已退役：
-- - 当前事实库 bootstrap：`assets/database/db/stacks/facts.sql`
-- - 当前派生库 bootstrap：`assets/database/db/stacks/derived.sql`
-- - 本文件对应早期一次性收敛过程，已不再作为重建或运维入口
--
-- 如需旧实现，请查 git history。

DO $$
BEGIN
    RAISE NOTICE 'facts_data_converge_layout.sql 已退役；请使用 assets/database/db/stacks/facts.sql';
END $$;
