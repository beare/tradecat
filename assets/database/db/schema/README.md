# schema 目录说明

> 本目录混合了当前 bootstrap SQL、历史兼容脚本和一次性迁移脚本。  
> 如果你想找“当前真相入口”，不要从文件编号猜，要按分层看。

## current

当前主链 bootstrap / 运行库真相相关：

- `000_timescaledb_extension.sql`
- `001_timescaledb.sql`
- `002_taker_buy_and_gap_tracking.sql`
- `004_continuous_aggregates.sql`
- `005_metrics_5m.sql`
- `007_metrics_cagg_from_5m.sql`
- `008_market_shared.sql`
- `009_crypto_binance_vision_landing.sql`
- `012_crypto_ingest_governance.sql`
- `013_core_symbol_map_hardening.sql`
- `016_crypto_trades_readable_views.sql`
- `017_crypto_trades_cagg_klines.sql`
- `019_crypto_raw_trades_sanity_checks.sql`
- `021_indicator_snapshots_sqlite_parity.sql`
- `026_indicator_snapshots_snapshots.sql`
- `022_signal_runtime.sql`
- `024_event_service.sql`
- `025_event_unified_view.sql`
- `027_governance_lineage_events.sql`

## legacy

历史兼容命名 / 已被新命名取代：

- `021_tg_cards_sqlite_parity.sql`
- `022_signal_state.sql`
- `023_sheets_state.sql`

## oneoff

一次性迁移、补数据或局部修复脚本：

- `003_add_all_intervals.sql`
- `006_candles_meta_views.sql`
- `010_multi_market_roots_placeholders.sql`
- `011_crypto_binance_vision_derived.sql`
- `014_crypto_futures_cm_trades_ids_swap.sql`
- `015_crypto_spot_trades_fact_table.sql`
- `018_core_binance_venue_code_futures_um.sql`
- `020_crypto_futures_book_ids_swap.sql`
- `028_binance_physical_surface_table_rename.sql`
- `optional/**`

## 使用原则

- 默认只从 `current` 里挑文件进入新的 bootstrap 链路
- `legacy` 只用于对照和兼容说明
- `oneoff` 只在你明确知道自己在处理哪次迁移时执行
