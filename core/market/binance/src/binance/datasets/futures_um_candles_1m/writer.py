"""1m K线 dataset 写入契约。"""

TARGET_TABLE = "market.binance_futures_um_candles_1m"

from binance.sources.binance_api.adapters.timescale import TimescaleAdapter

__all__ = ["TARGET_TABLE", "TimescaleAdapter"]
