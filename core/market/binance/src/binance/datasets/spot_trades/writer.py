"""Spot trades dataset 写入契约。"""

TARGET_TABLE = "market.binance_spot_trades"

from binance.storage.raw_spot_trades import RawSpotTradeRow, RawSpotTradesWriter

__all__ = ["RawSpotTradeRow", "RawSpotTradesWriter", "TARGET_TABLE"]
