"""现货 1m K线 dataset 契约。

当前实现说明：
- 对外对象：`spot_candles_1m`
- 物理落点：`market.binance_cagg_spot_klines_1m`
- 维护方式：由 `spot_trades` 的数据库连续聚合自动维护，不单独跑 LF/HF worker
"""

DATASET_KEY = "spot_candles_1m"
RUNTIME_STATUS = "materialized_view"
SOURCE_KIND = "cagg_from_spot_trades"
TARGET_TABLE = "market.binance_cagg_spot_klines_1m"
RESOURCE_ID = "facts/market/spot_candles_1m"
LINEAGE_ACTOR_ID = "core/market/binance"

__all__ = [
    "DATASET_KEY",
    "LINEAGE_ACTOR_ID",
    "RESOURCE_ID",
    "RUNTIME_STATUS",
    "SOURCE_KIND",
    "TARGET_TABLE",
]
