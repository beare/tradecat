"""UM metrics atomic dataset 契约。"""

DATASET_KEY = "futures_um_metrics_atomic"
BACKFILL_CLI = "crypto.archive.futures.um.metrics"
RUNTIME_STATUS = "backfill_only"
TARGET_TABLE = "market.binance_futures_um_metrics_atomic"

__all__ = ["BACKFILL_CLI", "DATASET_KEY", "RUNTIME_STATUS", "TARGET_TABLE"]
