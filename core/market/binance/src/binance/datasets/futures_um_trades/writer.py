"""UM trades dataset 写入契约。"""

TARGET_TABLE = "market.binance_futures_um_trades"

from binance.storage.raw_futures_um_trades import RawFuturesUmTradeRow, RawFuturesUmTradesWriter

__all__ = ["RawFuturesUmTradeRow", "RawFuturesUmTradesWriter", "TARGET_TABLE"]
