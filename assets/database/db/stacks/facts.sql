-- facts stack（当前事实库 bootstrap）
--
-- 适用：
-- - 初始化当前 `facts_data`
-- - 目标结构：`market.*` + `alternative.*`
--
-- 不包含：
-- - 历史兼容迁移
-- - 已归档 CM 链路
-- - 已归档 derived schema

\ir ../schema/000_timescaledb_extension.sql

\ir ../schema/001_timescaledb.sql
\ir ../schema/002_taker_buy_and_gap_tracking.sql
\ir ../schema/004_continuous_aggregates.sql
\ir ../schema/005_metrics_5m.sql
\ir ../schema/007_metrics_cagg_from_5m.sql
\ir ../schema/008_market_shared.sql
\ir ../schema/009_crypto_binance_vision_landing.sql
\ir ../schema/012_crypto_ingest_governance.sql
\ir ../schema/013_core_symbol_map_hardening.sql
\ir ../schema/016_crypto_trades_readable_views.sql
\ir ../schema/017_crypto_trades_cagg_klines.sql
\ir ../schema/019_crypto_raw_trades_sanity_checks.sql
\ir ../schema/024_event_service.sql
\ir ../schema/025_event_unified_view.sql
\ir ../schema/027_governance_lineage_events.sql
