"""U 本位合约 1m K线 dataset 契约。"""

DATASET_KEY = "futures_um_candles_1m"
RUNTIME_STATUS = "active"
SOURCE_KIND = "ws+rest_gap_fill"
TARGET_TABLE = "market.binance_futures_um_candles_1m"
RESOURCE_ID = "facts/market/futures_um_candles_1m"
LINEAGE_ACTOR_ID = "core/market/binance"

from .collect import LINEAGE_RESOURCE_ID, WSCollector

__all__ = [
    "DATASET_KEY",
    "LINEAGE_ACTOR_ID",
    "LINEAGE_RESOURCE_ID",
    "RESOURCE_ID",
    "RUNTIME_STATUS",
    "SOURCE_KIND",
    "TARGET_TABLE",
    "WSCollector",
]
