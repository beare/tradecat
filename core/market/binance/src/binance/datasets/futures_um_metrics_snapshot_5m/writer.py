"""5m 期货指标 dataset 写入契约。"""

TARGET_TABLE = "market.binance_futures_um_metrics_snapshot_5m"

from binance.sources.binance_api.adapters.timescale import TimescaleAdapter

__all__ = ["TARGET_TABLE", "TimescaleAdapter"]
