-- LF stack（历史兼容入口）
--
-- 已退役：
-- - 当前事实库请执行 `stacks/facts.sql`
-- - 当前派生库请执行 `stacks/derived.sql`

\echo 'lf.sql 已退役；请改用 stacks/facts.sql 与 stacks/derived.sql'
-- 历史兼容入口：不要再作为当前 bootstrap 真相。
-- 当前默认入口见：
--   assets/database/db/stacks/facts.sql
--   assets/database/db/stacks/derived.sql
