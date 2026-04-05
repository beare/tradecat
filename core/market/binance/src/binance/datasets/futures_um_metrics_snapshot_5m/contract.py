"""U 本位合约 5m 指标快照 dataset 契约。"""

DATASET_KEY = "futures_um_metrics_snapshot_5m"
RUNTIME_STATUS = "active"
SOURCE_KIND = "rest_poll+zip_backfill"
TARGET_TABLE = "market.binance_futures_um_metrics_snapshot_5m"
RESOURCE_ID = "facts/market/futures_um_metrics_snapshot_5m"
LINEAGE_ACTOR_ID = "core/market/binance"

from .collect import LINEAGE_RESOURCE_ID, MetricsCollector

__all__ = [
    "DATASET_KEY",
    "LINEAGE_ACTOR_ID",
    "LINEAGE_RESOURCE_ID",
    "RESOURCE_ID",
    "RUNTIME_STATUS",
    "SOURCE_KIND",
    "TARGET_TABLE",
    "MetricsCollector",
]
