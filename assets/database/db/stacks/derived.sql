-- derived stack（当前派生库 bootstrap）
--
-- 适用：
-- - 初始化当前 `derived_data`
-- - 当前主用 schema：`indicator_snapshots`、`signal_runtime`
--
-- 不包含：
-- - 已归档 `indicators`
-- - 已归档 `sheets_state`

\ir ../schema/021_indicator_snapshots_sqlite_parity.sql
\ir ../schema/026_indicator_snapshots_snapshots.sql
\ir ../schema/022_signal_runtime.sql
\ir ../schema/027_governance_lineage_events.sql
